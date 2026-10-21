export const VERSION = "0.1.2";
export type Row = Record<string, any>;
export type Actor =
  | { kind: "owner" }
  | { kind: "instance"; id: string }
  | { kind: "doorbell"; id: string };
export interface AgentEvent {
  type:
    | "session_started"
    | "assistant_message"
    | "reasoning"
    | "tool_call"
    | "tool_result"
    | "command"
    | "file_change"
    | "usage"
    | "error"
    | "run_completed"
    | "warning";
  timestamp?: string;
  [key: string]: unknown;
}
export interface StartOptions {
  cwd: string;
  systemPrompt: string;
  prompt: string;
  model?: string;
  options?: Row;
  env: Record<string, string>;
}
export interface ResumeOptions extends Omit<StartOptions, "systemPrompt"> {
  sessionId: string;
}
export interface RunResult {
  exitCode: number;
  finalOutput: string;
  usage: Row;
  error?: string;
}
export interface RunHandle {
  sessionId: Promise<string>;
  events: AsyncIterable<AgentEvent>;
  done: Promise<RunResult>;
  pid?: number;
  rawEvents?: AsyncIterable<unknown>;
}
export interface Harness {
  name: string;
  start(opts: StartOptions): RunHandle;
  resume(opts: ResumeOptions): RunHandle;
  stop(handle: RunHandle): Promise<void>;
}
export class AppError extends Error {
  constructor(
    message: string,
    public statusCode = 400,
    public details?: unknown,
  ) {
    super(message);
  }
}
export const now = () => new Date().toISOString();
export function render(tpl: string, vars: Row): string {
  return tpl.replace(/\{\{\s*([^{}]+?)\s*\}\}/g, (_, key) => {
    if (!Object.hasOwn(vars, key)) throw new AppError(`模板缺少变量: ${key}`);
    return String(vars[key]);
  });
}
