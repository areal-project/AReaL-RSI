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
import re
import ssl
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from http.client import HTTPException
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
        tree = ast.parse(self.code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in {"exit", "quit", "breakpoint"})
                or (isinstance(node.func, ast.Attribute) and node.func.attr in {"exit", "_exit"})
            ):
                raise ValueError("Teacher code cannot terminate its execution service")


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


@dataclass(frozen=True, slots=True)
class CohortPublication:
    collection_config: ContentRef
    prefix_task_ids: tuple[str, ...]
    manifest: ContentRef


def _questions(config: DistillationConfig) -> tuple[Example, ...]:
    pool = read_examples(local_path(config.training_pool))
    if any(item.source != "torl-train" or item.repetitions != 1 for item in pool):
        raise ValueError("Teacher questions must come from the official, unique training pool")
    if set(config.excluded_ids).intersection(item.task_id for item in pool):
        raise ValueError("Teacher training pool overlaps held-out identities")
    by_id = {item.task_id: item for item in pool}
    if config.task_ids:
        if not set(config.task_ids) <= by_id.keys():
            raise ValueError("Teacher curriculum contains a non-training identity")
        return tuple(by_id[task_id] for task_id in config.task_ids)
    return tuple(random.Random(config.seed).sample(pool, config.count))


def _tokenizer_files(config: DistillationConfig) -> tuple[ContentRef, ...]:
    refs = tuple(
        file_ref(path)
        for path in sorted(Path(config.tokenizer).iterdir())
        if path.name in {"tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt"}
    )
    if not refs:
        raise ValueError("A pinned local tokenizer is required")
    return refs


def _publish(
    config_ref: ContentRef,
    questions: tuple[Example, ...],
    tokenizer_refs: tuple[ContentRef, ...],
    attempt_refs: tuple[ContentRef, ...],
    output: Path,
) -> DistillationManifest:
    config = read_record(local_path(config_ref), DistillationConfig)
    attempts = tuple(read_record(local_path(ref), DistillationAttempt) for ref in attempt_refs)
    if tuple(attempt.example for attempt in attempts) != questions:
        raise ValueError("Teacher publication does not match the planned question identities")
    accepted = []
    for attempt in attempts:
        if attempt.teacher != config.teacher.model:
            raise ValueError("Teacher publication mixes unplanned teacher models")
        if attempt.accepted:
            if (
                attempt.error
                or attempt.sample is None
                or attempt.sample.task_id != attempt.example.task_id
            ):
                raise ValueError("Accepted teacher record has inconsistent training targets")
            accepted.append(attempt.sample)
    if not accepted:
        raise ValueError("Teacher produced no verified, executable training trajectories")
    path = output / "train.jsonl"
    with path.open("x") as stream:
        for sample in accepted:
            stream.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
    manifest = DistillationManifest(
        config_ref,
        config.training_pool,
        tokenizer_refs,
        attempt_refs,
        file_ref(path),
        len(attempts),
        len(accepted),
    )
    write_record(output / "manifest.json", manifest)
    return manifest


def publish_prefix(config_path: Path, count: int, output: Path) -> DistillationManifest:
    """Publish an immutable predeclared question prefix from an active collection.

    Every question in the prefix must have a closed attempt, including failures.
    This never chooses the first completed or first successful trajectories.
    Original producer records and its full collection configuration stay intact.
    """
    config = read_record(config_path, DistillationConfig)
    if not 1 <= count <= config.count:
        raise ValueError("Teacher cohort must be a positive prefix of its collection")
    selected = _questions(config)[:count]
    paths = tuple(Path(config.output) / "attempts" / f"{item.task_id}.json" for item in selected)
    if any(not path.is_file() for path in paths):
        raise ValueError("The planned teacher prefix is not completely processed")
    refs = tuple(file_ref(path) for path in paths)
    output.mkdir(parents=True, exist_ok=False)
    config_ref = file_ref(config_path)
    manifest = _publish(config_ref, selected, _tokenizer_files(config), refs, output)
    write_record(
        output / "cohort.json",
        CohortPublication(
            config_ref, tuple(item.task_id for item in selected), file_ref(output / "manifest.json")
        ),
    )
    return manifest


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
        except (URLError, TimeoutError, HTTPException):
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
    boxed_answer(events[-1].content)
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


@dataclass(slots=True)
class PendingTeacher:
    example: Example
    stage: Literal["draft", "answer"]
    events: list[TraceEvent]
    draft_response: str = ""


@dataclass(slots=True)
class CollectionState:
    status: Literal["running", "completed", "failed"] = "running"
    completed: int = 0
    accepted: int = 0
    error: str = ""


def boxed_answer(text: str) -> str:
    starts = list(re.finditer(r"\\boxed\s*\{", text))
    if len(starts) != 1:
        raise ValueError("Teacher final answer must contain exactly one boxed expression")
    start = starts[0]
    depth = 1
    for index in range(start.end(), len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            if not text[start.end() : index].strip():
                break
            return text[start.start() : index + 1]
    raise ValueError("Teacher boxed answer is empty or incomplete")


def execute_draft(pending: PendingTeacher, response: str) -> tuple[TeacherMessage, ...]:
    from tir_workflow import TIRWorkflow  # type: ignore[import-not-found]
    from tool_manager import ToolManager  # type: ignore[import-not-found]
    from tools import ToolCallStatus  # type: ignore[import-not-found]

    pending.draft_response = response
    draft = decode_record(TeacherDraft, json.loads(response))
    pending.events.extend(
        (
            TraceEvent("generation", draft.reasoning.strip() + "\n```python"),
            TraceEvent("generation", "\n" + draft.code.strip() + "\n```"),
            TraceEvent("tool_call", "```python\n" + draft.code.strip() + "\n```"),
        )
    )
    manager = ToolManager(timeout=30, enabled_tools="python;calculator", debug_mode=False)
    try:
        result, status = manager.execute_tool_call(pending.events[-1].content)
    finally:
        manager.cleanup()
    observation = TIRWorkflow._process_tool_result(result)
    pending.events.append(TraceEvent("tool_result", observation))
    if (
        status != ToolCallStatus.SUCCESS
        or not result.strip()
        or observation.startswith("\n```output\nError:")
    ):
        raise ValueError("Teacher program failed or produced an invalid observation")
    return (
        TeacherMessage("system", _ANSWER_INSTRUCTION),
        TeacherMessage("user", pending.example.prompt),
        TeacherMessage("assistant", draft.reasoning + "\n" + pending.events[3].content),
        TeacherMessage("user", observation),
    )


def admit_answer(
    config: DistillationConfig, pending: PendingTeacher, answer: str, tokenizer: Any
) -> DistillationAttempt:
    from math_verify import parse, verify  # type: ignore[import-not-found]
    from math_verify.parser import (  # type: ignore[import-not-found]
        ExprExtractionConfig,
        LatexExtractionConfig,
    )

    pending.events.append(TraceEvent("generation", "\n" + answer.strip()))
    sample = tokenize_trajectory(
        pending.example.task_id,
        tuple(pending.events),
        tokenizer,
        max_length=config.max_length,
        max_segment_tokens=config.max_segment_tokens,
    )
    targets = (ExprExtractionConfig(), LatexExtractionConfig())
    gold = parse(
        normalize_gold(pending.example.answer, "math_delimited"),
        extraction_config=targets,
        parsing_timeout=3,
    )
    prediction = parse(boxed_answer(answer), extraction_config=targets, parsing_timeout=3)
    if not gold or not prediction or not verify(gold, prediction, timeout_seconds=3):
        raise ValueError("Post-execution teacher answer failed independent gold verification")
    return DistillationAttempt(
        pending.example,
        config.teacher.model,
        tuple(pending.events),
        True,
        "",
        sample,
        pending.draft_response,
    )


def generate(config: DistillationConfig) -> DistillationManifest:
    from transformers import AutoTokenizer  # type: ignore[import-not-found]

    selected = _questions(config)
    output = Path(config.output)
    output.mkdir(parents=True, exist_ok=False)
    config_ref = write_record(output / "config.json", config)
    tokenizer = AutoTokenizer.from_pretrained(config.tokenizer, local_files_only=True)
    tokenizer_refs = _tokenizer_files(config)
    if tokenizer.eos_token_id is None:
        raise ValueError("A local tokenizer with an EOS token is required")
    # Only HTTP requests run in threads. The backend Python executor itself
    # creates processes; execute it in the coordinator, never in HTTP threads.
    # Admit/parse in the main thread so MathVerify's signal timeouts actually work.
    from prompts import TORL_PROMPT  # type: ignore[import-not-found]

    refs: dict[str, ContentRef] = {}
    state = CollectionState()
    write_record(output / "state.json", state)
    executor = ThreadPoolExecutor(max_workers=config.concurrency)
    pending: dict[Future[str], PendingTeacher] = {}
    remaining = iter(selected)

    def submit_question() -> None:
        example = next(remaining, None)
        if example is None:
            return
        context = PendingTeacher(
            example, "draft", [TraceEvent("prompt", TORL_PROMPT.format(prompt=example.prompt))]
        )
        future = executor.submit(
            teacher_completion,
            config.teacher,
            (
                TeacherMessage("system", _DRAFT_INSTRUCTION),
                TeacherMessage("user", example.prompt),
            ),
            json_output=True,
        )
        pending[future] = context

    try:
        for _ in range(min(len(selected), config.concurrency)):
            submit_question()
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                context = pending.pop(future)
                try:
                    response = future.result()
                    if context.stage == "draft":
                        messages = execute_draft(context, response)
                        context.stage = "answer"
                        pending[executor.submit(teacher_completion, config.teacher, messages)] = (
                            context
                        )
                        continue
                    attempt = admit_answer(config, context, response, tokenizer)
                except (Exception, SystemExit) as exc:
                    error = str(exc).replace(os.environ[config.teacher.api_key_env], "[REDACTED]")
                    attempt = DistillationAttempt(
                        context.example,
                        config.teacher.model,
                        tuple(context.events),
                        False,
                        type(exc).__name__ + ": " + error,
                        draft_response=context.draft_response,
                    )
                refs[attempt.example.task_id] = write_record(
                    output / "attempts" / f"{attempt.example.task_id}.json", attempt
                )
                state.completed += 1
                state.accepted += attempt.accepted
                write_record(output / "state.json", state)
                print(
                    f"teacher {state.completed}/{len(selected)} accepted={state.accepted}",
                    flush=True,
                )
                submit_question()
    except BaseException as exc:
        state.status = "failed"
        state.error = type(exc).__name__ + ": " + str(exc)
        state.error = state.error.replace(os.environ[config.teacher.api_key_env], "[REDACTED]")
        write_record(output / "state.json", state)
        diagnostic = traceback.format_exc().replace(
            os.environ[config.teacher.api_key_env], "[REDACTED]"
        )
        (output / "failure.log").write_text(diagnostic)
        print(diagnostic, flush=True)
        raise
    finally:
        # At most concurrency requests can remain; no hidden queue of hundreds
        # of paid requests continues after the collector has already failed.
        executor.shutdown(wait=True, cancel_futures=True)
    try:
        manifest = _publish(
            config_ref,
            selected,
            tokenizer_refs,
            tuple(refs[item.task_id] for item in selected),
            output,
        )
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc).replace(os.environ[config.teacher.api_key_env], "[REDACTED]")
        write_record(output / "state.json", state)
        raise
    state.status = "completed"
    write_record(output / "state.json", state)
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
    parser.add_argument(
        "--prefix-count", type=int, help="Publish a completely processed question prefix"
    )
    parser.add_argument("--publish-output", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    if (args.prefix_count is None) != (args.publish_output is None):
        parser.error("--prefix-count and --publish-output must be supplied together")
    manifest = (
        publish_prefix(args.config, args.prefix_count, args.publish_output)
        if args.prefix_count is not None
        else generate(read_record(args.config, DistillationConfig))
    )
    print(f"verified_teacher_trajectories={manifest.accepted_count}", flush=True)


if __name__ == "__main__":
    main()
