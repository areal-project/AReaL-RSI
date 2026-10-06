"""Behavioral checks for source confinement and independently graded bench rewards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from areal_rsi.api.control import WorkspaceOperation, WorkspaceRequest, read_record
from areal_rsi.infra.coordination.evaluation.bench import (
    AdaptationBenchWorkflow,
    BenchEnvironmentError,
    BenchInputs,
    BenchTask,
    RuntimeBinding,
    freeze_inputs,
    grade_candidate,
)
from areal_rsi.infra.coordination.tools.workspace import perform


def test_workspace_edits_candidate_and_rejects_external_paths(tmp_path: Path) -> None:
    colleague = tmp_path / "colleague"
    colleague.mkdir()
    protected = colleague / "launch.sh"
    protected.write_text("unchanged")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    perform(candidate, WorkspaceRequest(WorkspaceOperation.WRITE, "model.py", "value = 1\n"))
    perform(
        candidate,
        WorkspaceRequest(WorkspaceOperation.REPLACE, "model.py", "value = 2", "value = 1"),
    )
    assert perform(candidate, WorkspaceRequest(WorkspaceOperation.CHECK, "model.py"))
    assert (candidate / "model.py").read_text() == "value = 2\n"
    with pytest.raises(ValueError, match="escapes"):
        perform(
            candidate, WorkspaceRequest(WorkspaceOperation.WRITE, "../colleague/launch.sh", "bad")
        )
    (candidate / "external").symlink_to(colleague, target_is_directory=True)
    with pytest.raises(ValueError):
        perform(candidate, WorkspaceRequest(WorkspaceOperation.WRITE, "external/launch.sh", "bad"))
    assert protected.read_text() == "unchanged"


@pytest.mark.parametrize("task_count", [4, 6])
def test_overfit_freezes_selected_tasks_and_refuses_changed_input(
    tmp_path: Path, task_count: int
) -> None:
    dataset = tmp_path / "tasks.jsonl"
    rows = [
        {
            "id": f"model/T0{index}@1",
            "model_id": "model",
            "task_id": f"T0{index}",
            "task_revision": "v1",
            "instruction": f"Implement task {index}",
            "content_sha256": str(index) * 64,
        }
        for index in range(1, task_count + 1)
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows))
    destination = tmp_path / "frozen"
    inputs = freeze_inputs(dataset, destination)
    assert inputs.train_task_keys == inputs.test_task_keys
    assert len(inputs.tasks) == task_count
    assert read_record(destination / "inputs.json", BenchInputs) == inputs
    rows[0]["instruction"] = "Different requirement"
    dataset.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="changed"):
        freeze_inputs(dataset, destination)


def test_workflow_uses_each_tasks_baseline_and_keeps_originals_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from areal_rsi.infra.coordination.evaluation import bench as adaptation_bench

    baselines = []
    for value in ("first", "second"):
        baseline = tmp_path / value
        baseline.mkdir()
        (baseline / "model.py").write_text(value)
        baselines.append(baseline)
    bench_root = tmp_path / "bench"
    fixture = bench_root / "fixtures/tokens.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"input_ids": [1, 2], "loss_mask": [0, 1]}')
    rows = [
        {
            "id": f"model/T0{index}@1",
            "model_id": "model",
            "task_id": f"T0{index}",
            "task_revision": "v1",
            "instruction": f"Implement task {index}",
            "content_sha256": str(index) * 64,
            "source_files": {},
            "datasets": {
                "numeric": {
                    "dataset": {
                        "source": {
                            "path": "fixtures/tokens.json",
                            "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                        }
                    }
                }
            },
        }
        for index in range(1, 6)
    ]
    dataset = tmp_path / "tasks.jsonl"
    dataset.write_text("\n".join(json.dumps(row) for row in rows))
    inputs = freeze_inputs(dataset, tmp_path / "inputs")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "task_key": task.task_key,
                    "runtime_path": str(tmp_path / "runtime.json"),
                    "baseline_root": str(baselines[index % 2]),
                }
                for index, task in enumerate(inputs.tasks)
            ]
        )
    )
    seen = []

    def run_agent(*args: object, **kwargs: object) -> bytes:
        tool = args[3]
        assert isinstance(tool, adaptation_bench.McpTool)
        candidate = Path(tool.arguments[-1])
        assert (
            candidate / "benchmark-inputs/fixtures/tokens.json"
        ).read_bytes() == fixture.read_bytes()
        seen.append((candidate / "model.py").read_text())
        (candidate / "model.py").write_text("edited")
        return b"transcript"

    monkeypatch.setattr(adaptation_bench, "run_claude", run_agent)
    monkeypatch.setattr(adaptation_bench, "read_result", lambda raw: {"completed": True})
    monkeypatch.setattr(
        adaptation_bench,
        "grade_candidate",
        lambda *args: adaptation_bench.BenchGrade(
            0, False, "FAIL", "fresh_evaluation", "x", "a" * 64
        ),
    )
    workflow = AdaptationBenchWorkflow(
        inputs_root=str(tmp_path / "inputs"),
        runtime_registry=str(registry),
        bench_root=str(bench_root),
        run_root=str(tmp_path / "runs"),
        claude_cli="/usr/bin/claude",
        model="model",
        harness_prompt="Implement the task",
    )
    for task in inputs.tasks[:2]:
        assert workflow._run(task, "http://localhost", "session-key") == 0.0
    assert seen == ["first", "second"]
    assert [(p / "model.py").read_text() for p in baselines] == ["first", "second"]


@pytest.mark.parametrize(
    "status,scope",
    [
        ("ENVIRONMENT_ERROR", "fresh_evaluation"),
        ("PASS", "evaluated_artifacts"),
    ],
)
def test_environment_or_artifact_only_results_cannot_be_training_rewards(
    tmp_path: Path, status: str, scope: str
) -> None:
    bench = tmp_path / "bench"
    bench.mkdir()
    grade = {
        "score": 0 if status == "ENVIRONMENT_ERROR" else 1,
        "status": status,
        "scope": scope,
        "verified": status != "ENVIRONMENT_ERROR",
        "reason": "site unavailable",
        "collector_sha256": "a" * 64,
        "task_content_sha256": "b" * 64,
    }
    (bench / "evaluate.py").write_text(
        "import sys,json\n"
        "from pathlib import Path\n"
        f"result=json.loads({json.dumps(json.dumps(grade))})\n"
        "Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps(result))\n"
    )
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"timeout_seconds": 10}')
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    task = BenchTask("model/T01@1", "model", "T01", "v1", "Implement", "b" * 64)
    with pytest.raises(BenchEnvironmentError):
        grade_candidate(
            bench,
            task,
            candidate,
            RuntimeBinding(task.task_key, str(runtime), str(candidate)),
            tmp_path / "grade.json",
        )


@pytest.mark.parametrize("score", [0, 1])
def test_fresh_independent_pass_and_fail_become_binary_rewards(tmp_path: Path, score: int) -> None:
    bench = tmp_path / "bench"
    bench.mkdir()
    result = {
        "score": score,
        "status": "PASS" if score else "FAIL",
        "scope": "fresh_evaluation",
        "verified": bool(score),
        "collector_sha256": "a" * 64,
        "task_content_sha256": "b" * 64,
    }
    (bench / "evaluate.py").write_text(
        "import sys,json\nfrom pathlib import Path\n"
        f"result=json.loads({json.dumps(json.dumps(result))})\n"
        "Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps(result))\n"
        "sys.exit(0 if result['score'] else 1)\n"
    )
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"timeout_seconds": 10}')
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    task = BenchTask("model/T01@1", "model", "T01", "v1", "Implement", "b" * 64)
    grade = grade_candidate(
        bench,
        task,
        candidate,
        RuntimeBinding(task.task_key, str(runtime), str(candidate)),
        tmp_path / "grade.json",
    )
    assert grade.score == score
    assert grade.verified is bool(score)
    assert grade.result_sha256


@pytest.mark.parametrize("score,tamper", [(0, False), (1, False), (1, True)])
def test_native_task_runner_uses_directory_output_and_checks_fresh_evidence(
    tmp_path: Path, score: int, tamper: bool
) -> None:
    bench = tmp_path / "bench"
    runner = bench / "tasks/partial-rollout/run.py"
    runner.parent.mkdir(parents=True)
    task = BenchTask(
        "partial-rollout@1",
        "qwen25-partial-rollout",
        "T01",
        "qwen25-partial-rollout-v1",
        "Implement",
        "b" * 64,
    )
    # Execute a separate task process using the public Bench CLI and result schema.
    runner.write_text(
        "import argparse,hashlib,json,sys\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "for name in ('code','environment','output'): p.add_argument('--'+name,required=True)\n"
        "a=p.parse_args(); out=Path(a.output); out.mkdir()\n"
        "assert Path(a.code).is_dir() and Path(a.environment).is_file()\n"
        "evidence={'run_id':'fresh-id','candidate_sha256':'c'*64}\n"
        "hashes={}\n"
        "for name in ('observations.json','reference.json'):\n"
        " path=out/name; path.write_text(json.dumps(evidence)); "
        "hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()\n"
        f"result={{'score':{score},'verified':bool({score}),"
        f"'status':{'PASS' if score else 'FAIL'!r},"
        "'scope':'fresh_evaluation','task_name':'partial-rollout',"
        "'model_id':'qwen25-partial-rollout','task_id':'T01',"
        "'task_revision':'qwen25-partial-rollout-v1','task_content_sha256':'b'*64,"
        "'artifacts_dir':str(out.resolve()),'evidence_sha256':hashes,**evidence}\n"
        "(out/'result.json').write_text(json.dumps(result))\n"
        + ("(out/'observations.json').write_text('{}')\n" if tamper else "")
        + f"sys.exit(0 if {score} else 1)\n"
    )
    environment = tmp_path / "environment.json"
    environment.write_text("{}")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    binding = RuntimeBinding(
        task.task_key,
        None,
        str(candidate),
        environment_path=str(environment),
        task_name="partial-rollout",
        runner_path="tasks/partial-rollout/run.py",
        runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
    )
    output = tmp_path / "grade.json"
    if tamper:
        with pytest.raises(BenchEnvironmentError, match="evidence changed"):
            grade_candidate(bench, task, candidate, binding, output)
    else:
        grade = grade_candidate(bench, task, candidate, binding, output)
        assert grade.score == score
        assert grade.result_path == str(tmp_path / "grade.artifacts/result.json")
        assert not output.exists()
