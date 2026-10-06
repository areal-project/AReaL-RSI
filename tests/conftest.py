from __future__ import annotations

import json
from pathlib import Path

import pytest

from areal_rsi.infra.coordination.evaluation.harness import TIR_RUNNER_FILES, freeze_prompt_harness
from areal_rsi.infra.coordination.evaluation.tasks import SplitSizes, freeze_task_set


@pytest.fixture
def frozen_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    checkout = tmp_path / "AReaL"
    runner = checkout / "examples/tir"
    package = checkout / "areal"
    for path in (
        package / "__init__.py",
        package / "trainer/rl_trainer.py",
        package / "api/reward_api.py",
        checkout / "pyproject.toml",
        checkout / "uv.lock",
        *(runner / name for name in TIR_RUNNER_FILES),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# frozen test source\n")
    (runner / "config.yaml").write_text("scheduler:\n  type: slurm\n")
    train_source = tmp_path / "train.jsonl"
    test_source = tmp_path / "test.jsonl"

    def row(index: int) -> str:
        return json.dumps(
            {
                "messages": [{"role": "user", "content": f"What is {index} plus 1?"}],
                "answer": str(index + 1),
            }
        )

    train_source.write_text("\n".join(row(i) for i in range(4)) + "\n")
    test_source.write_text(row(10) + "\n")
    task_root = tmp_path / "taskset"
    freeze_task_set(train_source, test_source, task_root, sizes=SplitSizes(1, 1, 1, 1), seed=42)
    prompt = tmp_path / "h0.txt"
    prompt.write_text("Solve with the calculator. Final answer in \\boxed{{}}. {prompt}\n")
    h0_root = tmp_path / "h0"
    freeze_prompt_harness(prompt, runner, h0_root)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"gpt2"}')
    (model / "model.safetensors").write_bytes(b"test-weight")
    return task_root, h0_root, runner, model
