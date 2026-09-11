# Scale RSI Bench: Slurm deployment

**四个任务怎么跑：请先读 [中文统一运行指南](RUN_GUIDE.md)。**

Deployment code lives here; large and mutable runtime assets stay **outside Git**.
This is not yet a registered AReaL-RSI benchmark adapter or Linux research worker.
The existing package worker requires macOS `sandbox-exec`; a Linux/Slurm runtime
and task-specific benchmark/verifier/evaluation adapters are still needed for
the automatic research loop. No built-in benchmark registry was changed.

```text
rsi/
├── AReaL-RSI/examples/scale_rsi_bench/
│   ├── nanogpt_env/       # launch/install/test code and dependency pins
│   ├── opsd_env/          # includes allocation-safe dev evaluator
│   ├── jailbreak_env/
│   ├── swarm_env/
│   └── tests/
└── scale_rsi_bench/
    └── *_env/            # .venv, models, cache, source, workspace, runs/logs
```

Default runtime root is the sibling `scale_rsi_bench` of this repository.
Set `RSI_WORK_ROOT=/absolute/path/scale_rsi_bench` to use another prepared root,
or `RSI_ROOT=/absolute/path/opsd_env` for one task. Moving code does not move or
reinstall virtual environments, models, data, checkpoints or pinned source trees.
Each existing runtime launcher/config/script is a compatibility symlink to this
directory. New installations need the equivalent links and staged assets; these
scripts are not a one-command bootstrap for an empty machine.
The OPSD `/workspace/eval_official.py` and Jailbreak `/workspace/dev_eval.sh`,
`/workspace/eval/grade.py` links are intentionally absolute under
`/storage`: relative links escaping `/workspace` break at the container bind
mount. Repoint this link if the repository itself moves again.

From `rsi/`, either old or new entry points work:

```bash
bash AReaL-RSI/examples/scale_rsi_bench/nanogpt_env/bench.sh --help
bash scale_rsi_bench/nanogpt_env/bench.sh --baseline
bash AReaL-RSI/examples/scale_rsi_bench/opsd_env/run.sh
bash AReaL-RSI/examples/scale_rsi_bench/jailbreak_env/run.sh
bash AReaL-RSI/examples/scale_rsi_bench/swarm_env/run.sh
```

Task READMEs describe their training/evaluation contracts. Their relative runtime
paths still refer to `rsi/scale_rsi_bench`, not this code directory.

## OPSD fix and checkpoint-only validation

The upstream data-parallel evaluator overwrites `CUDA_VISIBLE_DEVICES` with
worker ordinals. This deployment copy maps workers to the inherited allocation
instead, supporting non-contiguous IDs and UUIDs. Missing/insufficient/invalid
multi-GPU allocations fail closed. Frozen source is untouched; prompts, model,
sampling, sharding and grading are unchanged. `workspace/eval_official.py` links
to the deployment copy. This fixes the **dev evaluation entry point**; the
upstream hidden verifier remains unchanged. The `run.sh verify` shorthand is
disabled before job submission: its GPU cleanup can kill unrelated processes.

Reuse the trained 100-step checkpoint (no retraining):

```bash
bash scale_rsi_bench/opsd_env/run.sh bash /workspace/dev_eval.sh \
  /workspace/runs/e2e-rytjpe5p/capped_seed42/checkpoint-100 \
  /workspace/runs/e2e-rytjpe5p/full-dev-eval-fixed.json
```

Defaults are 30 AIME24 problems × 12 samples, four GPUs, seed 20260610,
38,912 maximum new tokens, temperature 1.0. A single dev evaluation is not the
official five-run aggregate.

## Jailbreak judge configuration

The deployment grader reads `OPENAI_API_KEY`, `OPENAI_BASE_URL`
(default `https://api.openai.com/v1`), `JAILBREAK_JUDGE_MODEL` (default
`gpt-5-nano`) and `JAILBREAK_JUDGE_CONCURRENCY` (default 32). The endpoint
must support Chat Completions, strict JSON-schema structured outputs and
`reasoning_effort="minimal"`. Do not substitute another judge for comparable
benchmark scores. A gateway-specific model ID can be passed via `--model` only
when it routes to the same judge. Custom models are allowed for dev experiments,
but their scores are not directly benchmark-comparable. CLI `--model`,
`--base-url`, `--concurrency` override environment defaults. The unchanged upstream
hidden verifier does not read the new model/concurrency variables.
Both `dev_eval.sh` and direct `/workspace/eval/grade.py` use this deployment copy.
See [judge.env.example](jailbreak_env/judge.env.example) for a credential-free template.

Store secrets outside this repository and workspaces, restrict file permissions,
and load them in the shell submitting the job. Never paste keys into chat or
commit them. Load credentials only for the trusted grading job, not candidate
training or research-agent processes. The endpoint must be reachable from the
compute node. Example private file contents (replace placeholders locally):

```bash
export OPENAI_API_KEY='YOUR_SECRET'
export OPENAI_BASE_URL='https://YOUR_GATEWAY/v1'
export JAILBREAK_JUDGE_MODEL='gpt-5-nano'
export JAILBREAK_JUDGE_CONCURRENCY='8'
```

Then score the **existing** 280 generated dev responses; no training or generation
is needed. This command makes paid external judge requests:

```bash
(
source /absolute/private/path/jailbreak-judge.env
RSI_GPUS=0 RSI_CPUS=4 RSI_MEM=16G \
  bash scale_rsi_bench/jailbreak_env/run.sh \
  python /workspace/eval/grade.py \
    --data /workspace/data/dev_split.jsonl \
    --responses /workspace/runs/e2e-ku30gqS3/artifact/responses.jsonl \
    --output /workspace/runs/e2e-ku30gqS3/artifact/score.json
)
```

The launchers forward only explicitly provided judge credentials into the
container. Dev grading needs 220 judge requests before retries (160 StrongReject
+ 60 over-refusal); 60 IFEval examples are graded locally. The grader returns
ASR/refusal/capability axes; this is not the final hidden-test composite report.
Credentials alone do not launch billing: run the command explicitly.

## Offline regression tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s AReaL-RSI/examples/scale_rsi_bench/tests -v
```

Tests cover code/runtime resolution, compatibility links, allocation mapping,
and preservation of upstream frozen evaluation settings and grading functions.
