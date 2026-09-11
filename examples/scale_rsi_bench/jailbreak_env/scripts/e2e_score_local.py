"""Only the exact-match capability axis; never fabricate missing judge scores."""
import json
from pathlib import Path
import sys
sys.path.insert(0, "/workspace/eval")
from grade import score_ifeval
out = Path(sys.argv[1])
rows = {r["prompt_id"]: r for r in map(json.loads, Path("/workspace/data/dev_split.jsonl").read_text().splitlines())}
responses = list(map(json.loads, (out / "responses.jsonl").read_text().splitlines()))
assert len(responses) == len(rows) == 280
resp = {r["prompt_id"]: r["response"] for r in responses}
assert set(rows) == set(resp)
capability, details, count = score_ifeval({k: v for k, v in rows.items() if v["kind"] == "ifeval"}, resp)
result = {"status": "partial_missing_judge_credentials", "responses": len(resp),
    "ifeval_count": count, "capability_pct": capability, "asr_pct": None,
    "refusal_pct": None, "composite_score": None, "official_score": False}
(out / "local-eval.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
