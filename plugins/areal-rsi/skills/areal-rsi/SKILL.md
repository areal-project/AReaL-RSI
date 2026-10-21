---
name: areal-rsi
description: Start, discuss, observe, steer, resume, or review a persistent goal in AReaL-RSI. Use when the user requests AReaL-RSI or asks to delegate work to its independent runtime, including /areal-rsi. Do not use for ordinary coding tasks unless the user chooses RSI.
---

AReaL-RSI owns its agents, native Codex history, goal loop, research evaluations,
and experience library. You are its entry point and observer.

1. For a new run, call `rsi_create` with the absolute workspace, the user's request,
   and a concise context summary: goal, constraints, decisions, work already done,
   relevant files/images, unresolved questions. Preserve nuance; state uncertainty.
   Never include credentials or claim the full host conversation was transferred.
   Use `mode=rsi` for goal discussion, or `mode=chat` for ordinary RSI-owned work.
   On reconnect, find the existing run with `rsi_list`; do not create a replacement.
2. Every mutation needs a unique `request_id`. Save it before calling, along with
   its exact arguments. A receipt with `status=running` means accepted, not finished:
   use `rsi_request` to wait up to 20 seconds. On timeout/disconnection retry only
   with the same ID and arguments. For `interrupted`, inspect run/files and ask for
   clarification if the outcome is uncertain; never blindly reissue side effects.
3. Save the returned `run_id`. Call `rsi_observe` from cursor 0, then pass each
   returned cursor to the next observation; drain `has_more` pages. Show concise
   progress and relay Interaction's questions. Forward user replies via `rsi_send`.
   For native prompts, use `rsi_answer` with the current `prompt_id` and the user's
   actual answers/approval. Never approve on their behalf or reuse expired prompts.
4. Internal Interaction clarifies and saves the task; internal Leader executes it.
   Forward an explicit request to begin, or use `rsi_control(action=start)` after
   the task is saved and the user authorizes execution. Do not perform the same
   work concurrently in the host or mark the internal goal complete yourself.
5. While observing, use `wait_seconds=20` instead of rapid polling. Forward new user
   instructions and pause/resume/stop requests. Leaving the host leaves work running.
   If the user only requests a status update, return the status; no perpetual poll is
   required. If asked to monitor to completion, continue until completed, blocked,
   paused, failed, stopped, or budget limited. Report the actual state and evidence.
6. `rsi_configure` sets model/effort, user-requested budgets and `auto`/`guided`
   learning. `rsi_learn` accepts optional review feedback. `rsi_experience` lists or
   reads semantic Markdown documents. Research bindings are optional; use
   `rsi_research` for preparation/final evaluation and `rsi_query` for candidates.

The plugin launcher automatically installs/updates its own CLI when MCP loads.
If tools are unavailable, use the bundled setup skill to diagnose prerequisites
or finish a first download, then reconnect MCP. A local API key and Codex CLI are
required. Do not force-stop active work or alter the host's Codex configuration.
