# Plan: Support Experience + OPD in RSI

Status: **planning** (approved decisions recorded below; implementation not started).

## Goal

RSI today can only execute `method: sft`. Extend it so a Run with
`method: opd` runs AReaL's **Experience OPD** recipe end to end:
EnvArena trajectories → ResearchAgent synthesizes an **experience map** →
AReaL Experience-OPD training → holdout eval → memory, inside the same
`RSIController` loop that already drives SFT.

`TrainMethod = Literal["sft", "opd"]` already exists and `config.py`,
`action_tools.py`, `fake.py`, and the CLI already accept `opd`. **What is
missing is the actual OPD execution path and the experience-data contract**,
not the `opd` option itself.

## Reference material (AReaL side)

AReaL checkout: `/storage/openpsi/users/fenghui/AReaL`

There are **two distinct OPD launch models** in `examples/mopd/`, and they are
not interchangeable:

| Recipe | Launch model | Topology | Experience? | Entry |
|---|---|---|---|---|
| **Experience OPD** (SWE, 30B) | `sbatch` CPU driver → AReaL nested Slurm scheduler (`scheduler.type=slurm`, `allocation_mode`) spawns colocated actor/rollout/teacher | **exactly 4×8** (hard-checked) | **yes** (`experience_path`) | `sbatch_experience_opd_qwen3_30b.sh` → `run_experience_opd_qwen3_30b.sh` → `train_experience_opd.py --config swe_experience_opd_026_qwen3_30b.yaml` |
| **gsm8k MOPD** (local smoke) | in-process **local** launcher (`scheduler.type=local`) | **1×8** | **no** (pure teacher→student reverse-KL) | `python -m examples.mopd.gsm8k_qwen3_14b_to_0_6b --config gsm8k_qwen3_14b_to_0_6b_local.yaml [--dry-run]` |

Key data contract for Experience OPD:
- `EXPERIENCE_PATH` → a JSON file loaded by `ExperienceStore` as
  `dict[str, str]` = `{instance_id: experience_text}`. The teacher hook
  (`examples.mopd.experience_opd.ExperienceOPDHook`) looks experience up by
  `instance_id` and injects it into the teacher-only context for reverse-KL.
- `TRAIN_DATA` → the task JSONL; rows must carry `instance_id`.
  `train_experience_opd.py` **filters train rows to those covered by the
  experience store** and errors if the intersection is empty.
- Launcher CLI overrides: `actor.path`, `train_dataset.path`,
  `valid_dataset.path`, `experience_path`, `cluster.{n_nodes,n_gpus_per_node,
  fileroot,name_resolve.nfs_record_root}`, `experiment_name`, `trial_name`,
  `tokenizer_path`, `actor.scheduling_spec.0.image`.
- Deployment env required by the driver: `AREAL_IMAGE`, `AWEAGENT_ROOT`,
  `AWEX_ROOT`, `SGLANG_PYTHON_ROOT`, `FLASH_LINEAR_ATTENTION_ROOT`,
  `AENV_SYSTEM_URL` (sandbox), `WANDB_API_KEY`, `WANDB_BASE_URL`,
  `PYPI_INDEX_URL`, `SWE_RL_ADMIN_API_KEY` (auto-generated if unset).

## Approved decisions

1. **Align with SFT: generate our own sbatch.** The SFT wrapper
   (`sft_wrapper.py:_render_sbatch_script`) renders the complete sbatch script
   from config and submits it — it does **not** reuse an AReaL-provided script.
   The OPD Slurm path will do the same: the wrapper renders the CPU-driver
   sbatch (mirroring `sbatch_experience_opd_qwen3_30b.sh`) rather than shelling
   out to the checked-in AReaL script.
2. **Experience is produced purely by the ResearchAgent** from EnvArena
   trajectories. No `reference_experience_path` / external experience import.
3. **TRAIN_DATA is the benchmark's own task set** (generic; not SWE-specific).
   The train JSONL must expose an `instance_id` per task, provided by the
   EnvClient/benchmark, not hardcoded to SWE.
4. **Bring up with the gsm8k local recipe first**, then scale to the 30B
   Experience-OPD Slurm recipe. Write this plan first, then implement.

### Consequence of (4) to be explicit about

The gsm8k recipe is `scheduler: local` and has **no experience**. So the gsm8k
smoke validates: `method=opd` plumbing, `AReaLService` dispatch, the controller
OPD branch, and the **local** OPD launch mechanism — but it does **not**
exercise experience generation or sbatch generation. Those land with the
Experience-OPD Slurm path, whose first real run needs 4×8 GPUs (the recipe
hard-requires exactly 4 nodes × 8 GPUs). The `rsi` reservation is a single
8-GPU node, so the 30B experience run must target a 4-node allocation
elsewhere.

## Current SFT-hardcoded points (change map)

| File | Now | Change |
|---|---|---|
| `services/areal/service.py` | class wraps only `AReaLSFTWrapper`; `train()` raises for `method != "sft"` | dispatch by method: `sft`→SFT wrapper, `opd`→OPD wrapper; keep neutral `train`/`start_experiment`/`get_experiment_artifacts` API |
| `services/areal/sft_wrapper.py` | generates torchrun sbatch (SFT only) | leave as-is; add sibling `opd_wrapper.py` |
| `controller/local_controller.py:1076` | `_train_and_wait` raises `method != "sft"` | drop the guard; add OPD checkpoint resolution + driver-vs-nested completion handling |
| `config/areal.example.yaml` | only `areal.sft` section | add `areal.opd` section |
| `services/research/{claude_agent,mock,prompts,trajectory_workspace,sft_format}.py` | emit SFT JSONL + AReaL YAML | add experience-map output path (method-aware) |
| `config.py` / `RSIRunConfig` | `train.config` = single `train_config_path` | when `method=opd`, carry OPD launch config (recipe, config_path, env, overrides) |

## Implementation phases

### Phase 1 — OPD plumbing via gsm8k local (cheap, no experience)
Goal: `run-mock --method opd` and a real gsm8k **local** OPD run go through the
loop without touching the experience contract.
1. `AReaLService`: method-aware dispatch. Add an `areal.opd` config section
   (recipe selector: `local` gsm8k vs `slurm` experience; `module`/`command`,
   `config_path`, env passthrough, local vs slurm scheduler).
2. New `services/areal/opd_wrapper.py`, interface-parallel to
   `AReaLSFTWrapper` (`start_experiment`, `get_experiment_artifacts`).
   - **local** mode: run `python -m <module> --config <yaml>` (blocking,
     mirrors `_start_local`), resolve saved checkpoint from `saver.fileroot`.
3. Controller: remove the `method != "sft"` guard in `_train_and_wait`; make
   OPD checkpoint resolution use the OPD experiment/trial `saver` layout.
4. `MockAReaLService`: deterministic OPD branch so `run-mock --method opd`
   passes with no external deps.
5. Tests: service dispatch, opd_wrapper local launch (mocked runner),
   controller OPD branch (mock).

### Phase 2 — Experience data contract (ResearchAgent's new capability)
Goal: researcher turns trajectories into `{instance_id: experience}` and the
train JSONL is bound correctly.
6. `trajectory_workspace`: OPD materialization variant → write
   `experience.json`, resolve `train_dataset.path` (benchmark task set filtered
   to instances with experience), and emit OPD launch config (env + CLI
   overrides) instead of an SFT YAML.
7. `services/research/prompts.py`: experience-synthesis prompt (per-`instance_id`
   experience extracted from that instance's trajectory + memory).
8. `ClaudeResearchAgent.prepare_iteration`: method branch producing the
   experience artifact contract; `MockResearchAgent`: deterministic experience
   map for tests.
9. New `experience_format.py` (mirrors `sft_format.py`): validate the JSON is
   `{instance_id: non-empty str}` and has non-empty intersection with the train
   JSONL's `instance_id`s (matches `train_experience_opd.py`'s own check).
10. EnvClient/benchmark: expose per-task `instance_id` so TRAIN_DATA is generic
    across benchmarks (decision 3).

### Phase 3 — Experience-OPD Slurm launch (the real 30B path)
Goal: generate the CPU-driver sbatch ourselves (decision 1) and run 4×8.
11. `opd_wrapper.py` **slurm** mode: render the CPU-driver sbatch (mirroring
    `sbatch_experience_opd_qwen3_30b.sh`) — env exports + `srun singularity
    exec ... bash -lc 'exec bash run_experience_opd_qwen3_30b.sh'` (or exec the
    python launcher directly), with `#SBATCH` directives from config
    (nodes, cpus, mem, time, optional `--reservation`), `sbatch --parsable`.
12. `areal.opd.slurm` config: image, working_directory, the `*_ROOT`s, N=4/GPU=8
    (validated), exp/trial templates, config_path, bind paths; secrets injected
    at runtime, never stored.
13. Controller: OPD Slurm completion — driver job vs nested actor jobs; resolve
    the produced checkpoint from `saver` (`freq_steps`) for holdout eval.
14. `config/areal.opd.example.yaml` + `config/rsi.opd.example.yaml`
    (mock EnvArena + real OPD + Claude/mock researcher).

### Phase 4 — Hardening
15. Slurm completion detection robustness (this cluster lacks `sacct`; see the
    SFT run experience — the RL driver + nested scheduler makes this worse).
16. End-to-end: gsm8k local smoke green; then a guarded 30B experience run when
    a 4×8 allocation is available.

## Open items / risks
- **Resources**: 30B experience OPD needs 32 GPUs for up to 7 days; the `rsi`
  reservation is 1×8. A true experience smoke also needs 4×8 (recipe-enforced),
  so there is no cheap experience smoke — gsm8k covers plumbing only.
- **No cheap experience validation** below 4×8: consider whether a reduced
  experience config (fewer nodes) is acceptable to AReaL, or accept that the
  first experience run is the full 30B.
- **Secrets**: WANDB / AENV_SYSTEM_URL / SWE_RL_ADMIN_API_KEY / PYPI_INDEX_URL
  must be runtime-injected.
- **`sacct` missing** on this cluster: completion polling must not depend on it.
