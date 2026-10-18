import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest

from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from examples.search.evaluation import publish as output
from examples.search.evaluation.artifacts import FinalEvalConfig


def state_for(tmp_path: Path) -> output.OutputState:
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    config = FinalEvalConfig(
        benchmark_repository="/inputs/benchmark",
        historical_environment="/inputs/history",
        data_root="/inputs/data",
        model="/inputs/model",
        base_model="/inputs/base",
        embedding_model="/inputs/embed",
        judge_model="/inputs/judge",
        checkpoint_manifest="/inputs/checkpoint",
        checkpoint_manifest_sha256="checkpoint-sha",
        asset_lock="/inputs/lock",
        asset_lock_sha256="lock-sha",
        data_python="/inputs/python",
        vllm_python="/inputs/vllm",
        qwen_overlay="/inputs/overlay",
        output=str(shared),
        seed=42,
    )
    config_path = tmp_path / "input-config.json"
    write_record(config_path, config)
    write_record(local / "config.json", config)
    return output.OutputState(
        "123", str(config_path), file_ref(config_path).digest, str(local), str(shared), "running"
    )


@pytest.mark.parametrize(
    "entrypoint_path",
    ["examples/search/evaluation/official.py", "examples/search/final_evaluation.py"],
)
def test_publication_launches_the_requested_frozen_runtime_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint_path: str
) -> None:
    state = state_for(tmp_path)
    config_path = Path(state.config)
    destination = tmp_path / "published/trial"
    destination.parent.mkdir()
    write_record(
        config_path, replace(read_record(config_path, FinalEvalConfig), output=str(destination))
    )
    frozen = tmp_path / "frozen-runtime"
    entrypoint = frozen / entrypoint_path
    entrypoint.parent.mkdir(parents=True)
    source = b"# Immutable frozen evaluation entrypoint.\n"
    entrypoint.write_bytes(source)
    image = tmp_path / "runtime.sif"
    image.touch()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv("SEARCH_SLURM_JOB_INFO", "JobId=123 Reservation=rsi")

    class LaunchObserved(RuntimeError):
        pass

    def inspect_launch(command: list[str], **kwargs: object) -> NoReturn:
        script = Path(command[-1]).read_text()
        assert str(entrypoint) in script
        assert entrypoint.read_bytes() == source
        raise LaunchObserved

    monkeypatch.setattr(subprocess, "Popen", inspect_launch)
    with pytest.raises(LaunchObserved):
        output.run(config_path, frozen, image, scratch)


def test_mirror_waits_for_complete_solver_json_and_withholds_final_scores(tmp_path: Path) -> None:
    state = state_for(tmp_path)
    source, shared = Path(state.local_output), Path(state.shared_output)
    cases = source / "runs/model/trial"
    cases.mkdir(parents=True)
    valid = b'{"query_id":"1","text":"exact bytes"}\n'
    (cases / "run_1.json").write_bytes(valid)
    (cases / "run_2.json").write_bytes(b'{"query_id":')
    (source / "result.json").write_text('{"questions":830}')
    (source / "evals").mkdir()
    (source / "evals/judge.json").write_text('{"correct":true}')
    copied: set[str] = set()
    first = output.mirror_cases(state, copied)
    assert first.copied_files == first.deferred_files == 1
    assert (shared / "runs/model/trial/run_1.json").read_bytes() == valid
    assert not (shared / "runs/model/trial/run_2.json").exists()
    assert not (shared / "result.json").exists() and not (shared / "evals").exists()
    (cases / "run_2.json").write_text('{"query_id":"2"}')
    second = output.mirror_cases(state, copied)
    assert second.copied_files == 2 and second.deferred_files == 0


def test_shared_enospc_does_not_damage_local_or_previous_valid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = state_for(tmp_path)
    source = Path(state.local_output) / "runs/model/trial/run_1.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"query_id":"1","new":true}')
    destination = Path(state.shared_output) / source.relative_to(state.local_output)
    destination.parent.mkdir(parents=True)
    destination.write_text('{"query_id":"1","previous":true}')
    previous = destination.read_bytes()

    def no_space(fd: int) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", no_space)
        progress = output.mirror_cases(state, set())
    assert progress.deferred_files == 1 and destination.read_bytes() == previous
    assert source.read_text() == '{"query_id":"1","new":true}'
    output.mirror_cases(state, set())
    assert destination.read_bytes() == source.read_bytes()


def test_publication_recovers_from_shared_failure_without_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = replace(state_for(tmp_path), phase="inference-complete", return_code=0)
    source, shared = Path(state.local_output), Path(state.shared_output)
    (source / "evals").mkdir()
    (source / "evals/judge.json").write_text('{"complete":true}')
    (source / "solver-cost.json").write_text('{"input_tokens":123}')
    (source / "result.json").write_text('{"questions":830,"correct":125}')
    original = output.copy_bytes

    def unavailable(content: bytes, destination: Path) -> None:
        if destination.name == "judge.json":
            raise OSError(28, "No space left on device")
        original(content, destination)

    with monkeypatch.context() as patch:
        patch.setattr(output, "copy_bytes", unavailable)
        with pytest.raises(OSError):
            output.publish(state)
    assert not (shared / "result.json").exists()
    source_hashes = {
        str(p.relative_to(source)): file_ref(p).digest for p in source.rglob("*") if p.is_file()
    }
    output.publish(state)
    assert all(file_ref(shared / name).digest == digest for name, digest in source_hashes.items())
    assert read_record(shared / "output-execution.json", output.OutputState).phase == "published"
    output.publish(state)  # CPU-only recovery is idempotent with these exact inputs.


def test_failed_inference_cannot_publish_even_if_result_file_exists(tmp_path: Path) -> None:
    state = replace(state_for(tmp_path), phase="inference-failed", return_code=1)
    (Path(state.local_output) / "result.json").write_text('{"questions":830}')
    with pytest.raises(ValueError, match="successfully completed"):
        output.publish(state)
    assert not Path(state.shared_output).exists()
