<div align="center">

# AReaL-RSI

**Pursue persistent goals. Build reusable research experience.**

[Paper](https://arxiv.org/abs/2610.18083) · [Getting Started](#getting-started) · [User Guide](docs/usage.md) · [Research Examples](#research-examples) · [AReaL](https://github.com/areal-project/AReaL)

</div>

AReaL-RSI is an agent framework for sustained research and iterative improvement, built on
Codex's native execution runtime. Define a goal, let the agent execute and validate across
turns, and distill useful methods into experience for future work.

Our objective: **better results within the same time budget, or less time to reach the same result.**

## Highlights

- **Persistent execution:** goal clarification, context-preserving handoff, steering, pause/resume, and budget controls.
- **Reusable experience:** editable Markdown documents, automatic curation, and human review feedback.
- **Auditable research:** experiment records, evaluation checks, trajectories, and [AReaL](https://github.com/areal-project/AReaL) training integration.
- **Independent examples:** task-specific data, policies, and protocols stay outside the general-purpose core.

## Getting Started

Requires Python **3.11+**, [uv](https://docs.astral.sh/uv/), and Codex CLI **0.162.1+**.
The optional TUI additionally needs Node.js **22+**, npm and `patch`.
Plugin/MCP and headless use do not need Node.js. Ordinary work needs no local GPU.

```bash
git clone https://github.com/areal-project/AReaL-RSI.git
cd AReaL-RSI
npm install -g @openai/codex@0.162.1
uv tool install --editable .
cp -n .env.example .env
```

Set your API key in `.env`:

```dotenv
RSI_API_KEY=your-api-key
```

The defaults are **`gpt-6.1-sol` / `xhigh`** at
`https://htnn-gateway.alipay.com/arenaonlinegateway/api`.
Use a key accepted by that gateway, or configure another Responses API provider below. Then launch:

```bash
areal-rsi
# Or work in another project:
areal-rsi --workspace /absolute/path/to/project
```

The first launch needs network access for TUI dependencies. If the command is not found,
run `uv tool update-shell` and reopen your terminal.

Start a goal in the terminal:

```text
/rsi Improve data loading performance. Measure a baseline, preserve outputs,
and report tests and timing comparisons.
```

Discuss the constraints, then ask the agent to begin or use `/start` once the task is saved.
Use `/pause` and `/resume` to control execution, `/budget` to manage limits, and `/learn` to
curate experience. See the [user guide](docs/usage.md) for all commands and recovery behavior.

## Use from your own harness

The plugin forwards context and user messages to an **independent RSI runtime**.
Interaction discusses the task; Leader executes it. Closing the outer harness does not
stop a plugin-owned run. Your normal Codex login, settings and history stay separate.

With Python 3, Git, uv and Codex available, install the plugin (Codex CLI 0.163.0
tested; the Git distribution below requires this change to be merged to main):

```bash
codex plugin marketplace add areal-project/AReaL-RSI --ref main
codex plugin add areal-rsi@areal-rsi
```

No separate CLI installation is needed. On MCP load, the plugin checks the latest
main commit and installs/updates its private CLI under `~/.local/share/areal-rsi/cli`.
Existing services keep their original environment; stop the service and reconnect
to use the new runtime. Network failures keep a previous usable installation.
For local unmerged development, register this checkout instead and export
`RSI_CLI_PATH=/absolute/path/to/AReaL-RSI/.venv/bin/areal-rsi` before starting the host.
If the first download exceeds the host's MCP startup timeout, run the plugin's
bundled `setup` skill, then reconnect MCP.

Start a new host session and invoke the `areal-rsi` skill (for example `/areal-rsi` in
hosts supporting that spelling), or ask it to delegate a goal to AReaL-RSI.
The host summarizes its context; it does not transfer its original Codex thread.
Place shared personal credentials in `~/.config/areal-rsi/.env`, or use each workspace's
`.env`. Never put keys in plugin manifests or handoff messages.

The shared bundle is in `plugins/areal-rsi/`. Claude Code can load it with
`claude --plugin-dir /absolute/path/to/AReaL-RSI/plugins/areal-rsi`; other stdio MCP
hosts can use the same `scripts/launch.py mcp` launcher, or an independently
installed `areal-rsi mcp`. See [plugin setup, controls and upgrades](docs/plugins.md).

## Configuration

Use **`rsi.toml` for settings** and **`.env` for secrets**. User defaults live in
`~/.config/areal-rsi/` (`$XDG_CONFIG_HOME/areal-rsi/` when set). Project files come
from the explicit workspace, or the launch directory when no workspace is supplied.
Neither file is required when defaults and exported credentials are sufficient.
To customize settings, copy the complete [example](rsi.example.toml):

```bash
cp -n rsi.example.toml rsi.toml
```

A minimal `rsi.toml` looks like this; every field is optional:

```toml
data_root = "~/.local/share/areal-rsi"

[session]
model = "gpt-6.1-sol"
effort = "xhigh"
base_url = "https://htnn-gateway.alipay.com/arenaonlinegateway/api"
auto_learn = true
```

Other `[session]` settings are `workspace`, `sandbox`, `approval_policy`, `keep_awake`,
`use_experience`, `codex_bin` and `api_key_env`; the example explains their defaults.
Effort accepts `low`, `medium`, `high`, or `xhigh`. Permissions default to
`danger-full-access` / `never`; set `sandbox = "workspace-write"` or `"read-only"`
and `approval_policy = "on-request"` when restrictions are needed.
`auto_learn = false` waits for `/learn` and optional human feedback after a goal completes.
Providers must support the Responses API and tool calling. A custom API path is preserved;
the supplied gateway URL is used as written, without adding `/v1`.

**Precedence:** `--workspace` / `--data-root` → exported environment → project `.env` →
user `.env` → project `rsi.toml` → user `rsi.toml` → built-in defaults. Existing `RSI_*` overrides remain supported:

| TOML setting | Environment override |
| --- | --- |
| `data_root` | `RSI_DATA_ROOT` |
| `session.model`, `session.effort` | `RSI_MODEL`, `RSI_REASONING_EFFORT` |
| `session.base_url`, `session.api_key_env` | `RSI_BASE_URL`, `RSI_API_KEY_ENV` |
| `session.sandbox`, `session.approval_policy` | `RSI_SANDBOX`, `RSI_APPROVAL_POLICY` |
| `session.keep_awake`, `session.use_experience`, `session.auto_learn` | `RSI_KEEP_AWAKE`, `RSI_USE_EXPERIENCE`, `RSI_AUTO_LEARN` |
| `session.codex_bin` | `RSI_CODEX_BIN` |

TOML switches use `true` / `false`; environment switches accept `1` / `0` or `true` / `false`.
Unknown TOML settings and invalid values fail explicitly.

```bash
areal-rsi --config /path/rsi.toml --env-file /path/.env --workspace /path/project
areal-rsi config                       # Effective startup settings; no Codex/model connection
areal-rsi config --resume run-ID       # Settings saved with an existing conversation
```

`--config` and `--env-file` select files independently. `--workspace` selects the working
directory and the default project configuration location. An explicit `--env-file` replaces
both user and project dotenv files. Relative paths in TOML resolve from
that file's directory; relative paths in flags or environment variables resolve from the
launch directory. Session records, experience and the isolated Codex home live under
`data_root`; normal Codex settings are not modified.

For `areal-rsi research`, `--config` selects the research problem's JSON configuration;
use `--session-config /path/rsi.toml` for session settings. `--env-file /dev/null` disables
loading a dotenv file while retaining exported environment variables.

Each new conversation saves its settings. **Resuming uses the saved settings**, while the
API key is resolved from the saved dotenv **paths** and exported environment at connection
time; the key itself is never stored in session JSON. Loading a project does not mutate
process-global environment variables or another session’s configuration. Sessions without
saved dotenv paths use user/workspace `.env` defaults; an old launch-directory-only key
should be moved to the user config directory or explicitly exported before resuming. In the TUI,
`/config` shows current settings without the key; `/model`, `/effort` and `/learn-mode`
update the current conversation and carry over to `/new`. Model/effort changes apply to
the next turn, not active work. These commands do not rewrite `rsi.toml` or `.env`.
Goal budgets are separate: they default to unlimited and are adjusted with `/budget`.

**Upgrading from `.env`-only setup:** move ordinary settings into `rsi.toml`, then remove
their `RSI_*` entries from `.env` so they no longer override TOML or the new model defaults.
Keep your existing data location (for example, `data_root = ".rsi"`) to retain access to
saved conversations and experience. Changing the data path does not migrate existing data.

## Research Examples

TIR and Search implement the same ResearchProblem contract and use the unified research command.

| Example | Description | Resources |
| --- | --- | --- |
| Search research | Model training with fixed retrieval and independently judged evaluations | [Setup](examples/search/README.md) |
| TIR research | Agent-driven, multi-round research on data and training recipes; select on valid, then evaluate on test | [Setup](examples/tir/RESEARCH.md) · [Results](examples/tir/RESULTS.md) |

For multi-round TIR, configure `examples/tir/experiment.json` and `.env` on a Slurm cluster
with Apptainer and an AReaL training environment, then run from the repository root:

```bash
uv sync --locked
source .venv/bin/activate
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
areal-rsi research --problem examples.tir.problem:TIRProblem \
  --config /absolute/tir-experiment.json --objective /absolute/objective.md \
  --root /absolute/runs/tir-001 --env-file /absolute/private.env
```

## Documentation and Development

Detailed guides are currently in Chinese:

[Usage](docs/usage.md) · [Architecture](docs/architecture.md) ·
[API](docs/interfaces/README.md) · [Testing](tests/README.md)

Dependencies flow **UI / examples → infra → api**. The core does not import examples.
The session entry point is `SessionSystem` in `infra/session.py`.
Read [AGENTS.md](AGENTS.md) before contributing, and run:

```bash
uv sync --locked
make check
```

Checks cover Python, native TUI behavior, Ruff, and Mypy; real API calls and GPU training
are separate validations.

## Citation

If AReaL-RSI's research ideas are useful to your work, please cite:

```bibtex
@article{zhou2026arealrsi,
  title   = {{AReaL-RSI}: Adaptive Scaling and Coordination of Recursive Self-Improvement},
  author  = {Zhou, Wenhao and Song, Jiajun and Wang, Haitao and Qiu, Zipeng and
             Peng, Yingqi and Xu, Ruida and Li, Jiale and Li, Wenshuang and
             Xu, Shusheng and Dong, Wei and Dai, Hao and Yang, Tongkai and
             Gao, Yi and Yuan, Binhang},
  journal = {arXiv preprint arXiv:2610.18083},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.18083}
}
```

We thank [AReaL](https://github.com/areal-project/AReaL) for its training infrastructure,
[Codex](https://github.com/openai/codex) for its execution runtime, and
[DSH](https://github.com/deepseek-ai/deepseek-harness) for the terminal components.

## License

Original code is licensed under [Apache-2.0](LICENSE). Vendored components retain
their licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
