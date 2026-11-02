"""One proxy generation followed by Arena response-upload evaluation."""

from codev import RewardClient
from openai import AsyncOpenAI


def response_text(message):
    content = message.content or ""
    reasoning = getattr(message, "reasoning_content", None)
    return "<think>\n" + reasoning + "</think>\n" + content if reasoning else content


class CodeVAgent:
    def __init__(self, codev, gen_args):
        self.config, self.gen_args = codev, gen_args
        self.rewards = RewardClient(codev)

    async def run(self, data, *, base_url, api_key, http_client, session_id, **kwargs):
        from areal.infra.workflow_executor import WorkflowContractError

        try:
            client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                http_client=http_client,
                max_retries=0,
                timeout=self.config["generation_timeout"],
            )
            completion = await client.chat.completions.create(
                model="default",
                messages=data["messages"],
                n=1,
                stream=False,
                extra_body={
                    "top_k": self.gen_args["top_k"],
                    "chat_template_kwargs": {"enable_thinking": self.config["enable_thinking"]},
                },
                **{k: v for k, v in self.gen_args.items() if k != "top_k"},
            )
            if len(completion.choices) != 1 or completion.choices[0].finish_reason not in (
                "stop",
                "length",
            ):
                raise RuntimeError("Single-turn proxy generation did not finish normally")
            return await self.rewards.evaluate(
                data, response_text(completion.choices[0].message), session_id
            )
        except Exception as exc:
            raise WorkflowContractError(f"CodeV Agent failed: {type(exc).__name__}: {exc}") from exc
