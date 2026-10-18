"""Run the original synchronous Qwen-Agent harness through AReaL's proxy."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from examples.search.reward import answer_reward
from examples.search.rollouts import Episode

_mcp_lock = threading.Lock()
_mcp_tools: dict[str, tuple[Any, ...]] = {}


class SearchInfrastructureError(RuntimeError):
    """A failed tool service must not become an incorrect-answer training example."""


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
        max_completion_tokens: int = 10000,
        request_timeout: float = 180.0,
        tool_timeout: float = 60.0,
        evaluation_seed: int | None = None,
    ):
        self.repository = Path(benchmark_repository)
        self.mcp_url = mcp_url
        self.trace_directory = Path(trace_directory)
        self.max_completion_tokens = max_completion_tokens
        self.request_timeout = request_timeout
        self.tool_timeout = tool_timeout
        self.evaluation_seed = evaluation_seed

    def _run_sync(
        self,
        data: dict[str, Any],
        base_url: str,
        api_key: str,
        task_id: int,
        sample_index: int | None,
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
            task_id,
            sample_index,
            answer_reward(final, data["answers"]) if failure is None else 0.0,
            final,
            calls,
            tuple(sorted(retrieved)),
            time.monotonic() - started,
            tuple(result),
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
        from areal import workflow_context

        context = workflow_context.get()
        if context.task_id is None:
            raise ValueError("Search episodes require an AReaL task identity")
        base_url = extra_kwargs.get("base_url") or os.environ["OPENAI_BASE_URL"]
        api_key = extra_kwargs.get("api_key") or os.environ["OPENAI_API_KEY"]
        task = asyncio.create_task(
            asyncio.to_thread(
                self._run_sync, data, base_url, api_key, context.task_id, context.sample_idx
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

        def save_episode() -> None:
            self.trace_directory.mkdir(parents=True, exist_ok=True)
            with self._path(data).open("x") as output:
                output.write(json.dumps(asdict(episode), ensure_ascii=False) + "\n")

        await asyncio.to_thread(save_episode)
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
