# 3D Benchmark: stage 1

This example prepares and checks the upstream Contract V2 evaluation runtime.
It does not call an LLM, GameAgent, RSI, or Qwen-VL. No optimization experiment
is started. Only the scripts and this README are tracked here.

## Layout and provenance

- `.rsi/datasets/3d-model-v2/`: fixed viewer, helpers, validator, capture scripts,
  fixtures, lockfile, generated SHA-256 manifest, workspaces and outputs
  (Git-ignored).
- A separate workspace: writable copy; only `src/model.js` may change.
- Separate output directories: validation receipts, screenshots, run metadata.

Source: [upstream README](https://code.alipay.com/AReaL/GameAgent/blob/xss-gdd-text2model/gdd-model-runner/README.md),
branch `xss-gdd-text2model`.
Pinned GameAgent commit `d7a9504cc6bb5b1918a084ef62225688a67c3aa6`, directory
`gdd-model-runner/viewer-template`. `init` exports that exact commit, not the
current working tree. Scripts pin and check the full snapshot digest.
Three.js is `0.185.1`; npm installs the supplied lockfile with `npm ci`.
The embedded style is `piggy-island`. No runtime files are vendored in examples.

## Reproduce

Run from the AReaL-RSI repository root with Node.js and npm installed:

```bash
bash examples/3d_bench/run.sh init /Users/jjsong/code/GameAgent
bash examples/3d_bench/run.sh prepare .rsi/datasets/3d-model-v2/workspaces/3d-stage1
bash examples/3d_bench/run.sh selftest .rsi/datasets/3d-model-v2/workspaces/3d-stage1
```

On another machine, first obtain a checkout containing the pinned commit:

```bash
git clone --branch xss-gdd-text2model --single-branch \
  https://code.alipay.com/AReaL/GameAgent.git /path/to/GameAgent
bash examples/3d_bench/run.sh init /path/to/GameAgent
```

Repository access requires your normal code.alipay.com credentials. No access
tokens are stored in these scripts or runtime metadata.

`init` and `prepare` require new destinations and never overwrite an existing
dataset or workspace. Skip these steps when reusing the setup. `prepare`
downloads npm dependencies. `selftest` runs upstream positive/negative fixtures
and the viewer build; it is not a Benchmark score. The template model is an
empty placeholder and is expected to fail candidate validation.

After a trusted implementation replaces **only the createModel body** in the
workspace's `src/model.js`, preserving the executor-owned prefix:

```bash
bash examples/3d_bench/run.sh validate \
  .rsi/datasets/3d-model-v2/workspaces/3d-stage1 \
  .rsi/datasets/3d-model-v2/outputs/validation-1
CHROMIUM_BIN=/path/to/chromium \
  bash examples/3d_bench/run.sh capture \
  .rsi/datasets/3d-model-v2/workspaces/3d-stage1 \
  .rsi/datasets/3d-model-v2/outputs/capture-1
```

Output directories must be new, with existing parents; relative paths resolve
from the invoking directory. Validation produces `model-validation.json`;
capture produces model/collision composites, pixel smoke evidence, and logs.
Both record `run.json` with source/snapshot hashes, environment and execution
status. Capture additionally requires ImageMagick 7 (`magick`), DejaVu Sans
Bold, curl, Chromium and a free port (default 4173; override `MODEL_VIEW_PORT`).
The upstream capture uses 1280x800 front/right/back views and software rendering,
not the 1024x1024 three-quarter views described by Downloads/verify.

## Boundaries to resolve before Benchmark integration

1. This validator does not read TARGET.md or award a 0-100 visual score. It
   checks Contract V2 and model-declared structure. Passing does not establish
   that the task was solved. Downloads/verify is a separate scoring design.
2. The 100 TARGET.md files describe another delivery contract (standalone
   create/update/dispose exports). Contract V2 preserves an embedded helper
   prefix and uses its own actions/collision interface. A public, fixed task
   adaptation policy is needed before comparing Harnesses; do not silently
   treat the contracts as interchangeable.
3. This local runner is **not a security sandbox**. The upstream validator
   imports candidate JavaScript before some source checks. Use trusted fixtures
   only at this stage; isolate generated code, credentials, network and resource
   limits before unattended evaluation. Hash checks are drift detection, not
   protection from an adversarial process or modified node_modules.
4. Keep the RSI optimization target as Harness. Generated model.js files are
   per-task outputs, not a reason to switch to ArtifactEvolutionWorkflow.

Stage 1 fixes runtime bytes and dependencies. Browser, OS, fonts and rendering
driver also affect screenshots; record/pin those in a later isolated runtime
before claiming cross-machine reproducibility.
