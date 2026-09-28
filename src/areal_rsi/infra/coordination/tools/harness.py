"""One bounded LLM proposal from public TIR failures."""

from __future__ import annotations

import json
import os
from typing import BinaryIO, cast
from urllib.request import HTTPRedirectHandler, Request, build_opener

from areal_rsi.api.records import RsiEvaluationReport, RsiSplit, Task
from areal_rsi.api.research import ResearcherSpec

SYSTEM_PROMPT = (
    "You improve one reusable prompt for a math agent with a calculator tool. "
    "Return only a complete prompt template containing exactly one literal {prompt} field. "
    "Do not include specific questions, answers, task IDs, or benchmark examples. "
    "Keep the calculator syntax and a boxed final answer."
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        del req, fp, code, msg, headers, newurl
        raise ValueError("Researcher endpoint redirected unexpectedly")


def _open(request: Request) -> BinaryIO:
    return cast(BinaryIO, build_opener(_NoRedirect).open(request, timeout=60))


def propose_tir_prompt(
    config: ResearcherSpec,
    h0_prompt: str,
    public_tasks: tuple[Task, ...],
    baseline: RsiEvaluationReport,
) -> str:
    if baseline.split is not RsiSplit.HARNESS_EVOLVE:
        raise ValueError("Researcher feedback must come from harness_evolve")
    if tuple(task.task_id for task in public_tasks) != tuple(
        case.task_id for case in baseline.cases
    ):
        raise ValueError("Public task identities differ from feedback")
    by_id = {task.task_id: task for task in public_tasks}
    ordered = sorted(baseline.cases, key=lambda case: (case.score, case.task_id))
    examples = []
    for case in ordered[: config.max_cases]:
        messages = by_id[case.task_id].input["messages"]
        question = messages[0]["content"]
        if not isinstance(question, str):
            raise ValueError("Public task question is invalid")
        examples.append(
            {
                "question": question[:1000],
                "agent_response": case.response[:1500],
                "score": case.score,
            }
        )
    body = {
        "model": config.model,
        "temperature": 0.7,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"current_prompt": h0_prompt, "public_feedback": examples},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key_env is not None:
        key = os.environ.get(config.api_key_env)
        if not key:
            raise ValueError("Researcher API key environment variable is unset")
        headers["Authorization"] = f"Bearer {key}"
    request = Request(
        config.endpoint,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers=headers,
        method="POST",
    )
    with _open(request) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Researcher response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("Researcher returned an ambiguous response")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("Researcher message is malformed")
    prompt = choice["message"].get("content")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12_000:
        raise ValueError("Researcher prompt is empty or too large")
    if any(
        len(task.input["messages"][0]["content"]) > 40
        and task.input["messages"][0]["content"] in prompt
        for task in public_tasks
    ):
        raise ValueError("Researcher copied a specific public question into the Harness")
    return prompt.strip() + "\n"
