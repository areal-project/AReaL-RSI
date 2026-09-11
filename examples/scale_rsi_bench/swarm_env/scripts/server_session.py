"""Own a localhost node server for one command, then stop only that server."""
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from openai import OpenAI

if len(sys.argv) < 2:
    raise SystemExit("Usage: run.sh serve COMMAND [ARG ...]")
root = Path(os.environ["RSI_ROOT"])
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
key = secrets.token_hex(24)
env = dict(os.environ, NODE_LLM_API_BASE=f"http://127.0.0.1:{port}/v1",
           NODE_LLM_API_KEY=key, NODE_LLM_MODEL="node-1b", NODE_LLM_TIMEOUT="600")
log = tempfile.NamedTemporaryFile(prefix="node-server-", suffix=".log", dir=root / "logs", delete=False)
server = subprocess.Popen([sys.executable, "-m", "vllm.entrypoints.openai.api_server",
    "--model", "/opt/models/Qwen3-4B", "--served-model-name", "node-1b",
    "--host", "127.0.0.1", "--port", str(port), "--api-key", key,
    "--max-model-len", "24576", "--gpu-memory-utilization", "0.90"],
    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print(f"Node server log: {log.name}", flush=True)
try:
    with OpenAI(base_url=env["NODE_LLM_API_BASE"], api_key=key, timeout=3, max_retries=0) as client:
        deadline = time.monotonic() + 600
        while True:
            if server.poll() is not None:
                raise RuntimeError(f"Node server exited: see {log.name}")
            try:
                assert any(model.id == "node-1b" for model in client.models.list().data)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Node server did not become ready: see {log.name}")
                time.sleep(2)
    print(f"Owned node server ready on port {port}", flush=True)
    result = subprocess.run(sys.argv[1:], env=env)
    raise SystemExit(result.returncode)
finally:
    if server.poll() is None:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait()
    log.close()
