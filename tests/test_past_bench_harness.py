from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest


def module(name: str) -> Any:
    path = Path(__file__).parents[1] / "examples/past_bench" / (name + ".py")
    spec = importlib.util.spec_from_file_location("pb_" + name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


pb, runtime = module("run"), module("harness_runtime")


def candidate(root: Path) -> Path:
    for name in ("run_agent.py", "hermes_state.py", "tools/registry.py"):
        path = root / "hermes" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture")
    (root / "prompt.md").write_text("General policy")
    return root


def test_full_source_identity_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = candidate(tmp_path / "candidate")
    original = pb.candidate_policy(root, "hermes")[1]
    (root / "hermes/tools/new_tool.py").write_text("value = 1")
    assert pb.candidate_policy(root, "hermes")[1] != original
    with pytest.raises(ValueError, match="Candidate permits"):
        pb.candidate_policy(root, "prompt")
    (root / "grader.py").write_text("# not candidate scope")
    with pytest.raises(ValueError, match="Candidate permits"):
        pb.candidate_policy(root, "hermes")
    (root / "grader.py").unlink()
    (root / "hermes/escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlinks"):
        pb.candidate_policy(root, "hermes")
    (root / "hermes/escape").unlink()
    lock = {
        "protocol_id": "fixed",
        "protocol": {
            "edit_scope": "hermes",
            "options": {"target_model": "fixed", "base_url": "https://example.test/v1"},
        },
        "manifests": {
            split: [{"family": "memory/fixture", "episodes": [{"task_id": "a"}]}]
            for split in pb.PHASES.values()
        },
    }
    monkeypatch.setattr(pb, "load_lock", lambda _: lock)
    config = pb.research_config(tmp_path / "lock", root)
    assert config.brief.limits.allowed_paths == ("prompt.md", "hermes/*")
    monkeypatch.delenv("RSI_TEACHER_BASE_URL", raising=False)
    assert pb.research_config(tmp_path / "lock", root).base_url == "https://example.test/v1"
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "https://4ipic.com/v1")
    assert pb.research_config(tmp_path / "lock", root).base_url == "https://4ipic.com/v1"


@pytest.fixture
def fake_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "upstream"
    package = root / "past_bench/runtime/adapters"
    package.mkdir(parents=True)
    for path in (root / "past_bench", package.parent, package):
        (path / "__init__.py").touch()
    (package.parent / "protocol.py").write_text("""from types import SimpleNamespace
class Record(SimpleNamespace):
    @classmethod
    def model_validate(cls, value): return cls(**value)
    def model_dump(self, **kwargs): return vars(self)
StartSessionRequest = StepRequest = StepResponse = Record
""")
    (package.parent / "registry.py").write_text("from .protocol import Record as AgentSpec\n")
    (package / "hermes.py").write_text("""from pathlib import Path
from ..protocol import Record
_HERMES_ROOT_DEFAULT = Path('/unused/original-hermes')
class HermesAdapter:
    def __init__(self, spec, request): self.request = request
    def step(self, step):
        from run_agent import answer
        return Record(status='finished', final_output=answer(self.request.home))
    def close(self): pass
""")
    monkeypatch.setenv("PYTHONPATH", str(root))
    return root


def test_real_worker_loads_edited_tools_and_isolates_episode_code(
    tmp_path: Path, fake_upstream: Path
) -> None:
    root = candidate(tmp_path / "candidate") / "hermes"
    (root / "tools/__init__.py").touch()
    (root / "tools/memory.py").write_text("VALUE = 'original'")
    (root / "run_agent.py").write_text("""from pathlib import Path
from tools.memory import VALUE
def answer(home):
    state = Path(home)
    count = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(count))
    scratch = Path(__file__).with_name('scratch')
    assert not scratch.exists(), 'source state leaked across episodes'
    scratch.write_text('episode-local')
    return f'{VALUE}:{count}'
""")
    payload = {"spec": {}, "session": {"home": str(tmp_path / "memory")}, "step": {}}
    original = runtime.source_digest(root)
    first = runtime.execute_worker(root, payload, 10)
    assert first["response"]["final_output"] == "original:1"
    assert first["source_digest"] == original
    (root / "tools/memory.py").write_text("VALUE = 'edited'")
    second = runtime.execute_worker(root, payload, 10)
    assert second["response"]["final_output"] == "edited:2"
    assert second["source_digest"] != original
    assert not (root / "scratch").exists()
    assert not list(root.rglob("__pycache__"))
    payload["session"]["home"] = str(tmp_path / "fresh-memory")
    assert runtime.execute_worker(root, payload, 10)["response"]["final_output"] == "edited:1"


def test_worker_timeout_and_credential_environment(
    tmp_path: Path, fake_upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "do-not-forward")
    monkeypatch.setenv("JUDGE_API_KEY", "do-not-forward")
    assert "RSI_TEACHER_API_KEY" not in runtime.worker_environment()
    assert "JUDGE_API_KEY" not in runtime.worker_environment()
    root = candidate(tmp_path / "candidate") / "hermes"
    (root / "run_agent.py").write_text("import time\ndef answer(home): time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired):
        runtime.execute_worker(root, {"spec": {}, "session": {"home": "unused"}, "step": {}}, 0.2)


def test_missing_or_tampered_loading_receipt_fails(tmp_path: Path) -> None:
    source = candidate(tmp_path / "candidate") / "hermes"
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "one.jsonl").write_text(json.dumps({"type": "runtime_request", "session_id": "s"}))
    path = tmp_path / "receipts.jsonl"
    entry = {
        "session_id": "s",
        "loaded_entry": "hermes/run_agent.py",
        "source_digest": runtime.source_digest(source),
    }
    path.write_text(json.dumps(entry))
    pb.check_harness_receipts(trace, path, source)
    entry["source_digest"] = "different"
    path.write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="receipts"):
        pb.check_harness_receipts(trace, path, source)


def test_seed_copies_only_tracked_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "upstream"
    for name in ("run_agent.py", "hermes_state.py", "tools/registry.py", ".env"):
        path = repository / "agents/hermes-agent" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private" if name == ".env" else "# source")
    paths = [
        "agents/hermes-agent/" + name
        for name in ("run_agent.py", "hermes_state.py", "tools/registry.py")
    ]
    monkeypatch.setattr(
        pb,
        "load_lock",
        lambda _: {
            "protocol": {"edit_scope": "hermes", "options": {"repository": str(repository)}}
        },
    )
    monkeypatch.setattr(pb, "capture", lambda *a, **kw: "\0".join(paths))
    output = tmp_path / "seed"
    result = pb.seed_harness(tmp_path / "lock", output)
    assert result["harness_id"] == pb.candidate_policy(output, "hermes")[1]
    assert not (output / "hermes/.env").exists()


def test_saved_versions_are_separate_and_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "examples/past_bench"))
    versions = module("versions")
    workspace = candidate(tmp_path / "workspace")
    first, second = tmp_path / "versions/v001", tmp_path / "versions/v002"
    original = versions.save_version(workspace, first, "hermes")
    (workspace / "hermes/run_agent.py").write_text("# edited execution loop")
    modified = versions.save_version(workspace, second, "hermes")
    assert original["harness_id"] != modified["harness_id"]
    assert (first / "harness/hermes/run_agent.py").read_text() == "# fixture"
    assert versions.verify_version(second / "version.json") == second / "harness"
    with pytest.raises(FileExistsError):
        versions.save_version(workspace, first, "hermes")
    (second / "harness/hermes/run_agent.py").write_text("# overwritten")
    with pytest.raises(ValueError, match="Saved Harness version changed"):
        versions.verify_version(second / "version.json")
    with pytest.raises(ValueError, match="outside its source"):
        versions.save_version(workspace, workspace / "nested", "hermes")


def test_comparison_budget_model_and_version_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "examples/past_bench"))
    from areal_rsi.api.research import TaskBrief
    from areal_rsi.coordination.config import ResearchConfig, config_from_dict

    versions, comparison = module("versions"), module("comparison")
    workspace, saved = candidate(tmp_path / "workspace"), tmp_path / "saved"
    versions.save_version(workspace, saved, "hermes")
    lock = {"protocol": {"edit_scope": "hermes", "options": {"target_model": "gpt-6-astra"}}}
    monkeypatch.setattr(comparison, "load_lock", lambda _: lock)
    monkeypatch.setattr(
        comparison,
        "research_config",
        lambda lock, source: ResearchConfig(brief=TaskBrief("fixture", ""), seed_dir=str(source)),
    )
    monkeypatch.setenv("RSI_MODEL", "different-model")
    config = config_from_dict(
        comparison.comparison_config(tmp_path / "lock", saved / "version.json")
    )
    assert config.model_settings()["model"] == "gpt-6-astra"
    assert config.brief.limits.max_verify_calls == 32 + 6
    assert config.brief.limits.max_nodes is not None and config.brief.limits.max_nodes >= 30
    assert config.commands == ()
    assert config.preflight is not None and "--preflight" in config.preflight.command
    (saved / "harness/prompt.md").write_text("modified saved version")
    with pytest.raises(ValueError, match="Saved Harness version changed"):
        comparison.comparison_config(tmp_path / "lock", saved / "version.json")
