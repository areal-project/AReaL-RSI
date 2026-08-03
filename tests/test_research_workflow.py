import json
import re
from pathlib import Path

import pytest
import yaml

from areal_rsi.services.research import (
    ClaudeResearchAgent,
    ClaudeResearchConfig,
    SFTFormatError,
    TrajectoryWorkspace,
    validate_sft_jsonl,
)
from areal_rsi.services.research.claude_agent import ClaudeTurnResult


def test_trajectory_workspace_writes_one_file_per_task(tmp_path):
    workspace = TrajectoryWorkspace.create(tmp_path / "research")
    manifest = workspace.materialize(
        trajs=[
            {"traj_id": "a1", "task_id": "task/a", "reward": 0.0},
            {"traj_id": "a2", "task_id": "task/a", "reward": 1.0},
            {"traj_id": "b1", "task_id": "task/b", "reward": 0.5},
        ],
        memory_context={"events": [{"title": "previous"}]},
        run_context={"run_id": "run-1", "iteration": 1},
        base_train_config={"epochs": 1},
    )

    assert manifest["task_count"] == 2
    assert manifest["trajectory_count"] == 3
    assert manifest["scope"] == {
        "run_id": "run-1",
        "iteration": 1,
    }
    assert len(manifest["tasks"]) == 2
    first_task = json.loads(Path(manifest["tasks"][0]["path"]).read_text())
    assert first_task["task_id"] == "task/a"
    assert len(first_task["trajectories"]) == 2
    compact_manifest = Path(manifest["manifest_path"]).read_text()
    assert '"messages"' not in compact_manifest


def test_sft_validator_checks_reference_shape_and_tool_pairing(tmp_path):
    reference_path = tmp_path / "reference.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    record = _sft_record()
    reference_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    candidate_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    report = validate_sft_jsonl(candidate_path, reference_path=reference_path)

    assert report["valid"] is True
    assert report["record_count"] == 1

    invalid = _sft_record()
    invalid["messages"].insert(
        -1,
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "call",
            "tool_calls": [
                {
                    "id": "missing-result",
                    "type": "function",
                    "function": {"name": "Read", "arguments": {"file_path": "x"}},
                }
            ],
        },
    )
    candidate_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(SFTFormatError, match="without results"):
        validate_sft_jsonl(candidate_path, reference_path=reference_path)


def test_claude_researcher_reuses_one_session_within_iteration(tmp_path):
    reference_path = tmp_path / "reference.jsonl"
    reference_path.write_text(json.dumps(_sft_record()) + "\n", encoding="utf-8")
    template_path = _write_training_template(tmp_path)
    base_train_config = {
        "train_config_path": str(template_path),
        "scheduler_type": "slurm",
    }
    workspace = TrajectoryWorkspace.create(tmp_path / "iteration")
    manifest = workspace.materialize(
        trajs=[
            {
                "traj_id": "traj-1",
                "task_id": "gsm8k-1",
                "reward": 0.0,
                "messages": [{"role": "user", "content": "1+1?"}],
            },
            {
                "traj_id": "traj-2",
                "task_id": "gsm8k-2",
                "reward": 1.0,
                "messages": [{"role": "user", "content": "2+2?"}],
            },
        ],
        memory_context={"events": []},
        run_context={"run_id": "run-1", "iteration": 1},
        base_train_config=base_train_config,
    )
    received_session_ids: list[str | None] = []

    def fake_query(prompt, cwd, config, session_id):
        del config
        received_session_ids.append(session_id)
        if "Analyze exactly one task." in prompt:
            task_id = re.search(r"Task ID: (.+)", prompt).group(1)
            output_path = Path(
                re.search(r"Experience output file: (.+)", prompt).group(1)
            )
            output_path.write_text(
                json.dumps(
                    {
                        "schema_version": "task-experience.v1",
                        "task_id": task_id,
                        "status": "completed",
                        "source_traj_ids": [],
                        "experience": "check arithmetic",
                        "analysis": "mock",
                        "training_recommendation": {
                            "include": True,
                            "use_failed_trajectories": True,
                            "strategy": "distill",
                        },
                        "metadata": {},
                    }
                ),
                encoding="utf-8",
            )
        elif "Construct the final SFT data" in prompt:
            output = cwd / "output"
            (output / "swe_dataset.jsonl").write_text(
                json.dumps(_sft_record()) + "\n",
                encoding="utf-8",
            )
            _write_research_training_yaml(output, current_ckpt="base")
            (output / "research_result.json").write_text(
                json.dumps(
                    {
                        "schema_version": "research-result.v1",
                        "dataset_id": "iteration-1",
                        "dataset_path": str(output / "swe_dataset.jsonl"),
                        "train_config_path": str(output / "train_config.yaml"),
                        "included_task_ids": ["gsm8k-1", "gsm8k-2"],
                        "skipped_task_ids": [],
                        "memory_updates": [],
                        "summary": "mock synthesis",
                    }
                ),
                encoding="utf-8",
            )
        elif "Training and evaluation are complete." in prompt:
            (cwd / "output" / "checkpoint_decision.json").write_text(
                json.dumps(
                    {
                        "schema_version": "checkpoint-decision.v1",
                        "next_checkpoint": "candidate",
                        "reason": "higher evaluation score",
                        "memory_updates": [],
                    }
                ),
                encoding="utf-8",
            )
        return ClaudeTurnResult(session_id="iteration-session", result="ok")

    agent = ClaudeResearchAgent(
        ClaudeResearchConfig(
            reference_sft_path=str(reference_path),
            max_retries=3,
            retry_backoff_seconds=0,
        ),
        query_runner=fake_query,
    )
    result = agent.prepare_iteration(
        trajectory_manifest=manifest,
        memory_context={"events": []},
        current_ckpt="base",
        base_train_config=base_train_config,
        run_context={"run_id": "run-1", "iteration": 1},
    )
    decision = agent.select_checkpoint(
        research_result=result,
        allowed_checkpoints=["base", "candidate"],
        evaluation={"evaluation_score": {"mean_reward": 0.8}},
        memory_context={"events": []},
        run_context={"run_id": "run-1", "iteration": 1},
    )

    assert received_session_ids[0] is None
    assert received_session_ids[1:] == ["iteration-session"] * 4
    assert result["train_config"] == {
        "train_config_path": str(
            (tmp_path / "iteration" / "output" / "train_config.yaml").resolve()
        ),
        "scheduler_type": "slurm",
    }
    assert result["validation"]["record_count"] == 1
    assert decision["next_checkpoint"] == "candidate"
    assert decision["session_id"] == "iteration-session"


def test_claude_researcher_repairs_invalid_sft_in_same_session(tmp_path):
    reference_path = tmp_path / "reference.jsonl"
    reference_path.write_text(json.dumps(_sft_record()) + "\n", encoding="utf-8")
    template_path = _write_training_template(tmp_path)
    base_train_config = {"train_config_path": str(template_path)}
    workspace = TrajectoryWorkspace.create(tmp_path / "iteration")
    manifest = workspace.materialize(
        trajs=[{"traj_id": "traj-1", "task_id": "task-1", "reward": 0.0}],
        memory_context={"events": []},
        run_context={"run_id": "run-1", "iteration": 1},
        base_train_config=base_train_config,
    )
    prompts: list[tuple[str, str | None]] = []

    def fake_query(prompt, cwd, config, session_id):
        del config
        prompts.append((prompt, session_id))
        output = cwd / "output"
        if "Analyze exactly one task." in prompt:
            task_id = re.search(r"Task ID: (.+)", prompt).group(1)
            output_path = Path(
                re.search(r"Experience output file: (.+)", prompt).group(1)
            )
            output_path.write_text(
                json.dumps(
                    {
                        "schema_version": "task-experience.v1",
                        "task_id": task_id,
                        "status": "completed",
                        "source_traj_ids": [],
                        "experience": "learn from failure",
                        "analysis": "mock",
                        "training_recommendation": {
                            "include": True,
                            "use_failed_trajectories": True,
                            "strategy": "distill",
                        },
                        "metadata": {},
                    }
                ),
                encoding="utf-8",
            )
        elif "Construct the final SFT data" in prompt:
            (output / "swe_dataset.jsonl").write_text(
                json.dumps({"mid": "broken", "messages": []}) + "\n",
                encoding="utf-8",
            )
            _write_research_training_yaml(output, current_ckpt="base")
            (output / "research_result.json").write_text(
                json.dumps(
                    {
                        "schema_version": "research-result.v1",
                        "dataset_id": "repair-test",
                        "dataset_path": str(output / "swe_dataset.jsonl"),
                        "train_config_path": str(output / "train_config.yaml"),
                        "included_task_ids": ["task-1"],
                        "skipped_task_ids": [],
                        "memory_updates": [],
                        "summary": "mock invalid synthesis",
                    }
                ),
                encoding="utf-8",
            )
        elif "repair attempt" in prompt:
            (output / "swe_dataset.jsonl").write_text(
                json.dumps(_sft_record()) + "\n",
                encoding="utf-8",
            )
        return ClaudeTurnResult(session_id="iteration-session", result="ok")

    agent = ClaudeResearchAgent(
        ClaudeResearchConfig(
            reference_sft_path=str(reference_path),
            max_retries=1,
            retry_backoff_seconds=0,
            format_repair_attempts=2,
        ),
        query_runner=fake_query,
    )

    result = agent.prepare_iteration(
        trajectory_manifest=manifest,
        memory_context={"events": []},
        current_ckpt="base",
        base_train_config=base_train_config,
        run_context={"run_id": "run-1", "iteration": 1},
    )

    repair_prompts = [item for item in prompts if "repair attempt" in item[0]]
    assert len(repair_prompts) == 1
    assert repair_prompts[0][1] == "iteration-session"
    assert "missing reference fields" in repair_prompts[0][0]
    assert result["validation"]["valid"] is True
    assert len(result["validation_errors_repaired"]) == 1


def _sft_record():
    return {
        "mid": "sample",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "reason",
            },
        ],
        "tools": [],
        "system_message": "",
        "data_provenance": "{}",
        "data_characteristics": "{}",
        "data_consumption": "{}",
        "data_ext_info": "{}",
        "required_info": "{}",
        "meta": {},
        "metadata": {},
        "_ai_unique_id_": "id",
        "multiturn_loss": None,
        "reasoning_effort": "",
        "chat_template_kwargs": {},
    }


def _write_training_template(root: Path) -> Path:
    path = root / "template.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "experiment_name": "test",
                "trial_name": "template",
                "cluster": {
                    "fileroot": str(root),
                    "n_nodes": 1,
                    "n_gpus_per_node": 1,
                },
                "actor": {"path": "template-ckpt"},
                "train_dataset": {"path": "template.jsonl", "type": "sft"},
                "saver": {"freq_steps": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_research_training_yaml(output: Path, *, current_ckpt: str) -> None:
    (output / "train_config.yaml").write_text(
        yaml.safe_dump(
            {
                "experiment_name": "test",
                "trial_name": "iteration-1",
                "cluster": {
                    "fileroot": str(output.parent),
                    "n_nodes": 1,
                    "n_gpus_per_node": 1,
                },
                "actor": {"path": current_ckpt},
                "train_dataset": {
                    "path": str(output / "swe_dataset.jsonl"),
                    "type": "sft",
                    "batch_size": 1,
                },
                "saver": {"freq_steps": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
