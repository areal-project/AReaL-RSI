import type {
  Harness,
  StartOptions,
  ResumeOptions,
  AgentEvent,
} from "../../core/src/types.js";
import { runtimeEnv } from "../../core/src/runtime.js";
import { runProcess, stopProcess } from "../../harness-codex/src/process.js";
export function claudeArgs(o: StartOptions | ResumeOptions) {
  const args = [
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
    // Child collaboration uses CLI/HTTP; do not inherit server registrations.
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
  ];
  if ("sessionId" in o) args.push("--resume", o.sessionId);
  else args.push("--append-system-prompt", o.systemPrompt);
  if (o.model) args.push("--model", o.model);
  if (o.options?.effort) args.push("--effort", String(o.options.effort));
  return args;
}
export function normalizeClaude(r: any): AgentEvent[] {
  if (r.type === "system" && r.subtype === "init")
    return [{ type: "session_started", session_id: r.session_id }];
  if (r.type === "assistant")
    return (r.message?.content || []).map((c: any) =>
      c.type === "text"
        ? { type: "assistant_message", text: c.text }
        : c.type === "thinking"
          ? { type: "reasoning", text: c.thinking }
          : { type: "tool_call", name: c.name, input: c.input },
    );
  if (r.type === "user")
    return (r.message?.content || [])
      .filter((c: any) => c.type === "tool_result")
      .map((c: any) => ({ ...c, type: "tool_result" }));
  if (r.type === "result")
    return [
      ...(r.result
        ? [{ type: "assistant_message" as const, text: r.result }]
        : []),
      { type: "usage" as const, usage: r.usage || {} },
      ...(r.is_error
        ? [
            {
              type: "error" as const,
              message: r.result || r.errors?.join("\n") || r.subtype,
            },
          ]
        : []),
    ];
  return [];
}
export class ClaudeHarness implements Harness {
  name = "claude";
  constructor(public command = runtimeEnv("CLAUDE_BIN") || "claude") {}
  start(o: StartOptions) {
    return this.launch(o);
  }
  resume(o: ResumeOptions) {
    return this.launch(o);
  }
  private launch(o: StartOptions | ResumeOptions) {
    return runProcess(
      this.command,
      claudeArgs(o),
      o.cwd,
      o.env,
      o.prompt,
      normalizeClaude,
      (r) => r.session_id,
    );
  }
  stop = stopProcess;
}
