"""AReaL-RSI command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from areal_rsi.agent import FakeRSIAgent, OpenAIAgentConfig, OpenAIRSIAgent
from areal_rsi.config import RSIRunConfig
from areal_rsi.controller import LocalRSIController
from areal_rsi.memory import Memory
from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.services import (
    AReaLService,
    EnvArenaClient,
    MockAReaLService,
    MockEnvClient,
    MockResearchAgent,
    OpenAIResearchAgent,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="areal-rsi")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run an RSI loop from a config file.")
    run.add_argument("--config", required=True, help="Path to RSI config YAML/JSON.")
    run.add_argument("--run-root", default="runs")

    run_mock = subparsers.add_parser("run-mock", help="Run one local mock RSI iteration.")
    run_mock.add_argument("--ckpt", default="ckpt_000")
    run_mock.add_argument("--benchmark", "--task-set", dest="benchmark", default="swe_verified")
    run_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_mock.add_argument("--harness", default="mock_harness")
    run_mock.add_argument("--run-root", default="runs")

    run_loop_mock = subparsers.add_parser("run-loop-mock", help="Run a local mock multi-iteration RSI loop.")
    run_loop_mock.add_argument("--ckpt", default="ckpt_000")
    run_loop_mock.add_argument("--benchmark", "--task-set", dest="benchmark", default="swe_verified")
    run_loop_mock.add_argument("--method", default="sft", choices=["sft", "opd"])
    run_loop_mock.add_argument("--harness", default="mock_harness")
    run_loop_mock.add_argument("--max-iterations", type=int, default=3)
    run_loop_mock.add_argument("--run-root", default="runs")

    status = subparsers.add_parser("status", help="Print run state as JSON.")
    status.add_argument("run_id")
    status.add_argument("--run-root", default="runs")

    chat = subparsers.add_parser("chat", help="Ask RSIAgent for an action.")
    chat.add_argument("message", nargs="*", help="Message to send. If omitted, an interactive REPL starts.")
    chat.add_argument("--backend", choices=["fake", "openai"], default="fake")
    chat.add_argument("--model", help="OpenAI model for --backend openai. Defaults to OPENAI_MODEL or gpt-5-mini.")
    chat.add_argument("--run-id")
    chat.add_argument("--run-root", default="runs")
    chat.add_argument("--execute", action="store_true", help="Execute returned actions with the local controller.")
    chat.add_argument("--yes", action="store_true", help="Execute actions that require confirmation.")
    chat.add_argument("--harness", default="mock_harness")
    chat.add_argument("--config", help="Base RSI config loaded before interpreting start_loop.")
    chat.add_argument("--stdin", action="store_true", help="Read one message from stdin instead of starting REPL.")
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
    if args.command == "run":
        state = _run_config(args)
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
        if result is not None:
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
        benchmark=args.benchmark,
        method=args.method,
        harness_config={"harness": args.harness},
    )


def _run_config(args: argparse.Namespace):
    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    config = RSIRunConfig.load(args.config)
    controller = _controller_from_rsi_config(config, run_root, memory)
    return controller.run_config(
        config,
        config_metadata={
            "base_config_path": str(config.source_path) if config.source_path else None,
            "base_config_hash": config.source_hash,
            "overrides": {},
        },
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
        benchmark=args.benchmark,
        method=args.method,
        harness_config={"harness": args.harness},
        max_iterations=args.max_iterations,
    )


def _chat(args: argparse.Namespace) -> dict | None:
    message = " ".join(args.message).strip()
    run_root = Path(args.run_root)
    memory = Memory(run_root / "memory")
    base_config = RSIRunConfig.load(args.config) if args.config else None
    controller = (
        _controller_from_rsi_config(base_config, run_root, memory)
        if base_config is not None
        else _mock_controller(run_root, memory)
    )
    agent = _build_agent(args)
    if not message:
        if args.stdin:
            message = sys.stdin.read().strip()
        else:
            _chat_repl(
                args,
                agent=agent,
                controller=controller,
                memory=memory,
                base_config=base_config,
            )
            return None
    if not message:
        raise SystemExit("chat message is required")
    return _chat_once(
        args,
        message,
        agent=agent,
        controller=controller,
        memory=memory,
        base_config=base_config,
        run_id=args.run_id,
    )


def _build_agent(args: argparse.Namespace):
    if args.backend == "fake":
        return FakeRSIAgent()
    return OpenAIRSIAgent(config=OpenAIAgentConfig(system_prompt=_openai_rsi_system_prompt(), model=args.model))


def _chat_once(
    args: argparse.Namespace,
    message: str,
    *,
    agent,
    controller: LocalRSIController,
    memory: Memory,
    base_config: RSIRunConfig | None,
    run_id: str | None,
) -> dict:
    response = agent.chat(
        message,
        run_id=run_id,
        controller=controller,
        memory=memory,
        workflow_spec=_workflow_spec(args.harness, base_config),
    )
    payload = _agent_response_to_dict(response)
    if args.execute:
        payload["executions"] = [
            _execute_chat_action(controller, action, allow_confirmed=args.yes, base_config=base_config)
            for action in response.actions
        ]
    return payload


def _chat_repl(
    args: argparse.Namespace,
    *,
    agent,
    controller: LocalRSIController,
    memory: Memory,
    base_config: RSIRunConfig | None,
) -> None:
    current_run_id = args.run_id
    config_note = f" config={base_config.source_path}" if base_config and base_config.source_path else ""
    print(f"AReaL-RSI chat started.{config_note} Type 'exit' or 'quit' to leave.")
    read_message = _repl_reader()
    while True:
        try:
            message = read_message().strip()
        except EOFError:
            print()
            return
        if not message:
            continue
        if message.lower() in {"exit", "quit", ":q"} or message in {"退出", "再见"}:
            return
        try:
            payload = _chat_once(
                args,
                message,
                agent=agent,
                controller=controller,
                memory=memory,
                base_config=base_config,
                run_id=current_run_id,
            )
        except Exception as exc:
            print(f"error: {exc}")
            continue
        print(payload["reply"])
        for execution in payload.get("executions", []):
            if not execution.get("executed"):
                print(f"not executed: {execution.get('reason')}")
                continue
            result = execution.get("result", {})
            current_run_id = result.get("run_id", current_run_id)
            print(
                "executed: "
                f"run_id={result.get('run_id')} "
                f"status={result.get('status')} "
                f"iteration={result.get('iteration')} "
                f"ckpt={result.get('ckpt')}"
            )
        if not payload.get("executions"):
            for action in payload.get("actions", []):
                print(f"action: {action['type']} args={json.dumps(action['args'], ensure_ascii=False)}")


def _repl_reader():
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            from prompt_toolkit import PromptSession
        except ImportError:
            pass
        else:
            session = PromptSession()
            return lambda: session.prompt("AReaL-RSI> ")
    return lambda: input("AReaL-RSI> ")


def _mock_controller(run_root: Path, memory: Memory) -> LocalRSIController:
    return LocalRSIController(
        run_root=run_root,
        areal=MockAReaLService(),
        envarena=MockEnvClient(),
        research_agent=MockResearchAgent(),
        memory=memory,
    )


def _controller_from_rsi_config(
    config: RSIRunConfig,
    run_root: Path,
    memory: Memory,
) -> LocalRSIController:
    """Compose configured services at the CLI boundary before running a loop."""

    services = config.data.get("services", {})
    if not isinstance(services, dict):
        raise ValueError("RSI config services section must be a mapping.")
    areal_spec = _service_spec(services, "areal")
    envarena_spec = _service_spec(services, "envarena")
    research_spec = _service_spec(services, "research")

    areal_config_path = _service_config_path(config, "areal", areal_spec)
    if areal_config_path is None:
        areal = MockAReaLService()
    else:
        areal = AReaLService.from_yaml(areal_config_path)

    envarena_config_path = _service_config_path(config, "envarena", envarena_spec)
    envarena = (
        EnvArenaClient.from_yaml(envarena_config_path)
        if envarena_config_path is not None
        else MockEnvClient()
    )
    research_agent = _research_agent_from_spec(research_spec)
    return LocalRSIController(
        run_root=run_root,
        areal=areal,
        envarena=envarena,
        research_agent=research_agent,
        memory=memory,
    )


def _service_spec(services: dict[str, object], name: str) -> dict[str, object]:
    value = services.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"RSI config services.{name} must be a mapping.")
    return value


def _service_config_path(
    config: RSIRunConfig,
    service_name: str,
    service_spec: dict[str, object],
) -> Path | None:
    value = service_spec.get("config_path")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"RSI config services.{service_name}.config_path must be a non-empty string.")
    path = Path(value).expanduser()
    if not path.is_absolute() and config.source_path is not None:
        path = config.source_path.parent / path
    return path


def _research_agent_from_spec(spec: dict[str, object]):
    backend = str(spec.get("backend", "mock"))
    if backend == "mock":
        return MockResearchAgent()
    if backend == "openai":
        return OpenAIResearchAgent()
    raise ValueError(f"Unsupported research service backend: {backend!r}.")


def _execute_chat_action(
    controller: LocalRSIController,
    action: Action,
    *,
    allow_confirmed: bool,
    base_config: RSIRunConfig | None = None,
) -> dict:
    if action.requires_confirmation and not allow_confirmed:
        return {
            "action": _action_to_dict(action),
            "executed": False,
            "reason": "confirmation_required",
        }
    executable_action = action
    if base_config is not None and action.type == "start_loop":
        executable_action = Action(
            type=action.type,
            args=action.args,
            requires_confirmation=action.requires_confirmation,
            metadata={**action.metadata, "base_config": base_config},
        )
    return {
        "action": _action_to_dict(action),
        "executed": True,
        "result": controller.execute_action(executable_action),
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


def _workflow_spec(harness: str, base_config: RSIRunConfig | None) -> dict:
    spec = {"harness": harness}
    if base_config is not None:
        spec["base_config"] = base_config.to_dict()
        spec["base_config_path"] = str(base_config.source_path) if base_config.source_path else None
        spec["base_config_hash"] = base_config.source_hash
    return spec


def _openai_rsi_system_prompt() -> str:
    return (
        "You are RSIAgent, the user-facing coordinator for AReaL-RSI. "
        "Choose exactly one action tool for user goals and queries: "
        "start_loop, inspect_run, resume_run, or stop_run. "
        "Answer brief progress or completion questions directly from context "
        "without an action tool. "
        "Do not expose internal workflow steps such as eval, train, or "
        "holdout_eval as user actions. Ask for confirmation before expensive "
        "or irreversible operations."
    )


if __name__ == "__main__":
    raise SystemExit(main())
