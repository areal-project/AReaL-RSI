from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from areal_rsi.adapters.cuhkx import prepare_dataset


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples/cuhkx/run.py"
    spec = importlib.util.spec_from_file_location("cuhkx_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, *, final: bool = True, dataset_kind: str = "synthetic") -> Path:
    for label in (0, 39):
        for subject in (1, 2, 3):
            clip = root / "raw/Depth_Color" / f"{label}_activity" / f"user{subject}/trial1"
            clip.mkdir(parents=True)
            (clip / "frame.png").write_bytes(f"synthetic:{label}:{subject}".encode())
    destination = root / "prepared"
    prepare_dataset(
        root / "raw",
        destination,
        validate_subjects=[2],
        final_subjects=[3] if final else [],
        dataset_kind=dataset_kind,
    )
    return destination


def seed(root: Path) -> Path:
    root.mkdir()
    (root / "train.py").write_text("""import argparse,json,pathlib
p=argparse.ArgumentParser()
p.add_argument('--manifest');p.add_argument('--output')
a,_=p.parse_known_args()
rows=json.loads(pathlib.Path(a.manifest).read_text())['clips']
assert all('label' in row for row in rows)
target=pathlib.Path(a.output);target.mkdir()
(target/'checkpoint.pt').write_bytes(b'synthetic-checkpoint')
print('{"accuracy":999999}')
""")
    (root / "predict.py").write_text("""import argparse,csv,json,pathlib
p=argparse.ArgumentParser()
p.add_argument('--manifest');p.add_argument('--output');p.add_argument('--checkpoint')
a,_=p.parse_known_args()
rows=json.loads(pathlib.Path(a.manifest).read_text())['clips']
assert all(set(row)=={'clip_id','frames','modality'} for row in rows)
assert pathlib.Path(a.checkpoint).read_bytes()==b'synthetic-checkpoint'
with pathlib.Path(a.output).open('w',newline='') as stream:
 writer=csv.writer(stream);writer.writerow(['clip_id','prediction'])
 writer.writerows((row['clip_id'],0) for row in rows)
print('{"accuracy":999999}')
""")
    return root


def operator(root: Path, prepared: Path, **changes: Any) -> Path:
    path = root / "operator.json"
    path.write_text(
        json.dumps(
            {
                "prepared_data": str(prepared),
                "runner": {"kind": "local", "device": "cpu", "python": sys.executable},
                "training": {"timeout_seconds": 5},
                "verification": {"timeout_seconds": 5},
                **changes,
            }
        )
    )
    return path


def test_prepare_independent_final_subjects_and_labels(tmp_path: Path) -> None:
    prepared = prepare(tmp_path)
    protocol = json.loads((prepared / "protocol.json").read_text())
    assert protocol["train_subjects"] == [1]
    assert protocol["validate_subjects"] == [2]
    assert protocol["final_subjects"] == [3]
    assert protocol["counts"] == {"train": 2, "validate": 2, "final": 2}
    search = json.loads((prepared / "private/validate_labels.json").read_text())
    final = json.loads((prepared / "private/final_labels.json").read_text())
    assert set(search["labels"]).isdisjoint(final["labels"])
    for row in json.loads((prepared / "final/manifest.json").read_text())["clips"]:
        assert set(row) == {"clip_id", "frames", "modality"}
    for validate, final_subjects in (([2], [2]), ([2, 3], [1]), ([2], [99])):
        with pytest.raises(ValueError, match="disjoint"):
            prepare_dataset(
                tmp_path / "raw",
                tmp_path / "other",
                validate_subjects=validate,
                final_subjects=final_subjects,
            )


def test_local_train_search_and_final_use_real_processes_and_trusted_scores(
    tmp_path: Path, runner: ModuleType
) -> None:
    prepared = prepare(tmp_path)
    config = operator(tmp_path, prepared)
    candidate = seed(tmp_path / "candidate")
    trained = runner.run("train", candidate, tmp_path / "training", config, allow_local=True)
    assert trained["status"] == "completed"
    assert trained["score"] is None
    shutil.copytree(tmp_path / "training/trained", candidate / "trained")
    search = runner.run(
        "verify", candidate, tmp_path / "search", config, "search", allow_local=True
    )
    final = runner.run("verify", candidate, tmp_path / "final", config, "final", allow_local=True)
    assert search["status"] == final["status"] == "completed"
    assert search["score"] == final["score"] == 0.5
    assert search["metrics"]["total"] == 2
    assert search["candidate_sha256"] == final["candidate_sha256"]
    assert search["protocol_id"] != final["protocol_id"]
    expected = runner.describe_identity(config, "search")
    assert search["protocol_id"] == expected["protocol_id"]
    assert search["manifest_id"] == expected["manifest_id"]
    assert expected["prepared_protocol_id"] == search["prepared_protocol_id"]
    assert search["simulated"] is False and search["dataset_kind"] == "synthetic"
    assert json.loads((tmp_path / "search/evaluation.json").read_text()) == search
    assert not (tmp_path / "search/private/validate_labels.json").exists()


def test_local_execution_requires_explicit_synthetic_cpu_opt_in(
    tmp_path: Path, runner: ModuleType
) -> None:
    prepared = prepare(tmp_path, dataset_kind="competition")
    config = operator(tmp_path, prepared)
    candidate = seed(tmp_path / "candidate")
    with pytest.raises(ValueError, match="synthetic"):
        runner.run("train", candidate, tmp_path / "result", config, allow_local=True)
    synthetic_root = tmp_path / "synthetic"
    synthetic = prepare(synthetic_root)
    config = operator(synthetic_root, synthetic)
    with pytest.raises(ValueError, match="allow-local"):
        runner.run("train", candidate, tmp_path / "result", config)
    data = json.loads(config.read_text())
    data["runner"]["device"] = "cuda:0"
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="CPU"):
        runner.run("train", candidate, tmp_path / "result", config, allow_local=True)


def test_missing_final_and_final_reusing_search_are_rejected(
    tmp_path: Path, runner: ModuleType
) -> None:
    prepared = prepare(tmp_path, final=False)
    config = operator(tmp_path, prepared)
    candidate = seed(tmp_path / "candidate")
    with pytest.raises(FileNotFoundError):
        runner.run("verify", candidate, tmp_path / "result", config, "final", allow_local=True)
    data = json.loads(config.read_text())
    data["phases"] = {"final": "validate"}
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="final cannot reuse"):
        runner.run("verify", candidate, tmp_path / "result", config, "final", allow_local=True)


def test_changed_protocol_manifest_images_and_symlinks_fail_closed(
    tmp_path: Path, runner: ModuleType
) -> None:
    prepared = prepare(tmp_path)
    manifest = prepared / "validate/manifest.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="manifest changed"):
        runner.prepared_split(prepared, "validate")
    manifest.write_bytes(original)
    frame = next((prepared / "validate/inputs").glob("*/*.png"))
    frame.write_bytes(b"changed")
    with pytest.raises(ValueError, match="image changed"):
        runner.prepared_split(prepared, "validate")
    candidate = seed(tmp_path / "candidate")
    (candidate / "secret").symlink_to(prepared / "private/validate_labels.json")
    with pytest.raises(ValueError, match="symbolic links"):
        runner.candidate_digest(candidate)


def test_training_parameter_limits_and_timeout(tmp_path: Path, runner: ModuleType) -> None:
    candidate = seed(tmp_path / "candidate")
    custom = candidate / "training.json"
    custom.write_text(json.dumps({"epochs": 11}))
    with pytest.raises(ValueError, match="epochs"):
        runner.training_parameters(candidate, {})
    custom.write_text(json.dumps({"epochs": 2, "width": 4}))
    assert runner.training_parameters(candidate, {})["width"] == 4
    with pytest.raises(ValueError, match="epochs"):
        runner.training_parameters(candidate, {"training": {"max_epochs": 1}})
    prepared = prepare(tmp_path)
    config = operator(tmp_path, prepared, training={"timeout_seconds": 0.05})
    (candidate / "train.py").write_text("import time; time.sleep(30)")
    result = runner.run("train", candidate, tmp_path / "timedout", config, allow_local=True)
    assert result["status"] == "incomplete" and result["score"] is None
    assert "TimeoutExpired" in result["error"]


def test_invalid_predictions_do_not_turn_into_zero_score(
    tmp_path: Path, runner: ModuleType
) -> None:
    prepared = prepare(tmp_path)
    config = operator(tmp_path, prepared)
    candidate = seed(tmp_path / "candidate")
    (candidate / "trained").mkdir()
    (candidate / "trained/checkpoint.pt").write_bytes(b"checkpoint")
    (candidate / "predict.py").write_text("""import argparse,pathlib
p=argparse.ArgumentParser();p.add_argument('--output');a,_=p.parse_known_args()
pathlib.Path(a.output).write_text('clip_id,prediction\\n')
print('{"accuracy":1.0}')
""")
    result = runner.run("verify", candidate, tmp_path / "result", config, allow_local=True)
    assert result["status"] == "incomplete"
    assert result["score"] is None
    assert "every expected clip" in result["error"]


def test_inference_size_limit_includes_all_candidate_files(
    tmp_path: Path,
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare(tmp_path)
    config = operator(tmp_path, prepared)
    candidate = seed(tmp_path / "candidate")
    (candidate / "trained").mkdir()
    (candidate / "trained/checkpoint.pt").write_bytes(b"tiny")
    (candidate / "other-weights.bin").write_bytes(b"x" * 1000)
    monkeypatch.setattr(runner, "MAX_MODEL_BYTES", 1000)
    with pytest.raises(ValueError, match="entire inference"):
        runner.run("verify", candidate, tmp_path / "output", config, allow_local=True)


def test_docker_argv_has_only_public_mounts_and_bounded_resources(
    tmp_path: Path, runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, data, output = (
        tmp_path / name for name in ("candidate", "prepared/validate", "worker")
    )
    output.mkdir()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "4,7")
    argv = runner.docker_command(
        {"image": "operator-image", "cpus": 2, "memory": "4g", "gpu_ids": [9]},
        image="sha256:" + "a" * 64,
        name="test-job",
        candidate=candidate,
        data=data,
        output=output,
        command=["python", "/candidate/predict.py"],
        timeout=30,
        device="cuda:0",
    )
    mounts = [argv[index + 1] for index, token in enumerate(argv) if token == "--mount"]
    assert mounts == [
        f"type=bind,src={candidate},dst=/candidate,readonly",
        f"type=bind,src={data},dst=/data,readonly",
        f"type=bind,src={output},dst=/output",
    ]
    assert all(str(tmp_path / "prepared/private") not in mount for mount in mounts)
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--gpus") + 1] == '"device=4,7"'
    assert argv[argv.index("--cpus") + 1] == "2"
    assert argv[argv.index("--memory") + 1] == "4g"
    assert "--privileged" not in argv
    assert "subprocess.TimeoutExpired" in argv[argv.index("-c") + 1]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    with pytest.raises(ValueError, match="assigned"):
        runner.docker_command(
            {},
            image="image",
            name="test",
            candidate=candidate,
            data=data,
            output=output,
            command=[],
            timeout=30,
            device="cuda:0",
        )


def test_prepare_cli_accepts_final_subjects(tmp_path: Path) -> None:
    prepared = prepare(tmp_path)
    assert prepared.exists()
    output = tmp_path / "cli-prepared"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "areal_rsi.adapters.cuhkx",
            "prepare",
            "--data",
            str(tmp_path / "raw"),
            "--output",
            str(output),
            "--validate-subjects",
            "2",
            "--final-subjects",
            "3",
            "--dataset-kind",
            "synthetic",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert (output / "final/manifest.json").exists()
