# Terminal-Bench 2.1 / EnvArena

This is a record of the legacy Harness evolution setup on `tb21-terminus2-full@2.1.1`:

- Model: `gpt-6-astra-4ipic@1.0.0`
- Initial Harness: `terminus-2-tb21@1.0.10`
- Reward: `tb2-reward@4.0.16`, pinned by the Benchmark
- Mutable components: `system_prompt` and supported `model_parameters`
- Model weights: immutable

The old `rsi tui` preset and fixed-round workflow were removed in the native ResearchApp
transition. `experiment.yaml` is retained for historical reference; it cannot be launched by the
current CLI. A new TB2.1 adapter must pin the same Arena identities through ResearchApp before
another experiment can be run.
