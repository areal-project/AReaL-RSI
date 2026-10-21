import Fastify from "fastify";
import cookie from "@fastify/cookie";
import staticPlugin from "@fastify/static";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { execFileSync } from "node:child_process";
import {
  existsSync,
  writeFileSync,
  unlinkSync,
  openSync,
  closeSync,
  readFileSync,
} from "node:fs";
import {
  Service,
  VERSION,
  dataDir,
  AppError,
  type Actor,
  type Harness,
} from "../../core/src/index.js";
import { CodexHarness } from "../../harness-codex/src/index.js";
import { ClaudeHarness } from "../../harness-claude/src/index.js";
import { Doorbells } from "./doorbells.js";
import { recoverOrphans } from "./recovery.js";
export async function createDaemon(
  options: {
    dir?: string;
    port?: number;
    harnesses?: Record<string, Harness>;
    cliPath?: string;
    skillDir?: string;
    root?: string;
  } = {},
) {
  const root =
    options.root ||
    resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const dir = options.dir || dataDir();
  const service = new Service(
    dir,
    options.harnesses || {
      codex: new CodexHarness(),
      claude: new ClaudeHarness(),
    },
    options.cliPath || join(root, "bin/hamlet.js"),
    options.skillDir || join(root, "skills/hamlet"),
  );
  const app = Fastify({ logger: false, bodyLimit: 2 * 1024 * 1024 });
  await app.register(cookie);
  let closing = false;
  const streams = new Set<any>();
  app.setErrorHandler((e: any, _req, reply) => {
    reply
      .code(e.statusCode || 500)
      .send({ error: e.message, details: e.details });
  });
  app.addHook("onRequest", async (req, reply) => {
    const host = req.headers.host || "";
    if (
      !/^127\.0\.0\.1(?::\d+)?$/.test(host) &&
      !/^localhost(?::\d+)?$/.test(host)
    )
      throw new AppError("无效 Host", 403);
    const origin = req.headers.origin;
    if (origin && origin !== `http://${host}`)
      throw new AppError("禁止跨源请求", 403);
    if (req.url === "/health") return;
    const query = req.query as any;
    if (query.token && req.method === "GET" && !req.url.startsWith("/api/")) {
      const a = service.auth.verify(query.token);
      if (a.kind !== "owner") throw new AppError("无权限", 403);
      reply.setCookie("hamlet", query.token, {
        httpOnly: true,
        sameSite: "strict",
        path: "/",
      });
      return reply.redirect("/");
    }
    const bearer = req.headers.authorization?.replace(/^Bearer /, "");
    const token = bearer || req.cookies.hamlet || req.cookies.detached;
    if (!token) throw new AppError("需要身份令牌，请通过 hamlet ui 打开", 401);
    const actor = service.auth.verify(token);
    service.context(actor);
    (req as any).actor = actor;
  });
  app.get("/health", async () => ({
    ok: true,
    name: "Hamlet",
    pid: process.pid,
    version: VERSION,
  }));
  app.post("/api/ring", async (req) =>
    service.ring((req as any).actor, req.body as any, "ring"),
  );
  app.post<{ Params: { name: string } }>("/api/tools/:name", async (req) => {
    if (!req.body || typeof req.body !== "object" || Array.isArray(req.body))
      throw new AppError("参数必须是对象");
    if (
      req.params.name === "message_to" &&
      (req as any).actor.kind === "owner" &&
      !req.headers.authorization
    )
      return service.ring((req as any).actor, req.body as any, "ui");
    return service.call(req.params.name, req.body as any, (req as any).actor);
  });
  app.get("/api/identity", async (req) =>
    service.call("whoami", {}, (req as any).actor),
  );
  app.get("/api/events", async (req, reply) => {
    const actor = (req as any).actor as Actor;
    if (actor.kind === "doorbell") throw new AppError("无权限", 403);
    reply.hijack();
    const raw = reply.raw;
    raw.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    raw.write("event: ready\ndata: {}\n\n");
    streams.add(raw);
    const change = () => raw.write("event: change\ndata: {}\n\n");
    const event = (e: any) => {
      try {
        const r = service.store.get("runs", e.run_id);
        service.instance(r!.instance_id, actor);
        raw.write(`event: agent\ndata: ${JSON.stringify(e)}\n\n`);
      } catch {}
    };
    service.on("change", change);
    service.on("event", event);
    const timer = setInterval(() => raw.write(": heartbeat\n\n"), 15000);
    req.raw.on("close", () => {
      clearInterval(timer);
      service.off("change", change);
      service.off("event", event);
      streams.delete(raw);
    });
  });
  app.post("/api/shutdown", async (req) => {
    service.owner((req as any).actor);
    setTimeout(() => void close(), 10);
    return { ok: true };
  });
  const ui = join(root, "packages/ui/dist");
  if (existsSync(ui)) await app.register(staticPlugin, { root: ui });
  else
    app.get("/", async () => ({ message: "请先运行 pnpm build 构建 Web UI" }));
  const bells = new Doorbells(service, join(root, "packages/py-doorbell/bin"));
  async function close() {
    if (closing) return;
    closing = true;
    await bells.close();
    await service.close();
    for (const s of streams) s.end();
    await app.close();
    if (existsSync(join(dir, "daemon.json")))
      unlinkSync(join(dir, "daemon.json"));
  }
  return {
    app,
    service,
    bells,
    close,
    async listen() {
      await app.listen({ host: "127.0.0.1", port: options.port ?? 0 });
      const address = app.server.address() as any;
      service.url = `http://127.0.0.1:${address.port}`;
      writeFileSync(
        join(dir, "daemon.json"),
        JSON.stringify({
          pid: process.pid,
          port: address.port,
          url: service.url,
        }),
        { mode: 0o600 },
      );
      await recoverOrphans(service);
      service.start();
      await bells.restore();
      return service.url;
    },
  };
}
export async function runDaemon(options: { port?: number } = {}) {
  const dir = dataDir();
  const { mkdirSync } = await import("node:fs");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const lock = join(dir, "daemon.lock");
  try {
    const fd = openSync(lock, "wx", 0o600);
    writeFileSync(fd, String(process.pid));
    closeSync(fd);
  } catch {
    const pid = Number(readFileSync(lock, "utf8"));
    try {
      process.kill(pid, 0);
      if (pid > 1) {
        const zombie =
          process.platform === "linux"
            ? readFileSync(`/proc/${pid}/stat`, "utf8")
                .split(") ")[1]
                ?.startsWith("Z")
            : execFileSync("ps", ["-p", String(pid), "-o", "stat="], {
                encoding: "utf8",
              })
                .trim()
                .startsWith("Z");
        if (zombie)
          throw Object.assign(new Error("stale daemon"), { code: "ESRCH" });
      }
      throw new Error("daemon 已在运行或启动中");
    } catch (e: any) {
      if (e.code !== "ESRCH" && e.code !== "ENOENT") throw e;
      unlinkSync(lock);
      const fd = openSync(lock, "wx", 0o600);
      writeFileSync(fd, String(process.pid));
      closeSync(fd);
    }
  }
  const cleanup = () => {
    try {
      if (Number(readFileSync(lock, "utf8")) === process.pid) unlinkSync(lock);
    } catch {}
  };
  process.on("exit", cleanup);
  try {
    const d = await createDaemon({ dir, port: options.port });
    await d.listen();
    for (const signal of ["SIGTERM", "SIGINT"] as const)
      process.once(
        signal,
        () =>
          void d.close().then(() => {
            cleanup();
            process.exit(0);
          }),
      );
  } catch (e) {
    cleanup();
    throw e;
  }
}
if (process.argv.includes("--serve"))
  runDaemon().catch((e) => {
    console.error(e);
    process.exitCode = 1;
  });
