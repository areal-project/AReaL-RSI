from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import math
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.coordination.config import config_from_dict

ADAPTER = Path(__file__).parents[1] / "examples/past_bench/run.py"
spec = importlib.util.spec_from_file_location("past_bench_adapter", ADAPTER)
assert spec is not None and spec.loader is not None
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


@pytest.mark.parametrize("failed", [False, True])
def test_original_artifacts_are_indexed_even_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    output = tmp_path / "output"

    def fake_evaluate(*args: Any) -> dict[str, Any]:
        private = output / "private" / "family"
        private.mkdir(parents=True)
        (private / "trace.jsonl").write_text('{"tool_output":"original content"}\n')
        (private / "report.json").write_text('{"score":0}')
        if failed:
            raise ValueError("runner failed")
        return {"status": "completed"}

    monkeypatch.setattr(pb, "_evaluate", fake_evaluate)
    if failed:
        with pytest.raises(ValueError, match="runner failed"):
            pb.evaluate(tmp_path / "lock", tmp_path / "harness", output, "search")
    else:
        pb.evaluate(tmp_path / "lock", tmp_path / "harness", output, "search")
    index = json.loads((output / "artifacts.json").read_text())
    assert {item["path"] for item in index["files"]} == {
        "private/family/trace.jsonl",
        "private/family/report.json",
    }
    assert (output / "private/family/trace.jsonl").read_text() == (
        '{"tool_output":"original content"}\n'
    )


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    episodes: list[dict[str, Any]] = [
        {"task_id": "cold", "bucket": "baseline", "stage": "cold", "history_mode": "continue"},
        {"task_id": "learn", "bucket": "learn", "stage": "learn_a", "history_mode": "continue"},
        {
            "task_id": "learn_REFLECT",
            "bucket": "reflection",
            "stage": "reflection",
            "episode_kind": "reflection",
            "requires_fresh_session": True,
        },
        {
            "task_id": "near",
            "bucket": "evaluation",
            "stage": "eval_near",
            "history_mode": "from_anchor",
            "history_load_anchor": "learned",
        },
        {"task_id": "control", "bucket": "control", "stage": "control", "history_mode": "fresh"},
    ]
    manifest = {
        "family": "memory/fixture",
        "family_id": "fixture",
        "episodes": episodes,
        "service_ports": [],
    }
    comparison = {}
    for variant, evaluation in (("with_persistence", 0.9), ("without_persistence", 0.3)):
        comparison[variant] = {
            "variant": variant,
            "episodes": [
                {
                    **entry,
                    "family_id": "fixture",
                    "infra_blocked": False,
                    "passed": True,
                    "task_score": evaluation if entry["bucket"] == "evaluation" else 0.2,
                }
                for entry in episodes
            ],
        }
    return manifest, comparison


def test_paired_scores_use_episodes_not_untrusted_summary() -> None:
    manifest, comparison = fixture()
    comparison["delta"] = {"evaluation_avg_task_score": 100}
    result = pb.score_comparison(comparison, manifest)
    assert result[pb.METRIC] == pytest.approx(0.6)
    assert result["evaluation_score"] == 0.9
    comparison["with_persistence"]["episodes"][3]["task_score"] = 0
    assert pb.score_comparison(comparison, manifest)[pb.METRIC] == pytest.approx(-0.3)


def test_full_manifest_is_discovered_independently_of_search_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for family in ("memory/a", "reuse/b", "update/c"):
        path = tmp_path / "self-evolve-tasks-v2" / family / "family.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("{}")
    monkeypatch.setattr(
        pb,
        "family_manifest",
        lambda options, family: {
            "family": family,
            "episodes": [{"task_id": "learn"}, {"task_id": "test"}],
        },
    )
    manifests = pb.full_manifest(
        {"repository": str(tmp_path), "families": {"search": ["memory/a"]}}
    )
    assert len(manifests) == 3
    assert len(pb.task_ids(manifests)) == 12  # Both persistence variants, all episodes.
    lock = {
        "protocol_id": "p",
        "protocol": {"options": {"target_model": "m"}},
        "manifests": {"full": manifests, "search": manifests[:1]},
    }
    assert pb.phase_identity(lock, "baseline") == pb.phase_identity(lock, "final")
    assert len(pb.phase_identity(lock, "search")["task_ids"]) == 4


def test_families_without_cold_baselines_do_not_fabricate_a_baseline() -> None:
    manifest, comparison = fixture()
    manifest["episodes"].pop(0)
    for variant in ("with_persistence", "without_persistence"):
        comparison[variant]["episodes"].pop(0)
    result = pb.score_comparison(comparison, manifest)
    assert result["evaluation_lift"] == pytest.approx(0.6)
    assert "baseline_score" not in result
    assert "ablation_lift" not in result


def test_macro_average_and_baseline_coverage() -> None:
    result = pb.aggregate_scores(
        [
            {"evaluation_lift": 0.4, "baseline_score": 0.2, "ablation_lift": 0.3},
            {"evaluation_lift": 0.2},
        ]
    )
    assert result["evaluation_lift"] == pytest.approx(0.3)
    assert result["baseline_score"] == 0.2
    assert result["ablation_lift"] == 0.3
    assert result["baseline_family_count"] == 1


def test_occupied_mock_port_is_rejected_without_resetting_service() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match="occupied"):
            pb.require_free_ports([port])
    pb.require_free_ports([port])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "order",
        "infra",
        "nan",
        "bool",
        "range",
        "history",
        "variant",
        "reflection",
        "family",
        "status",
    ],
)
def test_invalid_comparisons_fail_closed(mutation: str) -> None:
    manifest, comparison = fixture()
    block = comparison["with_persistence"]
    rows = block["episodes"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "order":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "infra":
        rows[0]["infra_blocked"] = True
    elif mutation in {"nan", "bool", "range"}:
        rows[0]["task_score"] = {"nan": math.nan, "bool": True, "range": 2}[mutation]
    elif mutation == "history":
        rows[3]["history_load_anchor"] = "other-candidate"
    elif mutation == "variant":
        block["variant"] = "without_persistence"
    elif mutation == "reflection":
        rows[2]["task_id"] = "arbitrary-extra-task"
    elif mutation == "family":
        rows[0]["family_id"] = "other"
    else:
        del rows[0]["infra_blocked"]
    with pytest.raises(ValueError):
        pb.score_comparison(comparison, manifest)


def test_candidate_scope_identity_and_symlinks(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "prompt.md").write_text("Learn general rules.")
    text, identity = pb.candidate_policy(candidate)
    assert text == "Learn general rules."
    assert identity == pb.candidate_policy(candidate)[1]
    (candidate / "prompt.md").write_text("Retrieve relevant rules.")
    assert identity != pb.candidate_policy(candidate)[1]
    (candidate / "prompt.md").write_text("${RSI_API_KEY}")
    with pytest.raises(ValueError, match="environment-variable"):
        pb.candidate_policy(candidate)
    (candidate / "prompt.md").write_text("General policy")
    (candidate / "grader.py").write_text("raise RuntimeError('must never execute')")
    with pytest.raises(ValueError, match="Candidate permits"):
        pb.candidate_policy(candidate)
    (candidate / "grader.py").unlink()
    (candidate / "secret").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlinks"):
        pb.candidate_policy(candidate)


def options(tmp_path: Path) -> dict[str, Any]:
    source = Path(__file__).parents[1] / "examples/past_bench/operator.example.json"
    value: dict[str, Any] = json.loads(source.read_text())
    value.update(repository=str(tmp_path), python=str(Path(__file__)))
    return value


@pytest.mark.parametrize("mutation", ["overlap", "empty", "traversal", "unknown", "key", "url"])
def test_operator_validation(tmp_path: Path, mutation: str) -> None:
    value = options(tmp_path)
    pb.validate_options(value)
    if mutation == "overlap":
        value["families"]["final"] = value["families"]["search"]
    elif mutation == "empty":
        value["families"]["search"] = []
    elif mutation == "traversal":
        value["families"]["search"] = ["../../secret"]
    elif mutation == "unknown":
        value["command"] = "custom grader"
    elif mutation == "key":
        value["api_key_env"] = "sk-secret"
    else:
        value["base_url"] = "https://user:secret@example.org/v1"
    with pytest.raises(ValueError):
        pb.validate_options(value)


def test_manifest_and_protocol_identity_change_with_effective_inputs() -> None:
    manifest, _ = fixture()
    lock: dict[str, Any] = {
        "protocol_id": "p",
        "protocol": {"options": {"target_model": "fixed"}},
        "manifests": {"development": [manifest], "search": [manifest]},
    }
    original = pb.phase_identity(lock, "diagnostic")
    assert original == pb.phase_identity(copy.deepcopy(lock), "diagnostic")
    lock["manifests"]["development"][0]["episodes"].pop()
    assert original["manifest_id"] != pb.phase_identity(lock, "diagnostic")["manifest_id"]
    lock["protocol_id"] = "changed"
    assert original["protocol_id"] != pb.phase_identity(lock, "diagnostic")["protocol_id"]


def test_research_config_uses_frozen_harness_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, _ = fixture()
    lock: dict[str, Any] = {
        "protocol_id": "p",
        "protocol": {"options": {"target_model": "fixed", "base_url": "https://example.test/v1"}},
        "manifests": {split: [{**manifest, "family_id": split}] for split in pb.PHASES.values()},
    }
    monkeypatch.setattr(pb, "load_lock", lambda path: lock)
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "prompt.md").write_text("General policy")
    config = pb.research_config(tmp_path / "lock.json", seed)
    restored = config_from_dict(config.public())
    assert restored.brief.limits.allowed_paths == ("prompt.md",)
    assert restored.verifier is not None
    assert restored.verifier.command[:2] == (sys.executable, str(ADAPTER))
    assert restored.preflight is not None
    assert restored.preflight.command[:3] == (sys.executable, str(ADAPTER), "preflight")
    assert restored.verifier.public_output_subdir == "."
    assert restored.commands[0].output_subdir == "."
    assert "不能只凭源码或总分" in restored.brief.instructions
    assert restored.metadata["full_benchmark_evaluation"] is True
    assert (
        restored.metadata["verification_identity"]["phases"]["baseline"]
        == restored.metadata["verification_identity"]["phases"]["final"]
    )
    identity = restored.metadata["verification_identity"]
    assert identity["adapter"] == "harness"
    assert identity["phases"]["search"]["manifest_id"] != identity["phases"]["final"]["manifest_id"]
    assert "expected" not in json.dumps(config.public())
    assert "api_key" not in json.dumps(identity)


def test_external_cli_is_independent_of_working_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "preflight" in result.stdout and "evaluate" in result.stdout


def test_benchmark_preflight_checks_target_and_judge_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = {
        "protocol_id": "fixed",
        "protocol": {
            "options": {
                "target_model": "target",
                "api_key_env": "FIXTURE_TARGET",
                "judge_api_key_env": "FIXTURE_JUDGE",
            }
        },
        "manifests": {split: [] for split in pb.PHASES.values()},
    }
    monkeypatch.setattr(pb, "load_lock", lambda _: lock)
    monkeypatch.setenv("FIXTURE_TARGET", "secret-target")
    monkeypatch.delenv("FIXTURE_JUDGE", raising=False)
    with pytest.raises(ValueError, match="credential"):
        pb.preflight(tmp_path / "lock.json")
    monkeypatch.setenv("FIXTURE_JUDGE", "secret-judge")
    result = pb.preflight(tmp_path / "lock.json")
    assert result["verification_identity"]["phases"]["search"] == pb.phase_identity(lock, "search")
    assert "secret" not in json.dumps(result)


def test_runtime_errors_are_not_task_scores(tmp_path: Path) -> None:
    trace = tmp_path / "episode.jsonl"
    trace.write_text(
        json.dumps({"type": "trace_start", "model": "fixed", "trace_id": "t"})
        + "\n"
        + json.dumps({"type": "trace_end", "trace_id": "t"})
        + "\n"
    )
    pb.check_traces(tmp_path, "fixed")
    with pytest.raises(ValueError, match="different target"):
        pb.check_traces(tmp_path, "other")
    with trace.open("a") as stream:
        stream.write(json.dumps({"type": "runtime_response", "status": "error"}) + "\n")
    with pytest.raises(ValueError, match="Runtime error"):
        pb.check_traces(tmp_path, "fixed")


@pytest.mark.parametrize("scope", ["prompt", "hermes"])
def test_evaluator_delivers_bound_result_to_research_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    from areal_rsi.api.research import TaskBrief, VerificationContract
    from areal_rsi.base.storage import write_json
    from areal_rsi.coordination.coordinator import Coordinator
    from areal_rsi.coordination.verification import read_trusted_result
    from areal_rsi.execution import JobRecord, JobSpec

    manifest, comparison = fixture()
    operator = options(tmp_path)
    operator["target_model"] = "fixed"
    lock = {
        "protocol_id": "protocol",
        "protocol": {"options": operator, "image_id": "sha256:image", "edit_scope": scope},
        "manifests": {split: [manifest] for split in pb.PHASES.values()},
    }
    monkeypatch.setattr(pb, "load_lock", lambda path: lock)
    monkeypatch.setenv(operator["api_key_env"], "target-secret-never-published")
    monkeypatch.setenv(operator["judge_api_key_env"], "judge-secret-never-published")
    calls: list[list[str]] = []

    def official(command: list[str], repository: Path, log_path: Path) -> None:
        calls.append(command)
        if "v2-report" in command:
            return
        assert "--compare-no-persistence" in command and "--sandbox" in command
        assert command[command.index("--runtime") + 1] == "local"
        if scope == "hermes":
            assert Path(command[2]).name == "harness_runtime.py"
            assert command[command.index("--") + 1] == "evolve"
        config = pb.read_json(Path(command[command.index("--config") + 1]))
        assert config["model"]["system_prompt_prefix"] == "General policy"
        trace = Path(command[command.index("--trace-dir") + 1])
        trace.mkdir()
        for variant in ("with_persistence", "without_persistence"):
            for index, row in enumerate(comparison[variant]["episodes"]):
                trace_id = f"{variant}-{index}"
                row["trace_id"] = trace_id
                events = [
                    {"type": "trace_start", "trace_id": trace_id, "model": "fixed"},
                    {"type": "runtime_request", "session_id": trace_id},
                    {"type": "trace_end", "trace_id": trace_id},
                ]
                (trace / f"{trace_id}.jsonl").write_text(
                    "\n".join(json.dumps(event) for event in events)
                )
        pb.write_json(trace / "sequence_comparison.json", comparison)
        if scope == "hermes":
            import hashlib

            source = Path(command[command.index("--harness") + 1])
            files = {
                p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob("*")
                if p.is_file()
            }
            receipt = {
                "source_digest": hashlib.sha256(
                    json.dumps(files, sort_keys=True).encode()
                ).hexdigest(),
                "loaded_entry": "hermes/run_agent.py",
            }
            Path(command[command.index("--receipts") + 1]).write_text(
                "\n".join(
                    json.dumps({**receipt, "session_id": row["trace_id"]})
                    for variant in ("with_persistence", "without_persistence")
                    for row in comparison[variant]["episodes"]
                )
            )

    monkeypatch.setattr(pb, "run_official", official)

    async def run() -> None:
        coordinator = Coordinator.create(
            tmp_path / "research",
            TaskBrief("Improve", "Fixed families", verification=VerificationContract(pb.METRIC)),
        )
        store = coordinator.store
        seed = tmp_path / "seed"
        seed.mkdir()
        (seed / "prompt.md").write_text("General policy")
        if scope == "hermes":
            for name in ("run_agent.py", "hermes_state.py", "tools/registry.py"):
                path = seed / "hermes" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# source fixture")
        candidate = await store.snapshot(seed, agent_id="operator", description="seed")
        input_root = store.root / "execution-inputs" / "job-one"
        write_json(input_root / "snapshot.json", candidate.content)
        workspace = input_root / "workspace"
        await store.content.materialize(candidate.content, workspace)
        output = store.root / "execution-outputs" / "job-one"
        result = pb.evaluate(tmp_path / "lock.json", workspace, output, "search")
        assert len(calls) == 2
        assert result["score"] == pytest.approx(0.6)
        assert not (output / "public").exists()
        assert "secret-never-published" not in (output / "evaluation.json").read_text()
        job = JobRecord(
            "job-one",
            JobSpec(
                ("trusted-evaluator",),
                cwd=str(workspace),
                metadata={
                    "candidate_id": candidate.candidate_id,
                    "candidate_digest": candidate.content.digest,
                    "contract_digest": store.contract_digest(),
                    "phase": "search",
                },
            ),
            "succeeded",
            1,
            2,
        )
        identity = {"adapter": "harness", "phases": {"search": pb.phase_identity(lock, "search")}}
        verified = read_trusted_result(store, job, candidate, "search", identity=identity)
        assert verified.status == "succeeded" and verified.score == result["score"]
        for field in ("harness_id", "protocol_id", "manifest_id", "phase", "target_model"):
            tampered = {**result, field: "tampered"}
            write_json(output / "evaluation.json", tampered)
            with pytest.raises(ValueError):
                read_trusted_result(store, job, candidate, "search", identity=identity)
        write_json(output / "evaluation.json", result)
        negative = {
            **result,
            "score": -0.1,
            "passed": False,
            "metrics": {**result["metrics"], pb.METRIC: -0.1},
        }
        write_json(output / "evaluation.json", negative)
        assert read_trusted_result(store, job, candidate, "search", identity=identity).score == -0.1
        write_json(output / "evaluation.json", result)
        (workspace / "prompt.md").write_text("Changed after the evaluation")
        with pytest.raises(ValueError, match="differ from the frozen"):
            read_trusted_result(store, job, candidate, "search", identity=identity)

    asyncio.run(run())
