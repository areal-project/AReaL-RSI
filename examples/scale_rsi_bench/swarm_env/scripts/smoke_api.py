"""One benign request to the job-owned local OpenAI-compatible server."""
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
with OpenAI(api_key=os.environ["NODE_LLM_API_KEY"], base_url=os.environ["NODE_LLM_API_BASE"], timeout=120, max_retries=0) as client:
    output = client.chat.completions.create(model=os.environ["NODE_LLM_MODEL"],
        messages=[{"role": "user", "content": "What is two plus two?"}], max_tokens=16, temperature=0)
    assert output.choices and output.usage.completion_tokens > 0
print("LOCAL NODE API PASS")
(Path(os.environ["RSI_ROOT"]) / "logs/API_PASS.json").write_text(json.dumps({
    "passed": True, "official_score": False,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "model": os.environ["NODE_LLM_MODEL"]}, indent=2) + "\n")
