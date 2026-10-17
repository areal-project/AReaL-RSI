"""Paired statistical inference and failure accounting for the official benchmark."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from examples.search.analysis import paired_statistics, read_official_results, wilson_interval
from examples.search.final_evaluation import counters


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
