import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import digest


class PublicationTests(unittest.TestCase):
    def report(self):
        return {
            "date": "2026-09-21",
            "summary": "摘要",
            "items": [],
            "coverage": dict.fromkeys(["arxiv", "github", "wechat", "xiaohongshu"], "已检查"),
        }

    def test_dates_and_coverage(self):
        report = self.report()
        del report["coverage"]["wechat"]
        with self.assertRaises(ValueError):
            digest.render(report)
        self.assertEqual(
            digest.canonical("http://arxiv.org/pdf/2609.14858v1.pdf"),
            "https://arxiv.org/abs/2609.14858",
        )

    def test_ambiguous_create_blocks_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = root / "report.md"
            body.write_text(digest.render(self.report()))
            with (
                patch.object(digest, "yuque", side_effect=[[], TimeoutError()]),
                self.assertRaises(TimeoutError),
            ):
                digest.publish({"namespace": "a/b"}, root, self.report(), body)
            with patch.object(digest, "yuque") as call:
                with self.assertRaises(RuntimeError):
                    digest.publish({"namespace": "a/b"}, root, self.report(), body)
                call.assert_not_called()
            self.assertFalse((root / "seen.json").exists())

    def test_existing_remote_report_updates_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = root / "report.md"
            body.write_text(digest.render(self.report()))
            doc = {"title": "RSI 研究日报｜2026-09-21", "slug": "s", "url": "https://x/s"}
            remote = dict(doc, body=body.read_text(), published_at="2026-09-21")
            with patch.object(digest, "yuque", side_effect=[[doc], doc, remote]) as call:
                result = digest.publish({"namespace": "a/b"}, root, self.report(), body)
                self.assertEqual(call.call_args_list[1].args[:3], ("update", "doc", "a/b/s"))
                self.assertTrue(result["verified"])


if __name__ == "__main__":
    unittest.main()
