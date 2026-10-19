# Third-party components

AReaL-RSI's original code is licensed under Apache-2.0; see `LICENSE`.
Third-party code retains its own license.

## Vendored DSH terminal components

- Source: https://github.com/deepseek-ai/deepseek-harness
- Commit: `7248b5ec8f8769f882f12fd521504fa48e97bcf3`
- Original directory: `packages/ui/tui/src`
- Copyright (c) 2026, DeepSeek
- License: BSD-3-Clause, reproduced in `src/areal_rsi/ui/dsh/upstream/LICENSE`

The original component files and pi-tui patch are retained under
`src/areal_rsi/ui/dsh/upstream/`. `provenance.json` records their original paths
and SHA-256 digests. `vendor.mjs` bundles these components; the same BSD license
applies to the incorporated code. The license and this notice accompany both
source and wheel distributions.

## Separately installed dependencies

The terminal installs the pinned npm dependencies from `package-lock.json`,
including pi-tui (MIT), diff (BSD-3-Clause), and saxes (ISC). Their licenses are
included in their installed packages. Python dependencies are declared in
`pyproject.toml` and locked in `uv.lock`. Codex CLI and the optional AReaL training
backend are installed separately and retain their respective licenses.
