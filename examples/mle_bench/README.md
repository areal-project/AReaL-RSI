# MLE-bench on Slurm / H200

This directory contains a dedicated Apptainer SIF, task data, and Slurm launchers.
The Agent runs outside the image. There is no Agent scaffold or LLM server in the
build context. Upstream competition solutions are also excluded.

The local selection contains **81 tasks: 74 evaluation + 7 development**.
`vinbigdata-chest-xray-abnormalities-detection` is excluded because of a confirmed
upstream grader defect. **TensorFlow GPU training is unavailable in this image;
the TensorFlow CPU functional probe passes.**

## Layout

```text
container/     SIF definition and ML requirements
scripts/       build, preparation, training, grading and smoke-test launchers
tasks/         local enabled task lists (all, evaluation, development, lite)
source/        pinned upstream checkout and UPSTREAM_COMMIT (generated)
images/        mlebench-h200.sif, checksums, package freeze (generated)
data/          <competition>/raw and prepared/{public,private} (generated)
runs/          one workspace per experiment (generated)
logs/          Slurm build, training and grading logs (generated)
cache/         build context and caches (generated)
```

Large/generated directories are ignored by Git and stay under this example.
Paths resolve relative to the scripts, so callers can run them from any directory.

## Environment and build

The base is `/storage/openpsi/images/pytorch280-1210-v1.sif`: Ubuntu 22.04,
Python 3.11.13, PyTorch 2.8.0+cu129, torchvision 0.23.0+cu129, torchaudio
2.8.0+cu129. ML libraries are installed using `container/requirements.txt`.
MLE-bench is pinned to `507f92e1138bb6e40dac5c6ee7a6758e6424bf97`.
This is an updated H200 runtime, not an exact reproduction of the paper's
PyTorch 2.2 environment. Report its hardware, package versions and runtime budget
when comparing scores.

Run from the repository root:

```bash
examples/mle_bench/scripts/build.sh
```

The script fetches upstream metadata on the login node, then submits a CPU Slurm
build job and prints its job ID. Build logs are `logs/build-<jobid>.log`. The build
requires the cluster's root/container-build privileges. It does not need a GPU or
a Docker daemon. It uses node-local `/tmp` for intermediate layers. The base image
fingerprint and final package freeze are written to `images/`.

`MLE_BASE_IMAGE` can select another compatible local base. Any replacement must
provide the same Python/PyTorch/torchvision/torchaudio stack, compiler and CUDA
libraries, or the dependency specification must be updated together.

## Data

The first end-to-end smoke test uses the **development** task `spaceship-titanic`.
`prepare_dev.py` downloads a revision-pinned public mirror of its original training
CSV, verifies the source Git blob checksum, runs upstream's preparation function,
and verifies **all four prepared public/private files against upstream MD5s**.
Its provenance is recorded in `data/spaceship-titanic/provenance.json`.
The smoke test does not represent an evaluation-set score. The original upstream
evaluation set has 75 tasks; this local selection has 74 after the exclusion.

For official Kaggle downloads, configure your own `~/.kaggle/kaggle.json` (or
`KAGGLE_CONFIG_DIR`) and accept the relevant competition rules on Kaggle first.
Credentials are mounted only for trusted preparation commands, not training.

```bash
# These commands submit CPU preparation jobs; all outputs go to data/.
examples/mle_bench/scripts/prepare.sh -c aerial-cactus-identification
examples/mle_bench/scripts/prepare.sh --lite
examples/mle_bench/scripts/prepare.sh --all
# List all selected tasks without downloading or preparing data:
examples/mle_bench/scripts/prepare.sh --all --dry-run
```

Lite/full downloads require substantial disk and network bandwidth. Only the
development task is fetched automatically. Upstream v1 known dataset issues still
apply. The local `prepare.sh --all` selects 81 tasks and skips VinBigData;
`--lite` still selects 22 tasks. Explicitly preparing, training or grading the
excluded task is rejected by the example's launchers. The exclusion and its
reason are recorded in `scripts/task_policy.json`.

Enabled lists are `tasks/{all,evaluation,development,lite}.txt`. Results on the
local 74-task evaluation subset must be labeled as such, with denominator 74;
they are not full results on the original 75-task benchmark. The upstream source
and SIF remain unchanged, so invoking upstream commands directly bypasses the
local launchers' exclusion policy.

## Submit a task from an external Agent

Place the code and mutable files in a workspace under `runs/`, then submit:

```bash
ROOT=$(realpath examples/mle_bench)
mkdir -p "$ROOT/runs/my-experiment"
# Write/copy train.py into $ROOT/runs/my-experiment/ first.
"$ROOT/scripts/submit.sh" spaceship-titanic "$ROOT/runs/my-experiment" \
    python /workspace/train.py
```

The command returns a Slurm job ID. Defaults are 1 GPU, 16 CPUs, 128 GiB RAM and
24 hours. Override with `MLE_GPUS`, `MLE_CPUS`, `MLE_MEM`, `MLE_TIME` and
`MLE_PARTITION`. Check status with `squeue -j JOB_ID` and the final exit status with
`sacct -j JOB_ID --format=JobID,State,ExitCode`.

Inside the training container:

| Path | Meaning |
| --- | --- |
| `/home/data` | This task's public data and description, read-only |
| `/workspace` | This experiment's code and writable workspace |
| `/home/submission` | `runs/<experiment>/submission/` |
| `/home/logs` | `runs/<experiment>/logs/` |
| `/cache` | This experiment's persistent cache |

Write predictions to `/home/submission/submission.csv`. Private test labels,
raw labeled source data, Kaggle credentials and other runs are not mounted by
the training launcher. Slurm's `CUDA_VISIBLE_DEVICES` is explicitly forwarded;
do not replace it with a hardcoded GPU index. Multiple task jobs can share a node
subject to the node's CPU/RAM availability.

For an existing GPU allocation, call `scripts/run.sh` with the same arguments.
Additional packages can be installed in a workspace virtual environment, e.g.
`python -m venv --system-site-packages /workspace/.venv`. The SIF is read-only;
changes to its temporary overlay do not persist across runs.
Temporary files use `/workspace/.tmp` on the shared disk, avoiding the cluster's
small Apptainer session tmpfs. Framework/model caches also use the mounted cache.

`--containall` and restricted mounts reduce accidental data exposure. This is
not a hostile-code sandbox or a replacement for cluster access controls: an
external Agent with the user's host permissions remains trusted.

## Grade separately

After training succeeds:

```bash
examples/mle_bench/scripts/grade.sh spaceship-titanic my-experiment
```

This submits a CPU job and writes `runs/my-experiment/grading_report.json` using
the official grader. The trusted grading container mounts private labels. There
is no score-returning test endpoint inside the training container, and no
root-only Docker entrypoint or Docker-in-Docker dependency.

## End-to-end test

```bash
examples/mle_bench/scripts/smoke.sh
```

This fetches the development input and submits a dependent chain of preparation,
single-GPU neural-network training, and CPU grading jobs. It prints their IDs and
the run directory. The training test checks CUDA execution, one visible GPU,
absence of private data, a validation accuracy above 0.7, and a prediction for
every test row. Inspect `logs/smoke-*.log`, `runs/smoke-*/logs/smoke_metrics.json`,
and `runs/smoke-*/grading_report.json` for evidence.

Task training does not require Kaggle access. Download pretrained model weights
in advance if compute-node internet access is restricted; model inference/API
access for the external Agent is configured outside this example.

## Verified on this cluster (2026-10-19)

- Image: `images/mlebench-h200.sif`, 11,136,614,400 bytes (10.37 GiB).
- Build job `981593`: successful; `pip check` passed; all 82 registered task
  preparation/grading modules loaded; Agent/solution directories absent.
- Development data: all four prepared-file MD5s matched upstream.
- Smoke jobs `981618` / `981619` / `981620`: all completed with exit code 0.
- Training: one visible H200 (driver name `NVIDIA L20X`), 7,823 training rows,
  870 test rows, 870 predictions, validation accuracy 0.8031948805.
- Official development-task test accuracy: **0.80115**, valid submission.

Evidence: `verification.json`, `data/spaceship-titanic/provenance.json`, and
`runs/smoke-20261019-152649/{jobs.json,logs/smoke_metrics.json,grading_report.json}`.
This verifies the environment and launch pipeline; it is not a full MLE-bench
evaluation.

## Batch environment audit (2026-10-19)

Run the offline audit through Slurm using the same SIF and training launcher:

```bash
examples/mle_bench/scripts/audit.sh
# Optional explicit output name:
examples/mle_bench/scripts/audit.sh environment-audit-my-run
```

The launcher submits a CPU job for the 81 enabled tasks and a separate single-GPU job for
framework operations. It uses the existing `spaceship-titanic` public-data mount;
prepare that development task first on a fresh checkout. Results are written to
`runs/<run-name>/{task-audit.json,task-audit.csv,runtime-audit.json,audit-jobs.json}`;
each test also has its own log. Both jobs return a nonzero exit code if any check
fails. A completed launcher means jobs were submitted, not that all checks passed.

The current snapshot is summarized in `environment-audit.json`. The per-task
report after exclusion is `runs/environment-audit-20261019-filtered/task-audit.csv`.
The earlier unfiltered audit and runtime diagnostics remain in
`runs/environment-audit-20261019/` for provenance. TensorFlow GPU remains a
failed runtime probe, so a full audit still reports that known limitation.

| Check | Result | What it establishes |
| --- | --- | --- |
| Task registry, preparation imports, metadata and leaderboards | 81/81 passed | All enabled tasks pass; VinBigData is excluded |
| Actual official metric execution with synthetic inputs, plus leaderboard ranking | 81/81 passed | All enabled tasks match expected synthetic scores |
| Offline framework/format functional probes | 12/13 passed | Tested PyTorch CV/NLP/audio, BF16 backward, LoRA, XGBoost GPU, CatBoost GPU, CPU tabular training and common file formats work |
| Prepared real data with official checksum matches | 1/81 | Only the development task has been prepared |
| Real-data training and official grading | 1 development task, from the earlier smoke test | Remaining 80 enabled tasks have not been run end to end |

Runtime limitation and exclusion reason:

- **TensorFlow GPU is unavailable in this SIF.** TensorFlow 2.17's installed
  build requires CUDA 12.3 / cuDNN 8. The image has PyTorch's cuDNN 9 package,
  and `libcudnn.so.8` is missing. TensorFlow enumerates no GPU, although its CPU
  backward test passes. PyTorch GPU and the other tested GPU backends pass.
  Any Agent solution choosing TensorFlow GPU needs a compatible isolated runtime
  or an explicitly revised image. See
  `runs/environment-audit-20261019/tensorflow-diagnostic.json` and the GPU test log.
- **VinBigData is excluded from the local selection.** The pinned upstream
  grader assigns ground-truth annotation ID 0; a one-box perfect prediction
  scores 0. Changing only that ID to 1 in a diagnostic object gives approximately
  1. This is an upstream metric edge case. The example skips this task in bulk
  preparation and auditing, and rejects individual preparation/training/grading
  requests for it. The SIF and official grader have not been patched. See
  `runs/environment-audit-20261019/vinbigdata-diagnostic.json`.

Preparation dependency checks inspect imports, including imports inside
functions; they do **not** execute all preparers on real raw data. No Kaggle
credentials are configured, and the remaining 80 enabled datasets are absent. Synthetic
scores and ranking checks are not competition scores or medals. Pretrained
weights, arbitrary Agent-selected dependencies, compressed DICOM variants, full
dataset resource requirements and multi-GPU scaling are outside these probes.
The upstream 3-D metric's worker count is limited to 2 for the tiny fixture;
its numerical metric code is unchanged. No Agent is included in this audit.
