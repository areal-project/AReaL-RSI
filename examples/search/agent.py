"""Run the original synchronous Qwen-Agent harness through AReaL's proxy."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import os
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from examples.search.reward import answer_reward

_mcp_lock = threading.Lock()
_mcp_tools: dict[str, tuple[Any, ...]] = {}
_template_lock = threading.Lock()
_tokenizers: dict[str, Any] = {}


def prompt_tokens(path: str, messages: list[dict[str, Any]]) -> list[int]:
    from transformers import AutoTokenizer

    with _template_lock:
        if path not in _tokenizers:
            _tokenizers[path] = AutoTokenizer.from_pretrained(path, local_files_only=True)
        expected = _tokenizers[path].apply_chat_template(
            messages,
            tokenize=True,
            return_dict=False,
            add_generation_prompt=True,
            enable_thinking=True,
        )
    return list(expected)


def token_digest(tokens: list[int]) -> str:
    return hashlib.sha256(json.dumps(tokens).encode()).hexdigest()


class SearchInfrastructureError(RuntimeError):
    """A failed tool service must not become an incorrect-answer training example."""


@dataclass(frozen=True, slots=True)
class ModelCall:
    input_tokens: int
    input_sha256: str
    messages: tuple[dict[str, Any], ...]  # Opaque outgoing OpenAI message boundary.


@dataclass(frozen=True, slots=True)
class Episode:
    question_id: str
    sample_index: int | None
    reward: float
    final_answer: str
    search_calls: int
    retrieved_docids: tuple[str, ...]
    elapsed_seconds: float
    messages: tuple[dict[str, Any], ...]  # Opaque Qwen-Agent message boundary.
    model_calls: tuple[ModelCall, ...]
    failure_type: str = ""


class EpisodeRunError(RuntimeError):
    def __init__(self, episode: Episode, cause: Exception):
        super().__init__(type(cause).__name__)
        self.episode = episode
        self.cause = cause


def load_prompt(repository: Path, question: str) -> str:
    path = repository / "search_agent" / "prompts.py"
    spec = importlib.util.spec_from_file_location("bcp_search_prompts", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot import official prompt from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.format_query(question, "QUERY_TEMPLATE_NO_GET_DOCUMENT"))


def tools_for(url: str, timeout: float) -> tuple[Any, ...]:
    # Qwen-Agent's MCP manager is a process singleton with a background loop.
    # Serialize initialization and reuse tool clients, never mutable agent state.
    from qwen_agent.tools.mcp_manager import MCPManager

    with _mcp_lock:
        if url not in _mcp_tools:
            manager = MCPManager()
            tools = manager.initConfig({"mcpServers": {"search-server": {"url": url}}})
            if len(tools) != 1 or tools[0].name != "search-server-search":
                raise ValueError("The frozen baseline exposes exactly one MCP search tool")
            for tool in tools:
                client_id = tool.client_id

                def bounded_call(params: str, *, _client_id: str = client_id, **kwargs: Any) -> str:
                    arguments = json.loads(params)
                    client = manager.clients[_client_id]
                    future = asyncio.run_coroutine_threadsafe(
                        client.execute_function("search", arguments), manager.loop
                    )
                    try:
                        return str(future.result(timeout=timeout))
                    except BaseException:
                        future.cancel()
                        raise

                tool.call = bounded_call
            _mcp_tools[url] = tuple(tools)
        return _mcp_tools[url]


class BrowseCompTrainAgent:
    def __init__(
        self,
        benchmark_repository: str,
        mcp_url: str,
        trace_directory: str,
        tokenizer_path: str,
        max_completion_tokens: int = 10000,
        request_timeout: float = 180.0,
        tool_timeout: float = 60.0,
        evaluation_seed: int | None = None,
    ):
        self.repository = Path(benchmark_repository)
        self.mcp_url = mcp_url
        self.trace_directory = Path(trace_directory)
        self.tokenizer_path = tokenizer_path
        self.max_completion_tokens = max_completion_tokens
        self.request_timeout = request_timeout
        self.tool_timeout = tool_timeout
        self.evaluation_seed = evaluation_seed

    def _run_sync(
        self, data: dict[str, Any], base_url: str, api_key: str, sample_index: int | None
    ) -> Episode:
        if os.environ.get("QWEN_AGENT_MAX_LLM_CALL_PER_RUN") != "50":
            raise ValueError("Set QWEN_AGENT_MAX_LLM_CALL_PER_RUN=50 before importing Qwen-Agent")
        if os.environ.get("QWEN_AGENT_DEFAULT_MAX_INPUT_TOKENS") != "115000":
            raise ValueError(
                "Set QWEN_AGENT_DEFAULT_MAX_INPUT_TOKENS=115000 before importing Qwen-Agent"
            )
        from qwen_agent.agents import Assistant
        from qwen_agent.settings import DEFAULT_MAX_INPUT_TOKENS, MAX_LLM_CALL_PER_RUN

        if (MAX_LLM_CALL_PER_RUN, DEFAULT_MAX_INPUT_TOKENS) != (50, 115000):
            raise ValueError("Qwen-Agent was imported before the frozen harness limits were set")

        class CheckedAssistant(Assistant):
            def _call_tool(self, tool_name: str, tool_args: str = "{}", **kwargs: Any) -> str:
                result = str(super()._call_tool(tool_name, tool_args, **kwargs))
                if tool_name != "search-server-search":
                    return result
                try:
                    arguments = json.loads(tool_args)
                except (TypeError, ValueError):
                    return result  # Model-generated invalid JSON remains a normal observation.
                if not isinstance(arguments, dict) or not isinstance(arguments.get("query"), str):
                    return result
                try:
                    records = json.loads(result)
                except ValueError as exc:
                    raise SearchInfrastructureError(
                        "Search returned a non-JSON tool result"
                    ) from exc
                if not isinstance(records, list) or any(
                    not isinstance(item, dict) or "docid" not in item or "snippet" not in item
                    for item in records
                ):
                    raise SearchInfrastructureError("Search returned an invalid result schema")
                return result

        started = time.monotonic()
        agent = CheckedAssistant(
            llm={
                "model": "Qwen/Qwen3-8B",
                "model_server": base_url,
                "api_key": api_key,
                "generate_cfg": {
                    "max_tokens": self.max_completion_tokens,
                    "top_p": 0.8,
                    # The historical vLLM server inherited top_k=20 from the
                    # model generation_config.json; the proxy needs it explicit.
                    "top_k": 20,
                    "temperature": 0.7,
                    "request_timeout": self.request_timeout,
                    "max_retries": 0,
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
                },
            },
            function_list=list(tools_for(self.mcp_url, self.tool_timeout)),
        )
        model_calls: list[ModelCall] = []
        create_completion = agent.llm._chat_complete_create

        def capture_completion(*args: Any, **kwargs: Any) -> Any:
            # Observe the actual textual function prompt after Qwen conversion.
            # Forward the request unchanged; never retain credentials or headers.
            messages = json.loads(json.dumps(kwargs["messages"]))
            if any(not isinstance(message.get("content"), str) for message in messages):
                raise ValueError("Expected the frozen Qwen textual message format")
            tokens = prompt_tokens(self.tokenizer_path, messages)
            model_calls.append(ModelCall(len(tokens), token_digest(tokens), tuple(messages)))
            return create_completion(*args, **kwargs)

        agent.llm._chat_complete_create = capture_completion
        prompt = load_prompt(self.repository, str(data["question"]))
        result: list[dict[str, Any]] = []
        options = {"seed": self.evaluation_seed} if self.evaluation_seed is not None else {}
        failure: Exception | None = None
        try:
            for chunk in agent.run(messages=[{"role": "user", "content": prompt}], **options):
                result = chunk
        except Exception as exc:
            failure = exc
        final = ""
        retrieved: set[str] = set()
        calls = 0
        for message in result:
            if message.get("role") == "assistant" and not message.get("function_call"):
                final = str(message.get("content", ""))
            if message.get("role") == "assistant" and message.get("function_call"):
                calls += 1
            if message.get("role") == "function":
                try:
                    documents = json.loads(str(message.get("content", "")))
                except ValueError:
                    continue  # Malformed model arguments can produce a normal error observation.
                if isinstance(documents, list):
                    retrieved.update(str(item["docid"]) for item in documents if "docid" in item)
        episode = Episode(
            str(data["question_id"]),
            sample_index,
            answer_reward(final, data["answers"]) if failure is None else 0.0,
            final,
            calls,
            tuple(sorted(retrieved)),
            time.monotonic() - started,
            tuple(result),
            tuple(model_calls),
            type(failure).__name__ if failure is not None else "",
        )
        if failure is not None:
            raise EpisodeRunError(episode, failure) from failure
        return episode

    def _path(self, data: dict[str, Any]) -> Path:
        from areal import workflow_context

        context = workflow_context.get()
        key = f"{context.task_id}:{context.sample_idx}:{data['question_id']}"
        return self.trace_directory / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    async def run(self, data: dict[str, Any], **extra_kwargs: Any) -> float:
        import aiofiles
        from areal import workflow_context

        base_url = extra_kwargs.get("base_url") or os.environ["OPENAI_BASE_URL"]
        api_key = extra_kwargs.get("api_key") or os.environ["OPENAI_API_KEY"]
        task = asyncio.create_task(
            asyncio.to_thread(
                self._run_sync, data, base_url, api_key, workflow_context.get().sample_idx
            )
        )
        failure: EpisodeRunError | None = None
        try:
            episode = await asyncio.shield(task)
        except EpisodeRunError as exc:
            episode = exc.episode
            failure = exc
        except asyncio.CancelledError:
            # A cancelled thread keeps making HTTP requests. Drain its bounded calls
            # before allowing AReaL to release the associated proxy session.
            try:
                await task
            finally:
                raise
        await asyncio.to_thread(self.trace_directory.mkdir, parents=True, exist_ok=True)
        async with aiofiles.open(self._path(data), "x") as output:
            await output.write(json.dumps(asdict(episode), ensure_ascii=False) + "\n")
        if failure is not None:
            raise failure.cause from failure
        # Set only the final completion's reward. AReaL handles backward
        # propagation and logical rollout grouping across the individual turns.
        return float(episode.reward)

    @staticmethod
    def classify_proxy_failure(error: Exception, **kwargs: Any) -> str:
        if isinstance(error, SearchInfrastructureError):
            return "system_failure_reject"
        return "unknown_failure_reject"

    async def audit_interactions(self, data: dict[str, Any], interactions: Any) -> None:
        import aiofiles
        from areal.infra.workflow_executor import WorkflowContractError

        from areal_rsi.infra.artifacts import read_record

        if not interactions:
            raise WorkflowContractError("No trainable model interactions were exported")
        episode = await asyncio.to_thread(read_record, self._path(data), Episode)
        expected = Counter((call.input_tokens, call.input_sha256) for call in episode.model_calls)
        audits = []
        for interaction in interactions.values():
            if interaction.reward != episode.reward:
                raise WorkflowContractError(
                    "Export reward differs from the recorded episode outcome"
                )
            # HTTP export intentionally transports cached tensors without a
            # ModelResponse or messages. Audit the tensors training will consume.
            tensor = interaction.to_tensor_dict()
            keys = ("input_ids", "loss_mask", "logprobs", "versions", "attention_mask")
            if not all(key in tensor for key in keys):
                raise WorkflowContractError("Export is missing required rollout tensors")
            shapes = {tuple(tensor[key].shape) for key in keys}
            if (
                len(shapes) != 1
                or len(tensor["input_ids"].shape) != 2
                or tensor["input_ids"].shape[0] != 1
            ):
                raise WorkflowContractError("Missing or misaligned rollout tensors")
            active = tensor["attention_mask"].squeeze(0).bool()
            if not bool(active.all()):
                raise WorkflowContractError(
                    "Individual proxy exports must contain no masked padding"
                )
            if (
                "rewards" not in tensor
                or tensor["rewards"].numel() != 1
                or float(tensor["rewards"].item()) != interaction.reward
            ):
                raise WorkflowContractError("Cached training reward differs from exported reward")
            ids, mask, logprobs, versions = (
                tensor[key].squeeze(0)[active].tolist() for key in keys[:-1]
            )
            if 1 not in mask:
                raise WorkflowContractError("Export has no generated tokens")
            input_len = mask.index(1)
            if mask != [0] * input_len + [1] * (len(mask) - input_len):
                raise WorkflowContractError(
                    "Prompt/tool tokens must be masked; output must be trained"
                )
            if any(value != 0 for value in logprobs[:input_len]) or any(
                value != -1 for value in versions[:input_len]
            ):
                raise WorkflowContractError("Prompt tokens contain policy metadata")
            if any(not math.isfinite(value) for value in logprobs[input_len:]):
                raise WorkflowContractError("Non-finite rollout log probabilities")
            if any(version < 0 for version in versions[input_len:]):
                raise WorkflowContractError("Generated tokens lack a valid policy version")
            signature = (input_len, token_digest(ids[:input_len]))
            if expected[signature] < 1:
                path = self._path(data).with_suffix(".mismatch.json")
                async with aiofiles.open(path, "x") as output:
                    await output.write(
                        json.dumps(
                            {
                                "input_ids": ids[:input_len],
                                "expected_calls": [asdict(call) for call in episode.model_calls],
                            },
                            indent=2,
                        )
                        + "\n"
                    )
                raise WorkflowContractError("Proxy prompt differs from the frozen HF chat template")
            expected[signature] -= 1
            audits.append(
                {
                    "completion_id": interaction.interaction_id,
                    "input_tokens": input_len,
                    "output_tokens": len(ids) - input_len,
                    "input_sha256": signature[1],
                    "output_sha256": token_digest(ids[input_len:]),
                    "versions": sorted(set(versions[input_len:])),
                    "reward": interaction.reward,
                    "rollout_reward": interaction.rollout_reward,
                    "logprob_min": min(logprobs[input_len:]),
                    "logprob_max": max(logprobs[input_len:]),
                    "mask_validated": True,
                    "hf_template_validated": True,
                    "tensor_shape": list(tensor["input_ids"].shape),
                    "tensor_dtypes": {key: str(tensor[key].dtype) for key in keys},
                    "tensor_data": {
                        "input_ids": ids,
                        "loss_mask": mask,
                        "logprobs": logprobs,
                        "versions": versions,
                        "attention_mask": tensor["attention_mask"].squeeze(0).tolist(),
                    },
                }
            )
        references = {
            item.rollout_reward for item in interactions.values() if item.rollout_reward is not None
        }
        if references != {episode.reward}:
            raise WorkflowContractError("Logical rollout reference differs from episode outcome")
        if any(expected.values()) and not episode.failure_type:
            raise WorkflowContractError("Captured model requests are missing from the proxy export")
        path = self._path(data).with_suffix(".audit.json")
        async with aiofiles.open(path, "x") as output:
            await output.write(
                json.dumps({"schema_version": 2, "interactions": audits}, indent=2) + "\n"
            )
