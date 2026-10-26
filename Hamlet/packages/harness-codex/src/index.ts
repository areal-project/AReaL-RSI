import type {
  Harness,
  StartOptions,
  ResumeOptions,
  AgentEvent,
} from "../../core/src/types.js";
import { runtimeEnv } from "../../core/src/runtime.js";
import { runProcess, stopProcess } from "./process.js";
import { assertCodexShellEnvironment } from "./environment.js";
function toml(v: unknown): string {
  if (v === null) throw Error("TOML 不支持 null");
  if (Array.isArray(v)) return `[${v.map(toml).join(",")}]`;
  if (typeof v === "object")
    return `{${Object.entries(v as object)
      .map(([k, x]) => `${JSON.stringify(k)} = ${toml(x)}`)
      .join(",")}}`;
  return JSON.stringify(v);
}
export function codexArgs(o: StartOptions | ResumeOptions): string[] {
  const resume = "sessionId" in o;
  const args = [
    "exec",
    ...(resume ? ["resume", o.sessionId] : []),
    "--json",
    "--dangerously-bypass-approvals-and-sandbox",
    "--skip-git-repo-check",
  ];
  if (o.model) args.push("-m", o.model);
  const settings: Record<string, unknown> = { ...o.options };
  if (!resume)
    settings.developer_instructions = (o as StartOptions).systemPrompt;
  for (const [k, v] of Object.entries(settings)) {
    if (!/^[a-zA-Z0-9_.-]+$/.test(k)) throw Error("无效的 harness 配置键");
    args.push("-c", `${k}=${toml(v)}`);
  }
  if (o.env.HAMLET_AGENT_TOOLS) {
    args.push(
      "-c",
      `mcp_servers.hamlet=${toml({
        command: o.env.HAMLET_NODE || process.execPath,
        args: [o.env.HAMLET_AGENT_TOOLS],
        env_vars: ["HAMLET_URL", "HAMLET_TOKEN", "HAMLET_INSTANCE_ID"],
        enabled: true,
        required: true,
        startup_timeout_sec: 30,
        tool_timeout_sec: 90,
        enabled_tools: ["message_to", "message_force_push"],
      })}`,
    );
  }
  args.push("-");
  return args;
}
export function normalize(raw: any): AgentEvent[] {
  switch (raw.type) {
    case "thread.started":
      return [{ type: "session_started", session_id: raw.thread_id }];
    case "turn.completed":
      return [{ type: "usage", usage: raw.usage }];
    case "error":
    case "turn.failed":
      return [
        {
          type: "error",
          message: raw.message || raw.error?.message || "Codex turn failed",
        },
      ];
    case "item.started":
    case "item.updated":
    case "item.completed": {
      const i = raw.item || {},
        phase = raw.type.split(".")[1];
      switch (i.type) {
        case "agent_message":
          return phase === "completed"
            ? [{ type: "assistant_message", text: i.text }]
            : [];
        case "reasoning":
          return [{ type: "reasoning", text: i.text, phase }];
        case "command_execution":
          return [
            {
              type: "command",
              command: i.command,
              output: i.aggregated_output,
              exit_code: i.exit_code,
              phase,
            },
          ];
        case "file_change":
          return [{ type: "file_change", changes: i.changes, phase }];
        case "mcp_tool_call":
        case "web_search":
          return [
            {
              type: phase === "completed" ? "tool_result" : "tool_call",
              ...i,
              phase,
              harness_type: i.type,
            } as AgentEvent,
          ].map((e) => ({
            ...e,
            type: phase === "completed" ? "tool_result" : "tool_call",
          }));
        default:
          return [];
      }
    }
    default:
      return [];
  }
}
export class CodexHarness implements Harness {
  name = "codex";
  constructor(public command = runtimeEnv("CODEX_BIN") || "codex") {}
  start(o: StartOptions) {
    return this.launch(o);
  }
  resume(o: ResumeOptions) {
    return this.launch(o);
  }
  private launch(o: StartOptions | ResumeOptions) {
    const args = codexArgs(o);
    assertCodexShellEnvironment(this.command, args, o);
    return runProcess(
      this.command,
      args,
      o.cwd,
      o.env,
      o.prompt,
      normalize,
      (r) => (r.type === "thread.started" ? r.thread_id : undefined),
    );
  }
  stop = stopProcess;
}
