# Usage Guide

See the [project README](../README.md#getting-started) for installation and first launch.

## Configuration and Permissions

Put ordinary settings in `rsi.toml` and secrets in `.env`. Defaults are model `gpt-6.1-sol`, effort `xhigh`,
and API endpoint `https://htnn-gateway.alipay.com/arenaonlinegateway/api`.
See [README configuration](../README.md#configuration) for complete examples, precedence, and migration notes.
At startup, specify `--config /path/rsi.toml`, `--env-file /path/.env`, or `--data-root /path/to/data` as needed.
User defaults in `~/.config/areal-rsi/` load before project configuration in the workspace.
Without `--workspace`, the launch directory is the project directory; explicitly selected configuration files take precedence.
For `areal-rsi research`, `--config` selects problem JSON; use `--session-config` for session TOML.
`areal-rsi config` displays effective settings without connecting to a model; `/config` displays current session settings in the TUI.
Neither command displays secrets.
API connection settings, history, and Codex settings use RSI's separate data directory; they do not overwrite your regular Codex
login or `~/.codex/config.toml`. The API key is read only from the child process environment, never written to session JSON or command arguments.
Project instructions and tool configuration in the working directory still follow native Codex rules.

New sessions default to `sandbox = "danger-full-access"` and `approval_policy = "never"` in `[session]`:
they can access any directory permitted by the current OS user, without approval prompts. The workspace is a starting directory, not a permission boundary.
Regular mode, Interaction, Leader, and experience summarization share the same permission configuration; the summarization prompt still requires extracting experience only.
To restrict access, explicitly set `workspace-write` or `read-only` in `rsi.toml`. Existing sessions retain their saved permissions.
After changing defaults, exit and restart the TUI to create a new session; do not use `--resume` to restore the old configuration.

## Workflow

1. Start with a regular conversation: discuss requirements, understand the domain, or make small fixes. Enter sends; Shift+Enter inserts a newline.
2. Enter `/rsi`, or `/rsi Optimize the page based on our discussion`.
3. Interaction continues from the previous conversation to clarify the goal, constraints, and completion criteria. An explicit request to start triggers handoff;
   you can also enter `/start` after the task has been saved.
4. Leader executes. If a turn ends before the goal is complete, the host automatically continues.
5. `/chat` returns to regular conversation with the latest work context.

| Action | Command |
| --- | --- |
| Add requirements during execution | Send a message directly; uses native steer |
| Attach a reference image | `/image /path/reference.png`, then send a description; or append the description after the path |
| Pause / resume / stop | Esc or `/pause`; `/resume`; `/stop` |
| Answer questions / permission requests when approvals are explicitly enabled | Select or type in the upstream DSH dialog; approval defaults to rejection |
| Expand tools | Ctrl+O |
| Summarize experience / view documents | `/learn [review feedback]`; `/docs` |
| View / switch summarization mode | `/learn-mode`; `/learn-mode auto`; `/learn-mode guided` |
| View the current goal | `/status` |
| View current configuration without secrets | `/config` |
| View / remove / set budgets | `/budget`; `/budget off`; `/budget <tokens\|off> <seconds\|off>` |
| View API models / switch model | `/model`; `/model model-name` |
| View / set reasoning effort | `/effort`; `/effort low\|medium\|high\|xhigh` |
| Session history | `/sessions`, `/open run-ID`, or `--resume run-ID` at startup |
| New session / save and exit | `/new`; Ctrl+Q or `/quit` |

Exiting the TUI pauses unfinished work. Reopening does not execute automatically; use `/resume` to continue explicitly.
Plugin runs are owned by a separate background service and continue when the external harness closes;
see [plugin usage](plugins.md) for controls.
New goals have no token or execution-time limits by default, and Interaction does not set budgets. To impose limits after the goal is saved, enter
`/budget 100000 3600` (total token limit and active execution seconds, including usage already incurred); use `off` for either value to leave it unlimited.
After exhaustion, use `/budget off` to remove limits, or raise them, then `/resume` to continue the original goal and context. Usage is not reset.

`/model` queries `/models` using the current session's Base URL and API key, displaying the models actually returned by the endpoint.
The TUI remains usable during the query. If listing is unsupported or the connection fails, the reason is displayed; you can still set a model manually.
The list may include image or other non-Agent models; it does not imply that every model supports the tool calling Codex requires.

`/model model-name` and `/effort high` preserve the current context and take effect on the next turn; execution or summarization already in progress uses its original settings.
Settings are saved in the current session and inherited by subsequent `/new` sessions, without changing `rsi.toml`, `.env`, or regular Codex configuration.
This version offers four effort levels: low, medium, high, and xhigh. Support depends on the selected model and API service.

Regular conversation → Interaction uses the same native thread; Interaction → Leader uses a native fork,
inheriting prior text, tool results, images, and compacted history. Task configuration clarifies the goal without replacing the full conversation.
Long sessions remain subject to the model window and Codex compaction; not every original detail is guaranteed to remain resident forever.

This version's UI supports streaming output, common tool events, image-path input, permission confirmation, and recovery, but does not replicate
all Codex TUI interactions. The separate configuration directory does not automatically inherit regular Codex plugins or MCP configuration;
browser tools for frontend tasks must be configured in that separate environment. This version does not implement its own browser or visual verifier.

## Continued Execution and Experience

After a Leader turn ends, execution continues if the goal is incomplete. It stops on budget exhaustion, missing user input, failure, or explicit pause.
System idle sleep is inhibited by default during execution and experience curation, then released when no task is active. macOS uses `caffeinate`; Linux uses
`systemd-inhibit` when available. Closing the embedded TUI pauses work; plugin runs continue in the local service.
Closing the lid, shutting down, or losing network access can still interrupt execution.

The default, `/learn-mode auto`, starts an independent summarization session after an RSI goal completes.
`/learn-mode guided` waits for your review feedback after completion without starting the summarization model. After `/learn your feedback`,
the curator summarizes using native task history and your feedback. Use `/learn` alone to skip additional feedback and summarize from history.
The waiting state and mode are saved with the session, so reopening still shows the reminder; `/new` inherits the current mode.
Switching to `auto` while waiting starts summarization immediately; if summarization has already started, wait for it to finish before switching.
You can also use `/learn` after regular work or a task pause, optionally including feedback such as test results.
The curator extracts reusable methods from source history according to its role instructions, without continuing workspace changes. It inherits session permissions, without an additional read-only sandbox.
During summarization, the model checks the latest catalog and the latest body of this task's previous summary to decide where the content belongs: merge the same topic and preserve its filename,
and create a new document only for a distinct applicability scope or method. Updates deduplicate content and correct old conclusions instead of appending a review log each round.
When there are no new conclusions, the existing document is returned unchanged and the host skips rewriting it. The recommended body length is at most 1,500 characters,
with a hard limit of 4,000 characters (80 for the title and 200 for the description). Overlong content is not published and must be shortened before retrying.
The experience library has one regular Markdown file per topic: `RSI_DATA_ROOT/experience/<topic>.md`,
for example `browser-game-validation.md`. The Agent, `/docs`, and summary notifications use the same path. Updates overwrite it directly,
without historical versions, hash directories, numbered files, or symlinks; filenames use meaningful lowercase English phrases.
Regular work and Leader refresh the document catalog each new turn, reading the latest original content according to its applicability.
They do not copy conversations, code, or answers from previous tasks. Old content already in the conversation is not automatically erased.
Interaction focuses on requirements and does not promote experience suggestions into task constraints.
Summary failures display an error and can be retried with `/learn`; repeated requests for the same task reuse the published result by default.
New feedback triggers another summary. If the previous one is still running, the UI asks you to wait and submit again after completion, rather than silently dropping the feedback.

In `rsi.toml` under `[session]`, `auto_learn = false` defaults new sessions to guided mode,
`use_experience = false` disables experience document use, and `keep_awake = false` disables sleep inhibition.
Legacy `RSI_AUTO_LEARN`, `RSI_USE_EXPERIENCE`, and `RSI_KEEP_AWAKE` environment variables still override file settings.
Summarization makes additional API calls, with a maximum wait of 240 seconds for its turn (connection requests have separate timeouts).
Regular tasks are not automatically summarized after every turn.
Documents must still be validated against the current task; a single experience does not guarantee correctness.
