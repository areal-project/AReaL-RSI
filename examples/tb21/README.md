# Terminal-Bench 2.1 / EnvArena

This example starts Harness evolution on `tb21-terminus2-full@2.1.1` with:

- Model: `gpt-6-astra-4ipic@1.0.0`
- Initial Harness: `terminus-2-tb21@1.0.10`
- Reward: `tb2-reward@4.0.16`, pinned by the Benchmark
- Mutable components: `system_prompt` and supported `model_parameters`
- Model weights: immutable

Start the TUI from the repository root:

```bash
uv run --env-file .env --locked rsi tui
```

Then say:

```text
使用tb21 preset进行三轮Harness优化
```

The shortcut selects the built-in `tb21` preset and defaults to three iterations per research
line. The TUI displays the frozen plan first. Reply `确认` to submit the background worker and
enter the Arena optimization loop.

The number of research lines comes from `RSI_RESEARCH_AGENT_COUNT` when the TUI starts. For the
seven-line setup used by the original Main Run:

```bash
export RSI_RESEARCH_AGENT_COUNT=7
uv run --env-file .env --locked rsi tui
```

Each iteration runs the fixed TRAIN subset, asks a ResearchAgent for one reusable Harness change,
publishes an immutable candidate, evaluates the fixed VALIDATE subset, and selects the next version.
The shortcut starts this screening workflow; it does not automatically submit a final 89-task Job.
