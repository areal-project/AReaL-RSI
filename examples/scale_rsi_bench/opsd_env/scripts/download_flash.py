"""Download the official cp312 FlashAttention wheel using bounded range requests."""
from concurrent.futures import ThreadPoolExecutor
import os
import hashlib
from pathlib import Path
import shutil
import tempfile
import time
import zipfile
import requests

from env_paths import runtime_root
ROOT = runtime_root(__file__)
NAME = "flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"
URL = "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/" + NAME
session = requests.Session()
session.trust_env = False
response = session.get(URL, stream=True, timeout=60)
response.raise_for_status()
size = int(response.headers["Content-Length"])
resolved = response.url
response.close()
assert size == 256040057, f"Unexpected wheel size: {size}"
print(f"Downloading official wheel: {size} bytes", flush=True)
chunk = 256 * 1024
with tempfile.TemporaryDirectory(prefix="rsi-flash-wheel-") as scratch:
    path = Path(scratch) / NAME
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.ftruncate(descriptor, size)
    def fetch(start):
        end = min(start + chunk, size) - 1
        for attempt in range(4):
            try:
                client = requests.Session()
                client.trust_env = False
                with client.get(resolved, headers={"Range": f"bytes={start}-{end}"}, timeout=120) as part:
                    part.raise_for_status()
                    assert part.status_code == 206, "Server did not honor byte range"
                    assert part.headers["Content-Range"].startswith(f"bytes {start}-{end}/")
                    data = part.content
                    assert len(data) == end - start + 1
                    written = 0
                    while written < len(data):
                        written += os.pwrite(descriptor, data[written:], start + written)
                return start
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2)
    try:
        with ThreadPoolExecutor(max_workers=12) as workers:
            for count, _ in enumerate(workers.map(fetch, range(0, size, chunk)), 1):
                if count % 16 == 0:
                    print(f"Downloaded {min(count * chunk, size)}/{size} bytes", flush=True)
    finally:
        os.close(descriptor)
    with zipfile.ZipFile(path) as wheel:
        assert wheel.testzip() is None
    with path.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == "f25da18657a87fc83dc1bfb8b7751b82246e9db355510226b674fd437c34b5fb", "Official wheel SHA256 mismatch"
    destination = ROOT / "wheels" / NAME
    destination.parent.mkdir(exist_ok=True)
    partial = destination.with_suffix(".verified-partial")
    shutil.copyfile(path, partial)
    partial.replace(destination)
print("OFFICIAL FLASHATTENTION WHEEL READY", flush=True)
