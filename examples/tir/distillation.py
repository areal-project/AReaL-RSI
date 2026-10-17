"""Generate verified, executable teacher trajectories for the fixed TIR harness.

The teacher receives questions and actual tool observations, never ground truth.
Training gold is used only by the independent admission verifier. Everything sent
to the student uses AReaL's actual prompt, tool executor and observation formatter.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from dotenv import load_dotenv

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.execution import TokenizedTrainingSample
from areal_rsi.api.records import ContentRef, TraceEvent
from areal_rsi.infra.artifacts import file_ref, local_path, read_record, write_record
from examples.tir.benchmark import Example, read_examples
from examples.tir.synthesis import normalize_gold


@dataclass(frozen=True, slots=True)
class TeacherConfig:
    base_url: str
    model: str
    api_key_env: str = "RSI_API_KEY"
    verify_tls: bool = True
    timeout_seconds: float = 120
    max_completion_tokens: int = 3072
    proxy_env: str = ""

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://") or not self.model:
            raise ValueError("Supply a teacher HTTPS endpoint and model")
        if min(self.timeout_seconds, self.max_completion_tokens) <= 0:
            raise ValueError("Teacher budgets must be positive")


@dataclass(frozen=True, slots=True)
class TeacherMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class TeacherDraft:
    reasoning: str
    code: str

    def __post_init__(self) -> None:
        if not self.reasoning.strip() or not self.code.strip():
            raise ValueError("Teacher must supply reasoning and executable code")
        if any(marker in self.reasoning + self.code for marker in ("\\boxed", "```")):
            raise ValueError("Draft cannot answer before observing the tool result")
        ast.parse(self.code)


@dataclass(frozen=True, slots=True)
class DistillationConfig:
    teacher: TeacherConfig
    training_pool: ContentRef
    excluded_ids: tuple[str, ...]
    tokenizer: str
    output: str
    count: int = 1024
    seed: int = 20261018
    concurrency: int = 8
    max_length: int = 3000
    max_segment_tokens: int = 512
    task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if min(self.count, self.concurrency, self.max_length, self.max_segment_tokens) < 1:
            raise ValueError("Distillation budgets must be positive")
        if self.task_ids and (
            len(self.task_ids) != self.count or len(set(self.task_ids)) != self.count
        ):
            raise ValueError("Explicit teacher curriculum must contain count unique tasks")


@dataclass(frozen=True, slots=True)
class DistillationAttempt:
    example: Example
    teacher: str
    events: tuple[TraceEvent, ...]
    accepted: bool
    error: str
    sample: TokenizedTrainingSample | None = None
    draft_response: str = ""


@dataclass(frozen=True, slots=True)
class DistillationManifest:
    config: ContentRef
    training_pool: ContentRef
    tokenizer_files: tuple[ContentRef, ...]
    attempts: tuple[ContentRef, ...]
    dataset: ContentRef
    attempted_count: int
    accepted_count: int


def teacher_completion(
    config: TeacherConfig, messages: tuple[TeacherMessage, ...], *, json_output: bool = False
) -> str:
    token = os.environ[config.api_key_env]
    context = (
        ssl.create_default_context() if config.verify_tls else ssl._create_unverified_context()
    )
    handlers: list[Any] = [HTTPSHandler(context=context)]
    if config.proxy_env:
        handlers.append(ProxyHandler({"https": os.environ[config.proxy_env]}))
    opener = build_opener(*handlers)
    # OpenAI-compatible transport boundary. Credentials never enter persisted requests.
    body = {
        "model": config.model,
        "messages": [encode_record(message) for message in messages],
        "max_completion_tokens": config.max_completion_tokens,
    }
    if json_output:
        body["response_format"] = {"type": "json_object"}
    request = Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    for attempt in range(3):
        try:
            with opener.open(request, timeout=config.timeout_seconds) as response:
                result = json.load(response)
            if result.get("model") != config.model:
                raise ValueError("Teacher endpoint returned a different model")
            choice = result["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("Teacher response was truncated")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Teacher returned no answer")
            return content
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Teacher HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Teacher transport failed after three attempts") from None
        time.sleep(2**attempt)
    raise AssertionError("Unreachable teacher retry state")


def tokenize_trajectory(
    task_id: str,
    events: tuple[TraceEvent, ...],
    tokenizer: Any,
    *,
    max_length: int,
    max_segment_tokens: int,
) -> TokenizedTrainingSample:
    expected = ("prompt", "generation", "generation", "tool_call", "tool_result", "generation")
    if tuple(event.kind for event in events) != expected:
        raise ValueError("Distillation requires one executed tool call and a subsequent answer")
    if not events[1].content.endswith("```python") or not events[2].content.endswith("\n```"):
        raise ValueError("Teacher generations must follow the frozen tool stop markers")
    if events[3].content != "```python" + events[2].content:
        raise ValueError("Executed tool call differs from the model's generated code")
    if "\\boxed" not in events[-1].content or "\\boxed" in "".join(
        event.content for event in events[1:-1]
    ):
        raise ValueError("Only the post-execution assistant answer may contain a boxed answer")
    if not events[4].content.startswith("\n```output\n") or not events[4].content.endswith(
        "\n```\n"
    ):
        raise ValueError("Tool observation must use the actual backend formatter")
    ids: list[int] = []
    mask: list[int] = []
    for index, event in enumerate(events):
        if event.kind == "tool_call":
            continue  # An audit event, already present in the two generation segments.
        tokens = tokenizer.encode(event.content, add_special_tokens=False)
        learned = event.kind == "generation"
        if index == len(events) - 1:
            tokens = [*tokens, tokenizer.eos_token_id]
        if learned and len(tokens) > max_segment_tokens:
            raise ValueError("Teacher segment exceeds the frozen generation token budget")
        ids.extend(tokens)
        mask.extend([int(learned)] * len(tokens))
    if len(ids) > max_length:
        raise ValueError("Teacher trajectory exceeds the frozen context token budget")
    return TokenizedTrainingSample(task_id, tuple(ids), tuple(mask))


_DRAFT_INSTRUCTION = r"""Solve the math question using one short Python program.
Return ONLY a JSON object with exactly two string fields: reasoning and code.
reasoning: a concise derivation of the equations or algorithm, at most 100 words.
Use plain text equations without LaTeX backslashes, and escape JSON correctly.
code: executable Python, at most 30 lines and 350 tokens, that computes and prints
the answer. Use exact integers, fractions or sympy when appropriate. Do not assume
the answer. Do not hard-code a guessed result or print an unexplained constant.
Use no files, network, input(), markdown fences, or boxed answers. Sympy is installed.
The code will actually run, and you will receive its stdout before answering.
"""

_ANSWER_INSTRUCTION = r"""Continue the solution after the actual Python execution below.
Use its output and the derivation to give a concise final mathematical answer.
Do not call another tool. Put exactly one final answer within \boxed{}.
If the output reveals a mistake, explain it rather than inventing successful output.
"""


def distill_one(
    config: DistillationConfig, example: Example, tokenizer: Any
) -> DistillationAttempt:
    from areal.reward import get_math_verify_worker  # type: ignore[import-not-found]
    from prompts import TORL_PROMPT  # type: ignore[import-not-found]
    from tir_workflow import TIRWorkflow  # type: ignore[import-not-found]
    from tool_manager import ToolManager  # type: ignore[import-not-found]
    from tools import ToolCallStatus  # type: ignore[import-not-found]

    events: list[TraceEvent] = [TraceEvent("prompt", TORL_PROMPT.format(prompt=example.prompt))]
    response = ""
    try:
        response = teacher_completion(
            config.teacher,
            (TeacherMessage("system", _DRAFT_INSTRUCTION), TeacherMessage("user", example.prompt)),
            json_output=True,
        )
        draft = decode_record(TeacherDraft, json.loads(response))
        events.extend(
            (
                TraceEvent("generation", draft.reasoning.strip() + "\n```python"),
                TraceEvent("generation", "\n" + draft.code.strip() + "\n```"),
                TraceEvent("tool_call", "```python\n" + draft.code.strip() + "\n```"),
            )
        )
        manager = ToolManager(timeout=30, enabled_tools="python;calculator", debug_mode=False)
        try:
            result, status = manager.execute_tool_call(events[-1].content)
        finally:
            manager.cleanup()
        if status != ToolCallStatus.SUCCESS or not result.strip():
            raise ValueError("Teacher program failed or printed no result")
        observation = TIRWorkflow._process_tool_result(result)
        events.append(TraceEvent("tool_result", observation))
        answer = teacher_completion(
            config.teacher,
            (
                TeacherMessage("system", _ANSWER_INSTRUCTION),
                TeacherMessage("user", example.prompt),
                TeacherMessage("assistant", draft.reasoning + "\n" + events[3].content),
                TeacherMessage("user", observation),
            ),
        )
        events.append(TraceEvent("generation", "\n" + answer.strip()))
        sample = tokenize_trajectory(
            example.task_id,
            tuple(events),
            tokenizer,
            max_length=config.max_length,
            max_segment_tokens=config.max_segment_tokens,
        )
        # Verify assistant text alone; tool stdout cannot earn admission credit.
        gold = normalize_gold(example.answer, "math_delimited")
        if get_math_verify_worker().verify(answer, gold) != 1:
            raise ValueError("Post-execution teacher answer failed independent gold verification")
        return DistillationAttempt(
            example, config.teacher.model, tuple(events), True, "", sample, response
        )
    except Exception as exc:
        error = str(exc).replace(os.environ[config.teacher.api_key_env], "[REDACTED]")
        return DistillationAttempt(
            example, config.teacher.model, tuple(events), False, error, draft_response=response
        )


def generate(config: DistillationConfig) -> DistillationManifest:
    from areal.reward import get_math_verify_worker
    from transformers import AutoTokenizer  # type: ignore[import-not-found]

    pool = read_examples(local_path(config.training_pool))
    if any(item.source != "torl-train" or item.repetitions != 1 for item in pool):
        raise ValueError("Teacher questions must come from the official, unique training pool")
    excluded = set(config.excluded_ids)
    if excluded.intersection(item.task_id for item in pool):
        raise ValueError("Teacher training pool overlaps held-out identities")
    by_id = {item.task_id: item for item in pool}
    if config.task_ids:
        if not set(config.task_ids) <= by_id.keys():
            raise ValueError("Teacher curriculum contains a non-training identity")
        selected = tuple(by_id[task_id] for task_id in config.task_ids)
    else:
        selected = tuple(random.Random(config.seed).sample(pool, config.count))
    output = Path(config.output)
    output.mkdir(parents=True, exist_ok=False)
    config_ref = write_record(output / "config.json", config)
    tokenizer = AutoTokenizer.from_pretrained(config.tokenizer, local_files_only=True)
    tokenizer_refs = tuple(
        file_ref(path)
        for path in sorted(Path(config.tokenizer).iterdir())
        if path.name in {"tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt"}
    )
    if not tokenizer_refs or tokenizer.eos_token_id is None:
        raise ValueError("A pinned local tokenizer with an EOS token is required")
    get_math_verify_worker()  # Initialize optional parser dependencies before concurrent work.
    attempts: dict[str, DistillationAttempt] = {}
    refs: dict[str, ContentRef] = {}
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures = {executor.submit(distill_one, config, item, tokenizer): item for item in selected}
        for future in as_completed(futures):
            attempt = future.result()
            attempts[attempt.example.task_id] = attempt
            refs[attempt.example.task_id] = write_record(
                output / "attempts" / f"{attempt.example.task_id}.json", attempt
            )
            accepted = sum(item.accepted for item in attempts.values())
            print(f"teacher {len(attempts)}/{len(selected)} accepted={accepted}", flush=True)
    accepted_samples = [
        attempts[item.task_id].sample for item in selected if attempts[item.task_id].accepted
    ]
    if not accepted_samples or any(sample is None for sample in accepted_samples):
        raise ValueError("Teacher produced no verified, executable training trajectories")
    path = output / "train.jsonl"
    with path.open("x") as stream:
        for sample in accepted_samples:
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
    manifest = DistillationManifest(
        config_ref,
        config.training_pool,
        tokenizer_refs,
        tuple(refs[item.task_id] for item in selected),
        file_ref(path),
        len(selected),
        len(accepted_samples),
    )
    write_record(output / "manifest.json", manifest)
    return manifest


def materialize_for_student(
    manifest_path: Path,
    tokenizer: Any,
    output: Path,
    *,
    max_length: int,
    max_segment_tokens: int,
) -> ContentRef:
    """Replay verified traces with the actual warm-start checkpoint tokenizer.

    HF saves can change tokenizer JSON/configuration bytes. Replaying recorded
    segments avoids assuming that teacher preparation and student tokenization
    are identical, and preserves tool observations as conditioning only.
    """
    from prompts import TORL_PROMPT

    manifest = read_record(manifest_path, DistillationManifest)
    samples = []
    for ref in manifest.attempts:
        attempt = read_record(local_path(ref), DistillationAttempt)
        if not attempt.accepted:
            continue
        if (
            attempt.error
            or attempt.sample is None
            or not attempt.events
            or attempt.events[0].content != TORL_PROMPT.format(prompt=attempt.example.prompt)
        ):
            raise ValueError("Accepted distillation trace differs from the fixed TIR prompt")
        samples.append(
            tokenize_trajectory(
                attempt.example.task_id,
                attempt.events,
                tokenizer,
                max_length=max_length,
                max_segment_tokens=max_segment_tokens,
            )
        )
    if len(samples) != manifest.accepted_count:
        raise ValueError("Student target count differs from verified teacher admission")
    with output.open("x") as stream:
        for sample in samples:
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
    return file_ref(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    manifest = generate(read_record(args.config, DistillationConfig))
    print(f"verified_teacher_trajectories={manifest.accepted_count}", flush=True)


if __name__ == "__main__":
    main()
