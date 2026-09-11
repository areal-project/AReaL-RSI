"""Public 3D fixtures: split plumbing, real sandbox execution and infra failures."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from areal_rsi.api.base import (
    BenchmarkSplit,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
    Task,
    TaskResult,
    TrajectoryRef,
    VerificationRequest,
    VerificationStatus,
    VerifierSpec,
)
from areal_rsi.base.storage import FileContentStore, now
from areal_rsi.framework.verifier import VerifierExecutor

with patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]):
    adapter = importlib.import_module("examples.3d_bench.adapter")
    environment_module = importlib.import_module("examples.3d_bench.environment")


def test_dataset_supports_both_splits_and_tracks_content_identity(tmp_path: Path) -> None:
    for name in ("model-001", "002-example"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "TARGET.md").write_text(f"Build {name}")
    dataset = adapter.ThreeModelBenchmark(tmp_path)
    train = asyncio.run(dataset.list_tasks(BenchmarkSplit.TRAIN))
    validate = asyncio.run(dataset.list_tasks(BenchmarkSplit.VALIDATE))
    assert [task.task_id for task in train] == ["MODEL-001", "MODEL-002"]
    assert validate == train
    train[0].input = "changed copy"
    assert asyncio.run(dataset.list_tasks(BenchmarkSplit.TRAIN)) == validate
    assert len(adapter.load_tasks(tmp_path, task_ids=("MODEL-001",))) == 1
    (tmp_path / "model-001/TARGET.md").write_text("New target")
    assert adapter.ThreeModelBenchmark(tmp_path).name != dataset.name
    runner = importlib.import_module("examples.3d_bench.run_local")
    assert runner.ThreeModelBenchmark is adapter.ThreeModelBenchmark
    assert runner.ThreeModelVerifier is adapter.ThreeModelVerifier


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    if sys.platform != "darwin" or not shutil.which("node"):
        pytest.skip("Real 3D sandbox checks require macOS and Node.js")
    template = tmp_path / "template"
    prepared = tmp_path / "prepared"
    for name in environment_module.PUBLIC_FILES:
        path = template / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    (template / "package.json").write_text('{"type":"module"}')
    (template / "src/model.js").write_text("export function createModel(){return true}")
    (template / "scripts").mkdir()
    (template / "scripts/check-model.mjs").write_text(
        "import {readFileSync} from 'node:fs';\n"
        "import {createHash} from 'node:crypto';\n"
        "const source=readFileSync(new URL('../src/model.js',import.meta.url));\n"
        "const {createModel}=await import('../src/model.js');\n"
        "if(createModel()!==true) process.exit(1);\n"
        "console.log(JSON.stringify({ok:true,sourceSha256:"
        "createHash('sha256').update(source).digest('hex')}));\n"
    )
    files = {
        path.relative_to(template).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(template.rglob("*"))
        if path.is_file()
    }
    (tmp_path / "snapshot.json").write_text(json.dumps({"commit": "fixture", "files": files}))
    monkeypatch.setattr(environment_module, "SNAPSHOT_COMMIT", "fixture")
    monkeypatch.setattr(
        environment_module,
        "SNAPSHOT_DIGEST",
        hashlib.sha256(json.dumps(files, separators=(",", ":")).encode()).hexdigest(),
    )
    shutil.copytree(template, prepared)
    for name in ("three/package.json", "vite/bin/vite.js", ".bin/vite"):
        path = prepared / "node_modules" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if name.endswith("json") else "console.log('fixture build')")
    return environment_module.ModelEnvironment(template, prepared)


async def request(content: FileContentStore, source: str) -> VerificationRequest:
    ref = await content.put_text("fixture")
    solution = SolutionVersion(
        "solution",
        ModelVersion("model", "fixture", ref, now()),
        HarnessVersion("harness", "fixture", ref, now()),
        now(),
    )
    return VerificationRequest(
        Task("MODEL-001", "fixture"),
        solution,
        TaskResult(
            await content.put_text(source, media_type="text/javascript"),
            TrajectoryRef("trajectory"),
        ),
        VerifierSpec("contract-v2", "fixture"),
    )


def test_shared_verifier_runs_in_sandbox(tmp_path: Path, environment: Any) -> None:
    async def check() -> None:
        content = FileContentStore(tmp_path / "content")
        verifier = adapter.ThreeModelVerifier(
            tmp_path / "verification", content, environment=environment
        )
        good = await VerifierExecutor().verify(
            verifier, await request(content, "export function createModel(){return true}")
        )
        assert good.status == VerificationStatus.SUCCEEDED and good.passed
        marker = tmp_path.resolve() / "outside-evaluation.txt"
        source = (
            "import {writeFileSync} from 'node:fs';"
            f"writeFileSync({json.dumps(str(marker))},'escaped');"
            "export function createModel(){return true}"
        )
        rejected = await VerifierExecutor().verify(verifier, await request(content, source))
        assert rejected.status == VerificationStatus.SUCCEEDED and rejected.passed is False
        assert not marker.exists()

    asyncio.run(check())


@pytest.mark.parametrize("missing", ["checker", "dependency"])
def test_missing_infrastructure_is_not_a_model_score(
    tmp_path: Path, environment: Any, missing: str
) -> None:
    path = (
        (environment.template / "scripts/check-model.mjs")
        if missing == "checker"
        else (environment.prepared / "node_modules/three/package.json")
    )
    path.unlink()

    async def check() -> None:
        content = FileContentStore(tmp_path / "content")
        verifier = adapter.ThreeModelVerifier(
            tmp_path / "verification", content, environment=environment
        )
        result = await VerifierExecutor().verify(
            verifier, await request(content, "export function createModel(){return true}")
        )
        assert result.status == VerificationStatus.INFRA_ERROR
        assert result.score is None and result.passed is None

    asyncio.run(check())
