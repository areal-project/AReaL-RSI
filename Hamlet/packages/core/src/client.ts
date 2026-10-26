import {
  readFileSync,
  existsSync,
  mkdirSync,
  openSync,
  closeSync,
} from "node:fs";
import { join, dirname, resolve } from "node:path";

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { Auth } from "./auth.js";
import { dataDir, runtimeEnv } from "./runtime.js";
export { dataDir } from "./runtime.js";
export class Client {
  constructor(
    public url: string,
    public token: string,
  ) {}
  async request(path: string, body?: unknown) {
    const r = await fetch(this.url + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
    const data = (await r.json()) as any;
    if (!r.ok) {
      const e = new Error(data.error || r.statusText) as any;
      e.details = data.details;
      e.statusCode = r.status;
      throw e;
    }
    return data;
  }
  call(name: string, p: Record<string, unknown> = {}) {
    return this.request("/api/tools/" + name, p);
  }
}
export async function connect(autoStart = true): Promise<Client> {
  const url = runtimeEnv("URL"),
    token = runtimeEnv("TOKEN");
  if (url && token) return new Client(url, token);
  // A partial child context must never silently acquire local Owner authority.
  if (url || token || runtimeEnv("INSTANCE_ID"))
    throw new Error(
      "Hamlet 实例连接信息不完整；需要 URL 和 TOKEN，禁止回退 Owner",
    );
  const bellURL = process.env.DOORBELL_URL,
    bellToken = process.env.DOORBELL_TOKEN;
  if (bellURL && bellToken)
    return new Client(new URL(bellURL).origin, bellToken);
  if (bellURL || bellToken || process.env.DOORBELL_AGENT_ID)
    throw new Error("doorbell 连接信息不完整；禁止回退 Owner");
  const dir = dataDir();
  const discover = async () => {
    try {
      const info = JSON.parse(readFileSync(join(dir, "daemon.json"), "utf8"));
      const r = await fetch(info.url + "/health", {
        signal: AbortSignal.timeout(500),
      });
      const h = (await r.json()) as any;
      if (r.ok && h.pid === info.pid)
        return new Client(info.url, new Auth(dir).ownerToken);
    } catch {}
    return undefined;
  };
  const found = await discover();
  if (found) return found;
  if (!autoStart) throw new Error("daemon 未运行");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const script = join(root, "packages/daemon/dist/index.js");
  if (!existsSync(script)) throw new Error("请先运行 pnpm build");
  const fd = openSync(join(dir, "daemon.log"), "a", 0o600);
  const child = spawn(process.execPath, [script, "--serve"], {
    detached: true,
    stdio: ["ignore", fd, fd],
    env: { ...process.env, HAMLET_HOME: dir, DETACHED_HOME: dir },
  });
  closeSync(fd);
  child.unref();
  let error: Error | undefined;
  child.on("error", (e) => {
    error = e;
  });
  for (let n = 0; n < 100; n++) {
    await new Promise((r) => setTimeout(r, 100));
    if (error) throw error;
    const c = await discover();
    if (c) return c;
  }
  throw new Error(`daemon 启动超时；查看 ${join(dir, "daemon.log")}`);
}
