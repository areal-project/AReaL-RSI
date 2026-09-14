import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from areal_rsi.modules.runtime import sandbox_wrapper


@unittest.skipUnless(sys.platform == "darwin", "macOS sandbox-exec integration")
class IsolationTest(unittest.TestCase):
    def test_shell_cannot_read_a_sibling_answer_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private = root / "expected.txt"
            private.write_text("hidden answer")
            session = root / "session"
            session.mkdir()
            workspace = session / "workspace"
            workspace.mkdir()
            public = workspace / "task.txt"
            public.write_text("public question")
            installation = root / "dsh-cli"
            cli = installation / "node_modules/.bin/dsh"
            cli.parent.mkdir(parents=True)
            node = shutil.which("node")
            if node is None:
                self.skipTest("shared CLI requires Node.js")
            entry = installation / "entry.js"
            entry.write_text(
                'process.stdout.write(require("node:fs").readFileSync(process.argv[2]));'
            )
            cli.write_text(
                "#!/bin/sh\nexec " + shlex.quote(node) + " " + shlex.quote(str(entry)) + ' "$@"\n'
            )
            cli.chmod(0o700)
            installed_file = installation / "runtime.txt"
            installed_file.write_text("shared runtime")
            wrapper = sandbox_wrapper(session, workspace, cli)
            self.assertIn(str(cli), wrapper.read_text())
            runtime = subprocess.run([str(wrapper), str(installed_file)], capture_output=True)
            self.assertEqual(runtime.stdout, b"shared runtime", runtime.stderr.decode())
            write = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    str(session / "sandbox.sb"),
                    "/bin/sh",
                    "-c",
                    'printf changed > "$1"',
                    "sh",
                    str(installed_file),
                ],
                capture_output=True,
            )
            self.assertNotEqual(write.returncode, 0)
            self.assertEqual(installed_file.read_text(), "shared runtime")
            command = ["/usr/bin/sandbox-exec", "-f", str(session / "sandbox.sb"), "/bin/cat"]
            allowed = subprocess.run([*command, str(public)], capture_output=True)
            denied = subprocess.run([*command, str(private)], capture_output=True)
            self.assertEqual(allowed.stdout, b"public question")
            self.assertNotEqual(denied.returncode, 0)
            self.assertNotIn(b"hidden answer", denied.stdout)
            terminal = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    str(session / "sandbox.sb"),
                    sys.executable,
                    "-c",
                    "import pty,os; a,b=pty.openpty(); os.close(a); os.close(b)",
                ],
                cwd=workspace,
                capture_output=True,
            )
            self.assertEqual(terminal.returncode, 0, terminal.stderr.decode())
