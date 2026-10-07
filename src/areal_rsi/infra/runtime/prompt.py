"""Reward agents that generate through AReaL v2's session-scoped proxy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from areal_rsi.infra.coordination.evaluation.assets import (
    attest_worker_inputs,
    validate_areal_import_root,
)
from areal_rsi.infra.storage.artifacts import checkpoint_digest
from areal_rsi.scripts.train import areal_root, import_symbol, load_tokenizer


class PromptRewardAgent:
    """Generate one response; v2 owns tokens, logprobs and trajectory export."""

    def __init__(
        self,
        *,
        reward_fn: str,
        gconfig: Any,
        tokenizer: Any,
        enable_thinking: bool = False,
        prompt_template: str | None = None,
        fail_on_reward_timeout: bool = False,
        reward_timeout_seconds: float = 15,
        reward_max_retries: int = 3,
    ) -> None:
        from areal.api import AsyncRewardWrapper  # type: ignore[import-not-found]

        self.tokenizer = load_tokenizer(tokenizer) if isinstance(tokenizer, str) else tokenizer
        self.gconfig = gconfig
        self.prompt_template = prompt_template
        self.chat_template_kwargs: dict[str, Any] = {"enable_thinking": enable_thinking}
        if prompt_template is not None:
            # Preserve the frozen prompt verbatim instead of adding a model chat wrapper.
            # Native concat aligns later turns at assistant EOS boundaries, including
            # length-truncated completions. The first user prompt stays unchanged.
            self.chat_template_kwargs["chat_template"] = (
                "{% for message in messages %}{{ message['content'] }}"
                "{% if message['role'] == 'assistant' %}{{ eos_token }}{% endif %}"
                "{% endfor %}"
                "{% if messages[-1]['role'] == 'assistant' %}{{ '\n' }}{% endif %}"
            )
        self.async_reward = AsyncRewardWrapper(
            import_symbol(reward_fn),
            timeout_seconds=reward_timeout_seconds,
            max_workers=2,
            max_retries=reward_max_retries,
            raise_on_timeout=fail_on_reward_timeout,
        )

    def messages(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        if self.prompt_template is None:
            return list(data["messages"])
        return [
            {
                "role": "user",
                "content": self.prompt_template.format(prompt=data["messages"][0]["content"]),
            }
        ]

    @staticmethod
    def client(extra_kwargs: dict[str, Any]) -> Any:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        base_url, api_key = extra_kwargs.get("base_url"), extra_kwargs.get("api_key")
        if not isinstance(base_url, str) or not isinstance(api_key, str) or not api_key:
            raise ValueError("AReaL v2 must provide a session-scoped generation proxy")
        return AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=extra_kwargs.get("http_client"),
            max_retries=0,
        )

    async def generate(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        *,
        stop: list[str] | None = None,
        max_total_tokens: int | None = None,
    ) -> Any:
        parameters = self.gconfig.new(n_samples=1).to_openai_completions_args_dict()
        if self.gconfig.greedy:
            parameters["temperature"] = 0.0
        if stop is not None:
            parameters["stop"] = stop
        # AReaL's proxy receives these extensions alongside OpenAI generation fields.
        parameters["extra_body"] = {
            "top_k": self.gconfig.top_k,
            "max_total_tokens": max_total_tokens or self.gconfig.max_tokens,
            "extra_body": {"chat_template_kwargs": self.chat_template_kwargs},
        }
        return await client.chat.completions.create(
            model="default", messages=messages, **parameters
        )

    async def score(
        self,
        messages: list[dict[str, Any]],
        response: str,
        data: dict[str, Any],
        **metadata: Any,
    ) -> float:
        from areal.utils.hf_utils import apply_chat_template  # type: ignore[import-not-found]

        prompt = apply_chat_template(
            self.tokenizer,
            messages,
            tokenize=False,
            add_generation_prompt=True,
            **self.chat_template_kwargs,
        )
        return float(
            await self.async_reward(
                prompt,
                response,
                self.tokenizer.encode(prompt),
                self.tokenizer.encode(response),
                **data,
                **metadata,
            )
        )

    async def run(self, data: dict[str, Any], **extra_kwargs: Any) -> float:
        messages = self.messages(data)
        completion = await self.generate(self.client(extra_kwargs), messages)
        response = completion.choices[0].message.content or ""
        return await self.score(messages, response, data)


class FrozenPromptWorkflow(PromptRewardAgent):
    def __init__(
        self,
        *,
        task_set_root: str,
        harness_root: str,
        model_root: str,
        task_set_id: str,
        harness_id: str,
        runner_digest: str,
        verifier_digest: str,
        model_digest: str,
        prompt_template: str,
        **workflow_kwargs: Any,
    ) -> None:
        runner = areal_root() / "areal"
        validate_areal_import_root(runner, runner / "__init__.py")
        attest_worker_inputs(
            Path(task_set_root),
            Path(harness_root),
            runner,
            task_set_id=task_set_id,
            harness_id=harness_id,
            runner_digest=runner_digest,
            verifier_digest=verifier_digest,
            prompt_template=prompt_template,
        )
        if checkpoint_digest(Path(model_root)) != model_digest:
            raise ValueError("Slurm worker model snapshot differs from controller")

        super().__init__(prompt_template=prompt_template, **workflow_kwargs)
