import pytest

from areal_rsi.memory.audit import AuditLogger
from areal_rsi.memory.ledger import read_ledger


def test_audit_logger_records_success_and_redacts_secrets(tmp_path):
    audit = AuditLogger(tmp_path / "audit.jsonl")

    with audit.action(
        run_id="run_1",
        iteration=1,
        action_type="eval",
        step="submit_eval",
        input={"api_key": "secret", "nested": {"token": "secret", "safe": "value"}},
    ) as action:
        action.set_output({"score": 0.8, "auth_token": "secret"})

    events = read_ledger(tmp_path / "audit.jsonl")

    assert [event["status"] for event in events] == ["started", "succeeded"]
    assert events[0]["input"]["api_key"] == "***REDACTED***"
    assert events[0]["input"]["nested"]["token"] == "***REDACTED***"
    assert events[0]["input"]["nested"]["safe"] == "value"
    assert events[1]["output"]["auth_token"] == "***REDACTED***"


def test_audit_logger_records_failure(tmp_path):
    audit = AuditLogger(tmp_path / "audit.jsonl")

    with pytest.raises(RuntimeError):
        with audit.action(run_id="run_1", iteration=1, action_type="train", step="train"):
            raise RuntimeError("boom")

    events = read_ledger(tmp_path / "audit.jsonl")

    assert [event["status"] for event in events] == ["started", "failed"]
    assert events[1]["error"]["type"] == "RuntimeError"
    assert events[1]["error"]["message"] == "boom"
