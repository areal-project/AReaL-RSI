from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from areal_rsi.api.base import Task, TaskResult, TrajectoryRef, VerificationRequest, VerifierSpec
from areal_rsi.base.storage import FileContentStore, FileVersionManager

EXAMPLE = Path(__file__).parents[1] / "examples" / "3d_bench"
sys.path.insert(0, str(EXAMPLE))
from adapter import ThreeModelVerifier  # noqa: E402


class ThreeModelVerifierTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_js_file_passes_contract_v2(self) -> None:
        runtime = Path(".rsi/datasets/3d-model-v2/workspaces/3d-stage1")
        template = Path(".rsi/datasets/3d-model-v2/viewer-template/src/model.js").read_text()
        body = (EXAMPLE / "fixtures/valid-model-body.mjs").read_text()
        source = template[: template.index("export function createModel(THREE) {")] + body
        with tempfile.TemporaryDirectory() as temporary:
            content = FileContentStore(Path(temporary) / "content")
            versions = FileVersionManager(Path(temporary) / "versions")
            output = await content.put_text(source, media_type="text/javascript")
            model = await versions.create_model("model", output)
            harness = await versions.create_harness("harness", output)
            solution = await versions.create_solution(model, harness)
            request = VerificationRequest(
                Task("MODEL-001", "TARGET.md"), solution,
                TaskResult(output, TrajectoryRef("trace")),
                VerifierSpec("3d-model-v1", "injected"),
            )
            result = await ThreeModelVerifier(content, runtime).verify(request)
            self.assertTrue(result.passed)
            self.assertEqual(result.score, 1.0)
            self.assertEqual(len(result.attachments), 1)

    async def test_non_javascript_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            content = FileContentStore(Path(temporary) / "content")
            output = await content.put_text("not a model", media_type="application/json")
            with self.assertRaisesRegex(ValueError, "model.js 文件型"):
                await ThreeModelVerifier(content, Path(".rsi/datasets/3d-model-v2")).verify(
                    VerificationRequest(
                        Task("MODEL-001", "TARGET.md"),
                        await FileVersionManager(Path(temporary) / "versions").create_solution(
                            await FileVersionManager(Path(temporary) / "versions").create_model("m", output),
                            await FileVersionManager(Path(temporary) / "versions").create_harness("h", output),
                        ),
                        TaskResult(output, TrajectoryRef("trace")),
                        VerifierSpec("3d-model-v1", "injected"),
                    )
                )
