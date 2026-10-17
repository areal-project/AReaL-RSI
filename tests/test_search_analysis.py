"""Paired statistical inference and failure accounting for the official benchmark."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from areal_rsi.api.records import DirectorySnapshot
from areal_rsi.infra.artifacts import read_record, snapshot_directory
from examples.search.analysis import paired_statistics, read_official_results, wilson_interval
from examples.search.final_evaluation import counters, export_checkpoint


def test_hf5_export_preserves_weights_and_restores_legacy_position_metadata(tmp_path: Path) -> None:
    source = tmp_path / "trained"
    source.mkdir()
    parameters = {
        "rope_type": "yarn",
        "rope_theta": 1000000,
        "factor": 4.0,
        "original_max_position_embeddings": 32768,
    }
    configuration = {"model_type": "qwen3", "rope_parameters": parameters, "dtype": "bfloat16"}
    original = json.dumps(configuration)
    (source / "config.json").write_text(original)
    (source / "model.safetensors").write_bytes(b"unchanged checkpoint payload")
    source_manifest = tmp_path / "trained.json"
    reference = snapshot_directory(source, source_manifest)
    output = tmp_path / "inference"
    manifest = export_checkpoint(source_manifest, output, source_sha256=reference.digest)
    exported = json.loads((output / "config.json").read_text())
    assert exported["rope_theta"] == 1000000
    assert exported["torch_dtype"] == "bfloat16"
    assert exported["rope_parameters"] == parameters
    assert exported["rope_scaling"] == {
        key: value for key, value in parameters.items() if key != "rope_theta"
    }
    assert (source / "config.json").read_text() == original
    assert (output / "model.safetensors").read_bytes() == (
        source / "model.safetensors"
    ).read_bytes()
    before = read_record(source_manifest, DirectorySnapshot)
    after = read_record(manifest, DirectorySnapshot)
    assert next(x.sha256 for x in before.files if x.path == "model.safetensors") == next(
        x.sha256 for x in after.files if x.path == "model.safetensors"
    )
    assert export_checkpoint(source_manifest, output, source_sha256=reference.digest) == manifest
    (output / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="Checkpoint file changed"):
        export_checkpoint(source_manifest, output, source_sha256=reference.digest)


def test_hf5_export_rejects_conflicting_legacy_theta(tmp_path: Path) -> None:
    source = tmp_path / "trained"
    source.mkdir()
    (source / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3",
                "dtype": "bfloat16",
                "rope_theta": 10000,
                "rope_parameters": {"rope_type": "yarn", "rope_theta": 1000000, "factor": 4.0},
            }
        )
    )
    (source / "model.safetensors").write_bytes(b"original weights")
    manifest = tmp_path / "trained.json"
    reference = snapshot_directory(source, manifest)
    output = tmp_path / "inference"
    with pytest.raises(ValueError, match="Conflicting modern and legacy RoPE"):
        export_checkpoint(manifest, output, source_sha256=reference.digest)
    assert not output.exists()


def test_paired_bootstrap_preserves_constant_within_question_gain() -> None:
    baseline = {str(index): False for index in range(12)}
    result = paired_statistics(baseline, dict.fromkeys(baseline, True), repetitions=1000)
    assert result.candidate_wins == 12 and result.candidate_losses == 0
    assert result.delta_bootstrap_95_percentage_points == (100.0, 100.0)
    assert result.exact_mcnemar_p == 1 / 2048
    unchanged = paired_statistics(baseline, baseline, repetitions=1000)
    assert unchanged.delta_bootstrap_95_percentage_points == (0.0, 0.0)
    assert unchanged.exact_mcnemar_p == 1.0
    with pytest.raises(ValueError, match="same nonempty"):
        paired_statistics(baseline, {"different": False}, repetitions=1000)
    low, high = wilson_interval(79, 830)
    assert low < 79 / 830 < high


def test_official_judge_failures_and_missing_queries_cannot_be_hidden(tmp_path: Path) -> None:
    path = tmp_path / "one_eval.json"
    judge = {"correct": False, "parse_error": False}
    record = {
        "query_id": "one",
        "is_completed": True,
        "judge_response": "correct: no",
        "judge_result": judge,
    }
    path.write_text(json.dumps(record))
    assert read_official_results(tmp_path, {"one"}) == {"one": False}
    with pytest.raises(ValueError, match="missing or unexpected"):
        read_official_results(tmp_path, {"one", "two"})
    judge["parse_error"] = True
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Judge failed"):
        read_official_results(tmp_path, {"one"})
    record["is_completed"] = False
    record["judge_response"] = None
    path.write_text(json.dumps(record))
    assert read_official_results(tmp_path, {"one"}) == {"one": False}
    (tmp_path / "duplicate_eval.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Duplicate"):
        read_official_results(tmp_path, {"one"})


def test_server_token_counters_sum_replicas_without_counting_cache_twice() -> None:
    payload = b"""vllm:prompt_tokens_total{engine="0"} 17
vllm:prompt_tokens_total{engine="1"} 23
vllm:prompt_tokens_cached_total{engine="0"} 10
vllm:generation_tokens_total{engine="0"} 7
vllm:generation_tokens_total{engine="1"} 11
"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            value = counters(server.server_port)
            assert value.input_tokens == 40 and value.output_tokens == 18
            payload = b'vllm:prompt_tokens_total{engine="0"} NaN\n'
            with pytest.raises(ValueError, match="Missing or invalid"):
                counters(server.server_port)
        finally:
            server.shutdown()
            thread.join()
