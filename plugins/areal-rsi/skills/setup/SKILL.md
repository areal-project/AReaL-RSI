---
name: setup
description: Set up or repair the AReaL-RSI plugin's local CLI installation. Use during plugin onboarding or when the user requests AReaL-RSI installation or update help.
---

Run `python3 <plugin-root>/scripts/launch.py --setup`, resolving plugin-root from
this skill's location (two directories above this skill's directory). The launcher
installs or updates the CLI automatically from the repository's main branch.
It installs into a private environment and preserves running services.

If uv, Git, or Python 3 are missing, report that prerequisite and its installation
instructions. Do not modify the host's Codex configuration. Ask the user to put
`RSI_API_KEY` in `~/.config/areal-rsi/.env` if no API key is configured; never request
the key in conversation. Codex CLI must also be available for RSI's own runtime.

After setup, reconnect/reload the plugin's MCP server if its initial connection
timed out during the first download. Report the CLI location and any update error.
An existing service keeps its installed runtime until stopped; do not force-stop
active work to apply an update.
