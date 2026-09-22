from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "examples/scale_rsi_bench/sandbox/session.py"
SPEC = importlib.util.spec_from_file_location("scale_bwrap_session", MODULE_PATH)
assert SPEC and SPEC.loader
session_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session_module)

TASK_PATH = MODULE_PATH.with_name("task.py")
TASK_SPEC = importlib.util.spec_from_file_location("scale_bwrap_task", TASK_PATH)
assert TASK_SPEC and TASK_SPEC.loader
task_module = importlib.util.module_from_spec(TASK_SPEC)
sys.path.insert(0, str(TASK_PATH.parent))
TASK_SPEC.loader.exec_module(task_module)

JOB_PATH = MODULE_PATH.with_name("job.py")
JOB_SPEC = importlib.util.spec_from_file_location("scale_bwrap_job", JOB_PATH)
assert JOB_SPEC and JOB_SPEC.loader
job_module = importlib.util.module_from_spec(JOB_SPEC)
JOB_SPEC.loader.exec_module(job_module)


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"),
    reason="Linux Bubblewrap integration",
)
def test_swarm_session_contains_public_files_and_hides_runtime(tmp_path: Path) -> None:
    runtime_root = MODULE_PATH.parents[4] / "scale_rsi_bench"
    if not (runtime_root / "swarm_env/source/tasks/agent-swarm-optimization").is_dir():
        pytest.skip("staged Scale RSI runtime is unavailable")
    session = session_module.create_session("swarm", tmp_path / "session", runtime_root)
    metadata = json.loads((session / "session.json").read_text())
    assert metadata["benchmark"] == "swarm"
    assert (session / "workspace/instruction.md").is_file()
    assert (session / "workspace/data/train.jsonl").is_file()
    assert not (session / "workspace/runs").exists()
    command = session_module.sandbox_command(
        session,
        [
            "/bin/bash",
            "-ec",
            "test -f instruction.md; test ! -e /storage; "
            "test ! -e /workspace/source; ! sbatch --version >/dev/null 2>&1",
        ],
    )
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_codex_prompt_is_accepted_after_options() -> None:
    args = session_module.parse_args(
        [
            "codex",
            "/tmp/session",
            "--model",
            "gpt-5.6-terra",
            "--base-url",
            "https://example.invalid/v1",
            "读取 instruction.md，努力提高这个 benchmark。",
        ]
    )
    assert args.prompt == "读取 instruction.md，努力提高这个 benchmark。"


def test_all_sessions_document_the_scoped_submission_bridge(tmp_path: Path) -> None:
    runtime_root = MODULE_PATH.parents[4] / "scale_rsi_bench"
    if not (runtime_root / "swarm_env/source/tasks/agent-swarm-optimization").is_dir():
        pytest.skip("staged Scale RSI runtime is unavailable")
    for benchmark in session_module.BENCHMARKS:
        session = session_module.create_session(benchmark, tmp_path / benchmark, runtime_root)
        instructions = (session / "workspace/instruction.md").read_text()
        assert "rsi-submit" in instructions
        assert 'run_job(command_name="experiment"' in instructions
        assert "cannot call `sbatch`" in instructions
        if benchmark == "opsd":
            assert (session / "workspace/eval_official.py").is_file()


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"),
    reason="Linux Bubblewrap integration",
)
@pytest.mark.parametrize("benchmark", tuple(session_module.BENCHMARKS))
def test_cli_submission_bridge_snapshots_and_returns_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark: str
) -> None:
    session = tmp_path / "session"
    for path in (
        session / "home/.codex",
        session / "workspace",
        session / "tmp",
        session / "control",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (session / "session.json").write_text(json.dumps({"benchmark": benchmark, "public_mounts": []}))
    (session / "workspace/method.txt").write_text("frozen")
    helper = tmp_path / "helpers"
    helper.mkdir()
    (helper / "job.py").write_text(
        """import json, sys, time
from pathlib import Path
time.sleep(0.2)
workspace, output = Path(sys.argv[3]), Path(sys.argv[4])
output.mkdir(parents=True, exist_ok=True)
(output / 'summary.json').write_text(json.dumps({
    'state': 'COMPLETED', 'benchmark': sys.argv[1],
    'method': (workspace / 'method.txt').read_text()
}))
"""
    )
    monkeypatch.setattr(session_module, "HERE", helper)
    monkeypatch.setattr(session_module, "REPO", tmp_path)
    bridge = session_module.SubmissionBridge(session, tmp_path / "runtime")
    bridge.start()
    try:
        submitted = subprocess.run(
            session_module.sandbox_command(session, ["rsi-submit"]),
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert submitted.returncode == 0, submitted.stderr
        assert json.loads(submitted.stdout)["state"] == "RUNNING"
        (session / "workspace/method.txt").write_text("changed-after-submit")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = subprocess.run(
                session_module.sandbox_command(session, ["rsi-submit", "status"]),
                text=True,
                capture_output=True,
                timeout=20,
            )
            value = json.loads(status.stdout)
            if value["state"] != "RUNNING":
                break
            time.sleep(0.1)
        assert value["state"] == "COMPLETED"
        assert value["summary"]["benchmark"] == benchmark
        assert value["summary"]["method"] == "frozen"
        assert (
            session / "workspace" / value["feedback"].removeprefix("/workspace/") / "summary.json"
        ).is_file()
    finally:
        bridge.close()


def test_cli_environment_does_not_expose_judge_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_API_KEY", "agent-key")
    monkeypatch.setenv("OPENAI_API_KEY", "judge-key")
    environment = session_module.clean_environment()
    assert environment["RSI_API_KEY"] == "agent-key"
    assert "OPENAI_API_KEY" not in environment


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("bwrap"),
    reason="Linux Bubblewrap integration",
)
def test_codex_sandbox_exposes_native_process_identity(tmp_path: Path) -> None:
    session = tmp_path / "session"
    for path in (session / "home/.codex", session / "workspace", session / "tmp"):
        path.mkdir(parents=True, exist_ok=True)
    (session / "session.json").write_text(json.dumps({"benchmark": "opsd", "public_mounts": []}))
    executable = Path(sys.executable).resolve()
    command = session_module.sandbox_command(
        session,
        ["/bin/readlink", "/proc/self/exe"],
        process_identity=executable,
    )
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(executable)


def test_rsi_task_configs_share_workspace_and_register_one_scoped_command(
    tmp_path: Path,
) -> None:
    from areal_rsi.base.storage import encode
    from areal_rsi.coordination.config import config_from_dict
    from areal_rsi.coordination.intake import validate_execution

    runtime_root = MODULE_PATH.parents[4] / "scale_rsi_bench"
    if not (runtime_root / "swarm_env/source/tasks/agent-swarm-optimization").is_dir():
        pytest.skip("staged Scale RSI runtime is unavailable")
    for benchmark in session_module.BENCHMARKS:
        session = session_module.create_session(benchmark, tmp_path / benchmark, runtime_root)
        config = task_module.build_config(
            benchmark,
            session,
            runtime_root,
            max_agents=1,
            max_jobs=5,
            wall_seconds=10_800,
        )
        restored = config_from_dict(encode(config))
        assert restored.metadata["shared_agent_workspace"] is True
        assert [command.name for command in restored.commands] == ["experiment"]
        assert restored.brief.limits.max_jobs == 5
        assert validate_execution(restored) == []


def test_fixed_gpu_recipes_use_trusted_evaluators_and_isolate_judge_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def capture(command: list[str], *, env: dict[str, str] | None = None) -> None:
        assert env is not None
        calls.append((command, env))

    monkeypatch.setattr(job_module, "run", capture)
    monkeypatch.setenv("OPENAI_API_KEY", "judge-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://judge.invalid/v1")
    monkeypatch.setenv("JAILBREAK_JUDGE_MODEL", "judge-model")
    workspace, output, runtime = tmp_path / "workspace", tmp_path / "output", tmp_path / "runtime"
    workspace.mkdir()
    output.mkdir()

    job_module.gpu_benchmark("opsd", workspace, output, runtime)
    opsd_command = " ".join(calls.pop()[0])
    assert "/rsi/benchmark/train_capped.sh" in opsd_command
    assert "/rsi/benchmark/eval_official.py" in opsd_command
    assert "/workspace/dev_eval.sh" not in opsd_command

    job_module.gpu_benchmark("jailbreak", workspace, output, runtime)
    assert len(calls) == 2
    generation, grading = calls
    assert "OPENAI_API_KEY" not in generation[1]
    assert grading[1]["OPENAI_API_KEY"] == "judge-secret"
    assert "/opt/eval_src/generate.py" in " ".join(generation[0])
    assert "/opt/eval_src/grade.py" in " ".join(grading[0])
    assert "/opt/eval_data/dev_split.jsonl" in " ".join(generation[0] + grading[0])
    calls.clear()

    job_module.gpu_benchmark("swarm", workspace, output, runtime)
    swarm_command = " ".join(calls.pop()[0])
    assert "/rsi/scripts/eval_visible.py" in swarm_command
    assert "/workspace/selfcheck.py" not in swarm_command


def test_restricted_gpu_containers_disable_admin_storage_mount() -> None:
    repository = Path(__file__).parents[1]
    for benchmark in ("opsd", "jailbreak", "swarm"):
        script = (
            repository / f"examples/scale_rsi_bench/{benchmark}_env/scripts/container.sh"
        ).read_text()
        restricted = script.split("if [[ ${RSI_RESTRICTED:-0} == 1 ]]; then", 1)[1].split(
            "else", 1
        )[0]
        assert "mount_options=(--no-mount /storage)" in restricted
        assert '"$ROOT/.venv:$ROOT/.venv:ro"' not in restricted
        assert '"$ROOT/.venv:/rsi/.venv:ro"' in restricted
        assert '"${mount_options[@]}" "${binds[@]}"' in script

    opsd = (repository / "examples/scale_rsi_bench/opsd_env/scripts/container.sh").read_text()
    assert "/rsi/benchmark/train_capped.sh:ro" in opsd
    assert "/rsi/benchmark/eval_official.py:ro" in opsd
    assert "OPENAI_API_KEY" not in opsd

    jailbreak = (
        repository / "examples/scale_rsi_bench/jailbreak_env/scripts/container.sh"
    ).read_text()
    assert '"$ROOT/workspace/data:/opt/eval_data:ro"' in jailbreak

    swarm = (repository / "examples/scale_rsi_bench/swarm_env/scripts/container.sh").read_text()
    assert "/rsi/scripts/eval_visible.py:ro" in swarm
    assert "/rsi/engine:ro" in swarm
    assert "/rsi/val.jsonl:ro" in swarm
    assert "OPENAI_API_KEY" not in swarm


def test_restricted_gpu_runtime_relocates_virtualenv() -> None:
    repository = Path(__file__).parents[1]
    for benchmark in ("opsd", "jailbreak", "swarm"):
        script = (
            repository / f"examples/scale_rsi_bench/{benchmark}_env/scripts/inside.sh"
        ).read_text()
        assert "if [[ $RSI_ROOT == /rsi ]]; then" in script
        assert "export VIRTUAL_ENV=/rsi/.venv" in script
        assert "/rsi/.venv/bin:$PATH" in script
        assert "export HF_DATASETS_CACHE=/tmp/hf-datasets" in script

    swarm_container = (
        repository / "examples/scale_rsi_bench/swarm_env/scripts/container.sh"
    ).read_text()
    swarm_launcher = (repository / "examples/scale_rsi_bench/swarm_env/run.sh").read_text()
    assert "/rsi/scripts/server_session.py:ro" in swarm_container
    assert "server_session=/rsi/scripts/server_session.py" in swarm_launcher
