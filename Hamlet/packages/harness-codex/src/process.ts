import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import type { AgentEvent, RunHandle, RunResult } from "../../core/src/types.js";
import { childEnvironment } from "../../core/src/runtime.js";
class Stream<T> implements AsyncIterable<T> {
  private values: T[] = [];
  private wake?: () => void;
  private closed = false;
  push(v: T) {
    this.values.push(v);
    this.wake?.();
  }
  end() {
    this.closed = true;
    this.wake?.();
  }
  async *[Symbol.asyncIterator]() {
    while (!this.closed || this.values.length) {
      if (this.values.length) yield this.values.shift()!;
      else
        await new Promise<void>((r) => {
          this.wake = r;
        });
    }
  }
}
const children = new WeakMap<RunHandle, ChildProcess>();
export function runProcess(
  command: string,
  args: string[],
  cwd: string,
  env: Record<string, string>,
  prompt: string,
  parse: (raw: any) => AgentEvent[],
  sessionOf: (raw: any) => string | undefined,
): RunHandle {
  const events = new Stream<AgentEvent>(),
    rawEvents = new Stream<unknown>();
  let finalOutput = "",
    usage = {},
    sessionIdValue = "",
    stderr = "",
    parseError = "";
  let resolveSession!: (s: string) => void, rejectSession!: (e: Error) => void;
  const sessionId = new Promise<string>((res, rej) => {
    resolveSession = res;
    rejectSession = rej;
  });
  void sessionId.catch(() => {});
  const child = spawn(command, args, {
    cwd,
    env: { ...childEnvironment(), ...env },
    stdio: ["pipe", "pipe", "pipe"],
    detached: true,
  });
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    try {
      const raw = JSON.parse(line);
      rawEvents.push(raw);
      const sid = sessionOf(raw);
      if (sid && !sessionIdValue) {
        sessionIdValue = sid;
        resolveSession(sid);
      }
      for (const e of parse(raw)) {
        if (e.type === "assistant_message") finalOutput = String(e.text || "");
        if (e.type === "usage") usage = (e.usage || {}) as any;
        if (e.type === "error")
          parseError = String(e.message || "harness error");
        events.push(e);
      }
    } catch {
      rawEvents.push({ unparsed: line });
    }
  });
  child.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    stderr = (stderr + text).slice(-64000);
    rawEvents.push({ stderr: text });
    events.push({ type: "command", stream: "stderr", output: text });
  });
  child.stdin.on("error", () => {});
  child.stdin.end(prompt);
  const done = new Promise<RunResult>((resolve) => {
    let ended = false;
    const finish = (code: number, error?: string) => {
      if (ended) return;
      ended = true;
      if (!sessionIdValue)
        rejectSession(new Error(error || "harness 未返回会话 ID；会话不可用"));
      events.push({ type: "run_completed", exit_code: code });
      events.end();
      rawEvents.end();
      resolve({
        exitCode: code,
        finalOutput,
        usage,
        error:
          error ||
          parseError ||
          (code !== 0 ? stderr || `harness 退出码 ${code}` : undefined),
      });
    };
    child.on("error", (e) => finish(1, e.message));
    child.on("close", (code) => finish(code ?? 1));
  });
  const handle = { sessionId, events, rawEvents, done, pid: child.pid };
  children.set(handle, child);
  return handle;
}
export async function stopProcess(h: RunHandle) {
  const c = children.get(h);
  if (!c || c.exitCode !== null || c.signalCode !== null) return;
  const kill = (signal: NodeJS.Signals) => {
    try {
      process.kill(-c.pid!, signal);
    } catch {
      c.kill(signal);
    }
  };
  kill("SIGTERM");
  let timer: NodeJS.Timeout | undefined;
  await Promise.race([
    h.done,
    new Promise<void>((resolve) => {
      timer = setTimeout(() => {
        kill("SIGKILL");
        resolve();
      }, 10000);
    }),
  ]);
  clearTimeout(timer);
  await h.done;
}
