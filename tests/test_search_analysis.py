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
from examples.search.final_evaluation import (
    FinalEvalConfig,
    context_budget_exhaustion,
    counters,
    digest,
    export_checkpoint,
    recover_summary,
    tool_argument_failure,
)


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


def context_failure(directory: Path, query_id: str) -> Path:
    error = "qwen_agent.llm.base.ModelServiceError: Error code: 400 - " + repr(
        {
            "error": {
                "message": "You passed 121073 input tokens and requested 10000 output tokens. "
                "However, the model's context length is only 131072 tokens, resulting in "
                "a maximum input length of 121072 tokens. Please reduce the length of the "
                "input prompt. (parameter=input_tokens, value=121073)",
                "type": "BadRequestError",
                "param": "input_tokens",
                "code": 400,
            }
        }
    )
    record = {
        "query_id": query_id,
        "status": "error",
        "error": error,
        "metadata": {"max_output_tokens": 10000, "elapsed_seconds": 1.5},
        "tool_call_counts": {"search": 2},
        "retrieved_docids": ["external-doc"],
        "result": [{"type": "tool_call", "output": "external evidence"}],
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run_{query_id}.json"
    path.write_text(json.dumps(record))
    raw = directory.parent / (directory.name + "-raw") / query_id
    raw.mkdir(parents=True)
    original = {key: value for key, value in record.items() if key != "error"}
    original["status"] = "incomplete"
    (raw / "run_original.json").write_text(json.dumps(original))
    (raw / "messages.json").write_text(
        json.dumps(
            {
                "error": error,
                "request_config": {"max_tokens": 10000},
                "response": [{"role": "function", "content": "external evidence"}],
            }
        )
    )
    return path


def test_context_budget_requires_exact_refusal_and_original_incomplete_evidence(
    tmp_path: Path,
) -> None:
    path = context_failure(tmp_path / "runs" / "example", "external")
    original = json.loads(path.read_text())
    failure = context_budget_exhaustion(path, original)
    assert (failure.input_tokens, failure.requested_output_tokens, failure.context_tokens) == (
        121073,
        10000,
        131072,
    )
    for message in [
        "TimeoutError: retrieval timed out",
        "qwen_agent.llm.base.ModelServiceError: Error code: 500 - service unavailable",
        original["error"].replace("121073", "120000"),
    ]:
        with pytest.raises(ValueError, match="Unrecoverable"):
            context_budget_exhaustion(path, {**original, "error": message})
    with pytest.raises(ValueError, match="Unrecoverable"):
        context_budget_exhaustion(
            path, {**original, "result": [{"type": "output_text", "output": "an actual answer"}]}
        )
    Path(failure.original_record.path).unlink()
    with pytest.raises(ValueError, match="Unrecoverable"):
        context_budget_exhaustion(path, original)


def test_summary_recovery_preserves_full_official_denominator_and_rejects_other_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import asdict

    source = tmp_path / "original"
    runs = source / "runs/qwen3-embedding-8b" / source.name
    judged = source / "evals/qwen3-embedding-8b" / source.name
    judged.mkdir(parents=True)
    special = context_failure(runs, "0")
    for index in range(830):
        if index:
            (runs / f"run_{index}.json").write_text(
                json.dumps(
                    {
                        "query_id": str(index),
                        "status": "completed",
                        "tool_call_counts": {"search": 2},
                        "metadata": {"elapsed_seconds": 1.5},
                    }
                )
            )
        (judged / f"run_{index}_eval.json").write_text(
            json.dumps(
                {
                    "query_id": str(index),
                    "is_completed": index != 0,
                    "judge_response": "correct: yes" if index else None,
                    "judge_result": {"correct": 1 <= index <= 12, "parse_error": False},
                }
            )
        )
    (judged / "evaluation_summary.json").write_text(json.dumps({"Recall (%)": 1.0}))
    (tmp_path / "datasets").mkdir()
    (tmp_path / "datasets/queries.tsv").write_text(
        "".join(f"{index}\texternal question {index}\n" for index in range(830))
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    lock = tmp_path / "lock.json"
    lock.write_text("{}")
    config = FinalEvalConfig(
        benchmark_repository="unused",
        historical_environment="unused",
        data_root=str(tmp_path),
        model="unchanged-model",
        base_model="unchanged-base",
        embedding_model="unchanged-dense",
        judge_model="unchanged-judge",
        checkpoint_manifest=str(manifest),
        checkpoint_manifest_sha256=digest(manifest),
        asset_lock=str(lock),
        asset_lock_sha256=digest(lock),
        data_python="unused",
        vllm_python="unused",
        qwen_overlay="unused",
        output=str(source),
        seed=43,
    )
    config_path = source / "config.json"
    config_path.write_text(json.dumps(asdict(config)))
    (source / "environment.json").write_text(
        json.dumps(
            {
                "slurm_job_id": "123",
                "asset_hashes_verified": True,
                "historical_source_hashes_verified": True,
                "asset_lock_sha256": digest(lock),
            }
        )
    )
    (source / "solver-cost.json").write_text(
        json.dumps(
            {
                "input_tokens": 123456,
                "output_tokens": 23456,
                "inference_wall_seconds": 30,
            }
        )
    )
    monkeypatch.setattr(
        "examples.search.final_evaluation.subprocess.check_output",
        lambda *a, **k: "123|RUNNING|40|\n",
    )
    with pytest.raises(ValueError, match="must be terminal"):
        recover_summary(config_path, tmp_path / "live.json")
    before = {str(path): digest(path) for path in source.rglob("*") if path.is_file()}
    monkeypatch.setattr(
        "examples.search.final_evaluation.subprocess.check_output",
        lambda *a, **k: "123|FAILED|42|\n",
    )
    recovered = recover_summary(config_path, tmp_path / "recovered.json")
    summary = json.loads(recovered.read_text())
    assert summary["questions"] == 830 and summary["correct"] == 12
    assert summary["accuracy"] == 12 / 830 and summary["context_budget_exhaustions"] == 1
    assert summary["mean_search_calls"] == 2 and summary["mean_query_seconds"] == 1.5
    receipt = json.loads(Path(summary["recovery_receipt"]).read_text())
    assert receipt["source_job_elapsed_seconds"] == 42
    assert len(receipt["context_budget_exhaustions"]) == 1
    assert {str(path): digest(path) for path in source.rglob("*") if path.is_file()} == before
    assert json.loads(special.read_text())["status"] == "error"
    bad = runs / "run_1.json"
    record = json.loads(bad.read_text())
    record.update(status="error", error="TimeoutError: a real service failure")
    bad.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Unrecoverable"):
        recover_summary(config_path, tmp_path / "must-reject.json")
    assert not (tmp_path / "must-reject.json").exists()


@pytest.mark.parametrize("kind", ["invalid-json", "non-object"])
def test_model_argument_observations_cannot_hide_service_errors_or_changed_evidence(
    tmp_path: Path, kind: str
) -> None:
    config = FinalEvalConfig(
        benchmark_repository="unused",
        historical_environment="unused",
        data_root="unused",
        model="unused",
        base_model="unused",
        embedding_model="unused",
        judge_model="unused",
        checkpoint_manifest="unused",
        checkpoint_manifest_sha256="unused",
        asset_lock="unused",
        asset_lock_sha256="unused",
        data_python="unused",
        vllm_python="unused",
        qwen_overlay=str(tmp_path / "overlay"),
        output="unused",
    )
    path = context_failure(tmp_path / "runs/example", "external")
    record = json.loads(path.read_text())
    record.pop("error")
    record["status"] = "completed"
    header = "An error occurred when calling tool `search-server-search`:\n"
    manager = str(Path(config.qwen_overlay) / "qwen_agent/tools/mcp_manager.py")
    if kind == "invalid-json":
        arguments = '{"query": "'
        detail = (
            "JSONDecodeError: Unterminated string starting at: "
            "line 1 column 11 (char 10)\nTraceback:\n"
        )
        trace = f'  File "{manager}", line 273, in call\n    tool_args = json.loads(params)\n'
    else:
        arguments = json.dumps("external query")
        detail = (
            "ValidationError: 1 validation error for CallToolRequestParams\narguments\n"
            "  Input should be a valid dictionary [type=dict_type, input_type=str]\nTraceback:\n"
        )
        trace = (
            f'  File "{manager}", line 451, in execute_function\n'
            '  File "/venv/mcp/client/session.py", line 268, in call_tool\n'
        )
    observation = header + detail + trace
    record["result"] = [
        {"type": "tool_call", "tool_name": "search", "arguments": arguments, "output": observation}
    ]
    path.write_text(json.dumps(record))
    raw = path.parent.parent / (path.parent.name + "-raw") / "external"
    original = raw / "run_original.json"
    original.write_text(json.dumps(record))
    (raw / "messages.json").write_text(
        json.dumps({"error": None, "response": [{"role": "function", "content": observation}]})
    )
    result = tool_argument_failure(config, path, record, 0)
    assert result.kind.startswith("model-arguments-") and result.query_id == "external"
    for changes in [
        {"arguments": json.dumps({"query": "valid arguments"})},
        {"output": header + "TimeoutError: a real transport failure\n" + trace},
    ]:
        changed = {**record, "result": [{**record["result"][0], **changes}]}
        with pytest.raises(ValueError, match="Unverified tool error"):
            tool_argument_failure(config, path, changed, 0)
    original.write_text(json.dumps({**record, "status": "error"}))
    with pytest.raises(ValueError, match="Unverified tool error"):
        tool_argument_failure(config, path, record, 0)
