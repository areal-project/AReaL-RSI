import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  Service,
  OWNER,
  type Harness,
  type StartOptions,
  type ResumeOptions,
  type RunHandle,
  type RunResult,
  type AgentEvent,
} from "../packages/core/src/index.js";
export class Controlled implements Harness {
  name = "codex";
  calls: {
    options: StartOptions | ResumeOptions;
    kind: string;
    finish: (result?: Partial<RunResult>) => void;
  }[] = [];
  start(o: StartOptions) {
    return this.make(o, "fork");
  }
  resume(o: ResumeOptions) {
    return this.make(o, "resume");
  }
  private handles = new WeakMap<RunHandle, () => void>();
  make(o: StartOptions | ResumeOptions, kind: string): RunHandle {
    let finish!: (r: RunResult) => void;
    const done = new Promise<RunResult>((r) => {
      finish = r;
    });
    const id = this.calls.length + 1;
    this.calls.push({
      options: o,
      kind,
      finish: (r = {}) =>
        finish({
          exitCode: 0,
          finalOutput: "完成 " + id,
          usage: { input_tokens: 10 },
          ...r,
        }),
    });
    const events = {
      async *[Symbol.asyncIterator]() {
        yield {
          type: "session_started",
          session_id: "session-" + id,
        } as AgentEvent;
        await done;
        yield { type: "assistant_message", text: "完成 " + id } as AgentEvent;
      },
    };
    const h = { sessionId: Promise.resolve("session-" + id), events, done };
    this.handles.set(h, () =>
      finish({
        exitCode: 143,
        finalOutput: "",
        usage: {},
        error: "terminated",
      }),
    );
    return h;
  }
  async stop(h: RunHandle) {
    this.handles.get(h)?.();
    await h.done;
  }
}
export async function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "detached-test-"));
  const h = new Controlled(),
    s = new Service(
      join(dir, "data"),
      { codex: h },
      "/inner.js",
      process.cwd() + "/skills/hamlet-agent",
    );
  s.url = "http://127.0.0.1:1234";
  const g = await s.call("group_create", { name: "开发" }, OWNER);
  const agent = async (p: any = {}) =>
    s.call(
      "agent_create",
      { group: g.id, role_name: "开发者", workdir: join(dir, "work"), ...p },
      OWNER,
    );
  return {
    dir,
    h,
    s,
    g,
    agent,
    async close() {
      await s.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}
export async function until(fn: () => boolean, timeout = 4000) {
  const configured = Number(process.env.HAMLET_TEST_WAIT_MS);
  if (Number.isFinite(configured) && configured > timeout) timeout = configured;
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > timeout) throw Error("等待超时");
    await new Promise((r) => setTimeout(r, 10));
  }
}
