# Bubblewrap benchmark sessions

This launcher creates a fresh, history-free workspace and runs commands inside a
Bubblewrap mount namespace. The original runtime, prior runs, verifier, hidden
data, tests and `solution/` directories are absent from the sandbox.

Create one session and start an interactive Codex CLI:

```bash
python examples/scale_rsi_bench/sandbox/session.py create \
  swarm /tmp/rsi-swarm-session

set -a; source .env; set +a
python examples/scale_rsi_bench/sandbox/session.py codex \
  /tmp/rsi-swarm-session
```

The Codex process has a private `HOME` and `CODEX_HOME`; its commands use
`danger-full-access` inside the outer Bubblewrap boundary. Network access is
shared so the model API remains reachable. Only a small credential allowlist is
inherited.

Run a non-Codex command for diagnostics:

```bash
python examples/scale_rsi_bench/sandbox/session.py run \
  /tmp/rsi-swarm-session -- bash -lc 'pwd; find . -maxdepth 2 -type f'
```

Direct `sbatch`, `srun`, `salloc` and `scancel` are deliberately hidden. A Slurm
job does not inherit Bubblewrap's mount namespace and would otherwise be able to
read private benchmark history. Benchmark submission must go through a scoped
host bridge that accepts a fixed benchmark action and snapshots only this
session's submission.

## Run as an RSI research task

`task.py` creates a clean session, registers its fixed `experiment` action with
the workbench, and starts the research. For example:

```bash
set -a; source .env; set +a
python examples/scale_rsi_bench/sandbox/task.py nanogpt --port 28999
```

The first argument can be `nanogpt`, `opsd`, `jailbreak`, or `swarm`. Useful
limits are explicit launcher options:

```bash
python examples/scale_rsi_bench/sandbox/task.py swarm \
  --port 28999 --hours 3 --max-jobs 5 --max-agents 1
```

The command prints the run ID, web page, and shared workspace. `--max-agents 1`
means one Research Agent plus the coordinating RSI Agent. Both processes enter
separate Bubblewrap namespaces, but their `/workspace` view refers to the same
writable benchmark directory, so code and submission changes are immediately
visible to both. Concurrent edits to the same file still need coordination.

Resume the same task without recreating or restaging it:

```bash
python examples/scale_rsi_bench/sandbox/task.py swarm \
  --port 28999 --resume research_swarm_xxx
```

Inside the research, agents use the RSI `run_job` tool with command name
`experiment`. The host bridge snapshots the shared workspace and invokes only
the selected benchmark recipe. GPU containers receive that snapshot and the
explicit model/data mounts; the repository, other sessions, and prior result
directories are not mounted.
