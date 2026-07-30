"""AReaL-RSI command line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from areal_rsi.agent import FakeRSIAgent, OpenAIAgentConfig, OpenAIRSIAgent
from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.services import MockAReaLService, MockEnvClient, MockResearchAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="areal-rsi")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run_mock = subparsers.add_parser("run-mock", help="Run one local mock RSI iteration.")
    run_mock.add_argument("--ckpt", default="ckpt_000")
    run_mock.add_argument("--task-set", default="swe_verified")
    run_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_mock.add_argument("--harness", default="mock_harness")
    run_mock.add_argument("--run-root", default="runs")

    run_loop_mock = subparsers.add_parser("run-loop-mock", help="Run a local mock multi-iteration RSI loop.")
    run_loop_mock.add_argument("--ckpt", default="ckpt_000")
    run_loop_mock.add_argument("--task-set", default="swe_verified")
    run_loop_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_loop_mock.add_argument("--harness", default="mock_harness")
    run_loop_mock.add_argument("--max-iterations", type=int, default=3)
    run_loop_mock.add_argument("--run-root", default="runs")

    status = subparsers.add_parser("status", help="Print run state.")
    status.add_argument("run_id")
    status.add_argument("--run-root", default="runs")

    chat = subparsers.add_parser("chat", help="Ask RSIAgent for an action.")
    chat.add_argument("message", nargs="*", help="Message to send. If omitted, stdin is read.")
    chat.add_argument("--backend", choices=["fake", "openai"], default="fake")
    chat.add_argument("--model", help="OpenAI model for --backend openai. Defaults to OPENAI_MODEL or gpt-5-mini.")
    chat.add_argument("--run-id")
    chat.add_argument("--run-root", default="runs")
    chat.add_argument("--execute", action="store_true", help="Execute returned actions with the local controller.")
    chat.add_argument("--yes", action="store_true", help="Execute actions that require confirmation.")
    chat.add_argument("--harness", default="mock_harness")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from areal_rsi import __version__

        print(__version__)
        return 0
    if args.command == "run-mock":
        state = _run_mock(args)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "run-loop-mock":
        state = _run_loop_mock(args)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "status":
        controller = LocalRSIController(run_root=args.run_root)
        state = controller.get_status(args.run_id)
        print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False, default=repr))
        return 0
    if args.command == "chat":
        result = _chat(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=repr))
        return 0
    parser.print_help()
    return 0


def _run_mock(args: argparse.Namespace):
    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    controller = LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )
    return controller.run_iteration(
        ckpt=args.ckpt,
        task_set=args.task_set,
        method=args.method,
        harness_config={"harness": args.harness},
    )


def _run_loop_mock(args: argparse.Namespace):
    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    controller = LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )
    return controller.run_loop(
        initial_ckpt=args.ckpt,
        task_set=args.task_set,
        method=args.method,
        harness_config={"harness": args.harness},
        max_iterations=args.max_iterations,
    )


def _chat(args: argparse.Namespace) -> dict:
    message = " ".join(args.message).strip()
    if not message:
        import sys

        message = sys.stdin.read().strip()
    if not message:
        raise SystemExit("chat message is required")

    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    controller = _mock_controller(run_root, memory)
    if args.backend == "fake":
        agent = FakeRSIAgent()
    else:
        agent = OpenAIRSIAgent(config=OpenAIAgentConfig(system_prompt=_openai_rsi_system_prompt(), model=args.model))

    response = agent.chat(
        message,
        run_id=args.run_id,
        controller=controller,
        memory=memory,
        workflow_spec={"harness": args.harness},
    )
    payload = _agent_response_to_dict(response)
    if args.execute:
        payload["executions"] = [
            _execute_chat_action(controller, action, allow_confirmed=args.yes)
            for action in response.actions
        ]
    return payload


def _mock_controller(run_root: Path, memory: Memory) -> LocalRSIController:
    return LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )


def _execute_chat_action(controller: LocalRSIController, action: Action, *, allow_confirmed: bool) -> dict:
    if action.requires_confirmation and not allow_confirmed:
        return {
            "action": _action_to_dict(action),
            "executed": False,
            "reason": "confirmation_required",
        }
    return {
        "action": _action_to_dict(action),
        "executed": True,
        "result": controller.execute_action(action),
    }


def _agent_response_to_dict(response: AgentResponse) -> dict:
    return {
        "reply": response.reply,
        "actions": [_action_to_dict(action) for action in response.actions],
        "requires_confirmation": response.requires_confirmation,
        "metadata": response.metadata,
    }


def _action_to_dict(action: Action) -> dict:
    return {
        "type": action.type,
        "args": action.args,
        "requires_confirmation": action.requires_confirmation,
        "metadata": action.metadata,
    }


def _openai_rsi_system_prompt() -> str:
    return (
        "You are RSIAgent, the user-facing coordinator for AReaL-RSI. "
        "Choose exactly one action tool for user goals and queries: "
        "start_loop, get_status, inspect_run, resume_run, or stop_run. "
        "Do not expose internal workflow steps such as eval, train, or "
        "holdout_eval as user actions. Ask for confirmation before expensive "
        "or irreversible operations."
    )


if __name__ == "__main__":
    raise SystemExit(main())
