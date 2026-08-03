"""Prompts for the file-backed, Run-scoped ResearchAgent workflow."""

from __future__ import annotations

import json
from typing import Any

RESEARCH_SYSTEM_PROMPT = """\
You are the Researcher in an autonomous recursive self-improvement loop.
The controller, EnvArena, and training service are separate systems. Your job is
to analyze task trajectories, write reusable experience, construct a valid SFT
dataset, choose training configuration, interpret evaluation evidence, and
choose the checkpoint for the next iteration.

Work through files. Never ask for large trajectory payloads in the conversation.
Treat one task as the atomic analysis unit. You may use successful, failed, or
partial trajectories in any way supported by the evidence. You decide which
memory and experience should affect training. A failed task must be isolated:
write a skipped result when possible and continue with the remaining tasks.

Your context boundary is the current Run. Do not inspect sibling or previous
Run directories under the RSI run root. The controller materializes all
allowed cross-iteration history for this Run in memory.json. External reads are
limited to the explicitly named SFT reference and training template.

Do not edit the AReaL-RSI source tree. Only read current-iteration inputs and
write under the current iteration's research workspace. Outputs are machine-consumed, so
obey the requested JSON or JSONL schemas exactly. Do not claim that a file was
written until it exists.
"""


def iteration_bootstrap_prompt(
    *,
    manifest: dict[str, Any],
    reference_sft_path: str | None,
) -> str:
    output = manifest["output"]
    return f"""\
Begin one RSI research iteration. This Claude session is dedicated to this iteration and
will be reused for every task and for the post-training decision. A new session
will be created for the next iteration in the same Run.

Read these compact inputs first:
- trajectory manifest: {manifest["manifest_path"]}
- run context: {manifest["run_context_path"]}
- memory scoped to this Run, including previous iterations: {manifest["memory_path"]}
- base training configuration: {manifest["base_train_config_path"]}

There are {manifest["task_count"]} tasks and {manifest["trajectory_count"]}
trajectories. Do not read every task file now. Read task files one at a time
when the controller asks you to analyze them.

Iteration outputs:
- per-task experiences: {output["experiences_dir"]}
- SFT JSONL dataset: {output["dataset_path"]}
- training configuration: {output["train_config_path"]}
- research result manifest: {output["research_result_path"]}
- checkpoint decision: {output["checkpoint_decision_path"]}

Known-good SFT format reference:
{reference_sft_path or "not configured"}

Acknowledge the file contract briefly. Do not create the final dataset yet.
"""


def task_analysis_prompt(
    *,
    task: dict[str, Any],
    experience_path: str,
) -> str:
    return f"""\
Analyze exactly one task.

Task ID: {task["task_id"]}
Trajectory input file: {task["path"]}
Experience output file: {experience_path}

Read the input file. It may contain multiple attempts for the same task.
Reason about the task, actions, feedback, rewards, successes, and failures.
Decide whether and how this task should contribute to later training.

Write one JSON object to the output file with this schema:
{{
  "schema_version": "task-experience.v1",
  "task_id": {json.dumps(task["task_id"], ensure_ascii=False)},
  "status": "completed" | "skipped",
  "source_traj_ids": ["..."],
  "experience": "concise reusable experience",
  "analysis": "evidence-backed task analysis",
  "training_recommendation": {{
    "include": true | false,
    "use_failed_trajectories": true | false,
    "strategy": "your proposed use of this task"
  }},
  "metadata": {{}}
}}

Do not analyze any other task in this turn. Finish by stating only the output
path and status.
"""


def dataset_construction_prompt(
    *,
    manifest: dict[str, Any],
    completed_experience_paths: list[str],
    skipped_tasks: list[dict[str, Any]],
    reference_sft_path: str | None,
    reference_contract: dict[str, Any],
) -> str:
    output = manifest["output"]
    return f"""\
All task-level analysis attempts for this iteration are complete.

Completed experience files:
{json.dumps(completed_experience_paths, indent=2, ensure_ascii=False)}

Skipped task records:
{json.dumps(skipped_tasks, indent=2, ensure_ascii=False)}

Now decide the iteration training strategy. You may reread task trajectory files,
experience files, and memory selectively. You decide which tasks and attempts to
include, how to use failed trajectories, and what experience should influence
future training.

Construct the final SFT data at:
{output["dataset_path"]}

Use the first non-empty JSONL record in this known-good reference as the exact
top-level and message/tool format contract:
{reference_sft_path or "not configured"}

The controller has already inspected that record. Use this compact structural
contract instead of trying to load or scan the large reference JSONL:
{json.dumps(reference_contract, indent=2, ensure_ascii=False)}

When you choose to put experience in the system prompt, append it to
messages[0].content. Do not put it only in the top-level system_message field.
The candidate file will be checked structurally against the reference. Write one
JSON object per line and do not wrap JSONL in Markdown.

Write the complete AReaL training YAML selected for this iteration to:
{output["train_config_path"]}
Read {manifest["base_train_config_path"]}. Its train_config_path points to the
user-selected complete AReaL YAML template. Copy that template into the output
path, then make the training and data choices supported by this iteration's
evidence. The run context contains current_ckpt; the generated YAML must set
actor.path to that exact checkpoint. Set train_dataset.path to the SFT JSONL you
just created. Keep cluster/service credentials and Slurm launcher settings out
of the training YAML. Preserve all required AReaL fields from the template.
Use a unique trial_name for this iteration and retain a checkpoint saver
frequency that produces at least one discoverable checkpoint. The global
train_dataset.batch_size must be divisible by
cluster.n_nodes * cluster.n_gpus_per_node, even when the generated dataset has
fewer records; do not lower it below that world size merely because the smoke
dataset is small.

Finally write this JSON manifest to {output["research_result_path"]}:
{{
  "schema_version": "research-result.v1",
  "dataset_id": "stable identifier",
  "dataset_path": {json.dumps(output["dataset_path"])},
  "train_config_path": {json.dumps(output["train_config_path"])},
  "included_task_ids": ["..."],
  "skipped_task_ids": ["..."],
  "memory_updates": [
    {{"title": "...", "content": "...", "metadata": {{}}}}
  ],
  "summary": "what you changed and why"
}}

Do not select the next checkpoint yet; evaluation has not happened. The
controller will validate the YAML checkpoint and dataset bindings before it
submits training.
"""


def dataset_format_repair_prompt(
    *,
    dataset_path: str,
    reference_sft_path: str,
    reference_contract: dict[str, Any],
    validation_error: str,
    attempt: int,
) -> str:
    return f"""\
The controller rejected the candidate SFT JSONL during structural validation.
This is repair attempt {attempt}.

Candidate file to repair in place:
{dataset_path}

Known-good reference:
{reference_sft_path}

Validation error:
{validation_error}

Compact reference contract:
{json.dumps(reference_contract, indent=2, ensure_ascii=False)}

Repair every non-empty JSONL record, not only the first one. Preserve the
research content and task selection, but add or correct all required structural
fields and types. Neutral metadata values from the compact contract are valid.
Every record must start messages with a system message whose content is a
string. An empty tools list is valid; if tool calls are retained, every call
must have a matching tool result.

Write valid JSONL directly to the same candidate file without Markdown. Do not
write helper scripts and do not change the research result or training config.
Finish by stating only the repaired path.
"""


def checkpoint_selection_prompt(
    *,
    evaluation_context_path: str,
    decision_path: str,
) -> str:
    return f"""\
Training and evaluation are complete. Read:
{evaluation_context_path}

Using the evidence from this same iteration session and Run-scoped memory, choose
the checkpoint that should be the base of the next iteration. You may
choose the previous base, the new candidate, or a historical checkpoint, but
you must choose exactly one value from allowed_checkpoints in that file.

Write this JSON object to {decision_path}:
{{
  "schema_version": "checkpoint-decision.v1",
  "next_checkpoint": "one exact allowed checkpoint",
  "reason": "evidence-backed explanation",
  "memory_updates": [
    {{"title": "...", "content": "...", "metadata": {{}}}}
  ]
}}

The controller alone applies target-score and maximum-iteration termination
rules. Your decision controls only which checkpoint is used if another
iteration is started.
"""
