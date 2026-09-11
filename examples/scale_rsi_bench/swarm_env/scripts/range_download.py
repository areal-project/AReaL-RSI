"""Parallel HTTP range download, verified against HF LFS SHA256 before publish."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import requests
from huggingface_hub import get_hf_file_metadata, hf_hub_url

def download(repo, name, revision, destination):
    info = get_hf_file_metadata(hf_hub_url(repo, name, revision=revision))
    expected = destination.name
    assert info.etag.strip('"') == expected
    size = info.size
    chunk = 8 * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="rsi-model-repair-") as scratch:
        path = Path(scratch) / "weights"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.ftruncate(fd, size)
        def fetch(start):
            end = min(size, start + chunk) - 1
            for attempt in range(4):
                try:
                    with requests.Session() as client:
                        client.trust_env = False
                        with client.get(info.location, headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}, timeout=90) as response:
                            response.raise_for_status()
                            assert response.status_code == 206
                            assert response.headers["Content-Range"].startswith(f"bytes {start}-{end}/")
                            data = response.content
                            assert len(data) == end - start + 1
                            offset = 0
                            while offset < len(data):
                                offset += os.pwrite(fd, data[offset:], start + offset)
                    return len(data)
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2)
        try:
            with ThreadPoolExecutor(max_workers=12) as executor:
                futures = [executor.submit(fetch, start) for start in range(0, size, chunk)]
                done = 0
                for index, future in enumerate(as_completed(futures), 1):
                    done += future.result()
                    if index % 32 == 0:
                        print(f"{name}: {done}/{size} bytes", flush=True)
        finally:
            os.close(fd)
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected
        with tempfile.NamedTemporaryFile(prefix=".verified-repair-", dir=destination.parent, delete=False) as output:
            staged = Path(output.name)
        try:
            shutil.copyfile(path, staged)
            with staged.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == expected
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)
