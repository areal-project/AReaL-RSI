from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
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
