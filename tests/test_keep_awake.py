"""Exercise the actual macOS assertion, including cleanup after a dead child."""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from areal_rsi.api.research import SessionConfig
from areal_rsi.infra.codex import CodexClient


@unittest.skipUnless(sys.platform == "darwin", "macOS assertion integration test")
class KeepAwakeTests(unittest.TestCase):
    def test_native_assertion_is_held_only_while_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = CodexClient(root / "codex", root / "native.log", SessionConfig(str(root)))
            try:
                client.keep_awake(True)
                child = client.inhibitor
                assert child is not None
                deadline = time.monotonic() + 3
                while True:
                    assertions = subprocess.check_output(["pmset", "-g", "assertions"], text=True)
                    if f"pid {child.pid}(caffeinate)" in assertions:
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                client.keep_awake(False)
                self.assertIsNotNone(child.poll())
                client.keep_awake(False)
                client.keep_awake(True)
                assert client.inhibitor is not None
                client.inhibitor.kill()
                client.inhibitor.wait()
                client.keep_awake(True)
                self.assertTrue(client.inhibit_unavailable)
                self.assertIsNone(client.inhibitor)
            finally:
                client.keep_awake(False)
