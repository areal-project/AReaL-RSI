import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createDaemon } from "../packages/daemon/src/index.js";
import { OWNER } from "../packages/core/src/index.js";
import { codexArgs } from "../packages/harness-codex/src/index.js";
import { claudeArgs } from "../packages/harness-claude/src/index.js";
import { Controlled, until } from "./helpers.js";

const bridge = resolve("bin/hamlet-agent-tools.js");
const value = (r: any) => r.structuredContent || JSON.parse(r.content[0].text);

test("registered tools traverse stdio and HTTP with instance permissions and actionable queue errors", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-tools-")),
    h = new Controlled();
  const d = await createDaemon({ dir, harnesses: { codex: h } });
  const clients: Client[] = [];
  try {
    const url = await d.listen(),
      s = d.service;
    const g = await s.call("group_create", { name: "test" }, OWNER);
    const a = await s.call(
      "agent_create",
      {
        group: g.id,
        role_name: "sender",
        worktree: false,
        workdir: join(dir, "sender"),
      },
      OWNER,
    );
    const b = await s.call(
      "agent_create",
      {
        group: g.id,
        role_name: "target",
        run_mode: "resident",
        worktree: false,
        workdir: join(dir, "target"),
      },
      OWNER,
    );
    s.ring(OWNER, { target: a.id });
    s.ring(OWNER, { target: b.id });
    await until(() => h.calls.length === 2);
    await s.call("group_update", { id: g.id, paused: true }, OWNER);
    const sender = `${a.id}-1`,
      target = `${b.id}-1`;
    const connect = async (
      id: string,
      token = s.auth.sign({ kind: "instance", id }),
    ) => {
      const c = new Client({ name: "hamlet-test", version: "1" });
      clients.push(c);
      await c.connect(
        new StdioClientTransport({
          command: process.execPath,
          args: [bridge],
          env: { HAMLET_URL: url, HAMLET_TOKEN: token, HAMLET_INSTANCE_ID: id },
          stderr: "pipe",
        }),
      );
      return c;
    };
    const c = await connect(sender);
    assert.deepEqual(
      (await c.listTools()).tools.map((t) => t.name),
      ["message_to", "message_force_push"],
    );
    const call = (name: string, args: any) =>
      c.callTool({ name, arguments: args });
    for (let n = 0; n < 6; n++) {
      const result = await call("message_to", {
        target: n % 2 ? target : b.id,
        prompt: `task-${n}`,
      });
      assert.equal(result.isError, undefined);
      assert.equal(value(result).from_id, sender);
      assert.equal(value(result).status, "queued");
    }
    const rejected = await call("message_to", {
      target,
      prompt: "preserve me",
      dedup_key: "blocked",
    });
    assert.equal(rejected.isError, true);
    const error = value(rejected);
    assert.equal(error.statusCode, 409);
    assert.equal(error.details.queued_count, 6);
    assert.equal(
      error.details.confirmation.params.id,
      error.details.message_id,
    );
    const id = error.details.message_id;
    assert.equal(
      value(await call("message_to", { target, dedup_key: "blocked" })).details
        .message_id,
      id,
    );
    const other = await connect(target);
    assert.equal(
      value(
        await other.callTool({ name: "message_force_push", arguments: { id } }),
      ).statusCode,
      403,
    );
    const pushed = await call("message_force_push", { id });
    assert.equal(pushed.isError, undefined);
    assert.equal(value(pushed).prompt, "preserve me");
    assert.equal(value(pushed).from_id, sender);
    assert.equal(value(pushed).status, "queued");
    assert.equal(
      value(await call("message_force_push", { id })).statusCode,
      409,
    );
    assert.equal(
      value(await call("message_force_push", { id: "unknown" })).statusCode,
      404,
    );
    const count = s.store.all("messages").length;
    for (const [name, args] of [
      ["agent_delete", { id: b.id }],
      ["message_to", { target, force_fork: true }],
      ["message_to", {}],
    ])
      assert.equal((await call(name as string, args)).isError, true);
    const wrongIdentity = await connect(sender, s.auth.ownerToken);
    assert.equal(
      (
        await wrongIdentity.callTool({
          name: "message_to",
          arguments: { target },
        })
      ).isError,
      true,
    );
    const wrongInstance = await connect(
      target,
      s.auth.sign({ kind: "instance", id: sender }),
    );
    assert.equal(
      (
        await wrongInstance.callTool({
          name: "message_to",
          arguments: { target },
        })
      ).isError,
      true,
    );
    const isolated = await s.call("group_create", { name: "other" }, OWNER);
    const outsider = await s.call(
      "agent_create",
      {
        group: isolated.id,
        role_name: "outside",
        worktree: false,
        workdir: join(dir, "outside"),
      },
      OWNER,
    );
    assert.equal(
      value(await call("message_to", { target: outsider.id })).statusCode,
      404,
    );
    assert.equal(s.store.all("messages").length, count);
  } finally {
    for (const c of clients) await c.close();
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("both harnesses register the same tools on fork and resume without credentials in argv", () => {
  const o = {
    cwd: "/tmp",
    prompt: "task",
    systemPrompt: "system",
    env: {
      HAMLET_NODE: process.execPath,
      HAMLET_AGENT_TOOLS: bridge,
      HAMLET_URL: "http://localhost:1234",
      HAMLET_TOKEN: "test-secret",
      HAMLET_INSTANCE_ID: "a-1",
    },
  };
  for (const options of [o, { ...o, sessionId: "existing-session" }]) {
    const codex = codexArgs(options),
      claude = claudeArgs(options);
    assert.ok(codex.some((a) => a.startsWith("mcp_servers.hamlet=")));
    assert.ok(codex.some((a) => a.includes('"HAMLET_TOKEN"')));
    const config = JSON.parse(claude[claude.indexOf("--mcp-config") + 1]);
    assert.equal(config.mcpServers.hamlet.env.HAMLET_TOKEN, "${HAMLET_TOKEN}");
    assert.equal(config.mcpServers.hamlet.args[0], bridge);
    assert.ok(!JSON.stringify([codex, claude]).includes("test-secret"));
  }
});

// Exercise actual Codex tool discovery/calls without starting a model turn.
test(
  "installed Codex discovers and calls instance tools",
  { skip: !process.env.HAMLET_CODEX_TOOLS_TEST_BIN, timeout: 120000 },
  async () => {
    const { spawn } = await import("node:child_process");
    const { createInterface } = await import("node:readline");
    const dir = mkdtempSync(join(tmpdir(), "hamlet-real-tools-")),
      h = new Controlled();
    const d = await createDaemon({ dir, harnesses: { codex: h } });
    let child: ReturnType<typeof spawn> | undefined;
    try {
      const url = await d.listen(),
        s = d.service;
      const g = await s.call("group_create", { name: "probe" }, OWNER);
      const a = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "probe",
          worktree: false,
          workdir: join(dir, "work"),
        },
        OWNER,
      );
      s.ring(OWNER, { target: a.id });
      await until(() => h.calls.length === 1);
      await s.call("group_update", { id: g.id, paused: true }, OWNER);
      const id = `${a.id}-1`;
      const env = {
        ...process.env,
        HAMLET_URL: url,
        HAMLET_TOKEN: s.auth.sign({ kind: "instance", id }),
        HAMLET_INSTANCE_ID: id,
        HAMLET_NODE: process.execPath,
        HAMLET_AGENT_TOOLS: bridge,
      };
      const args = codexArgs({
        cwd: dir,
        prompt: "",
        systemPrompt: "",
        env: env as Record<string, string>,
      });
      const flags = args.flatMap((arg, n) =>
        arg === "-c" ? [arg, args[n + 1]] : [],
      );
      child = spawn(
        process.env.HAMLET_CODEX_TOOLS_TEST_BIN!,
        ["app-server", "--stdio", ...flags],
        { cwd: dir, env, stdio: ["pipe", "pipe", "ignore"], detached: true },
      );
      const pending = new Map<
        number,
        { resolve: (v: any) => void; reject: (e: Error) => void }
      >();
      const lines = createInterface({ input: child.stdout! });
      lines.on("line", (line) => {
        const r = JSON.parse(line),
          p = pending.get(r.id);
        if (p) {
          pending.delete(r.id);
          if (r.error) p.reject(new Error(JSON.stringify(r.error)));
          else p.resolve(r.result);
        }
      });
      const fail = () => {
        for (const p of pending.values())
          p.reject(new Error("Codex tool probe exited"));
        pending.clear();
      };
      child.on("exit", fail);
      child.on("error", fail);
      let seq = 0;
      const rpc = (method: string, params: any) =>
        new Promise<any>((resolve, reject) => {
          pending.set(++seq, { resolve, reject });
          child!.stdin!.write(
            JSON.stringify({ id: seq, method, params }) + "\n",
          );
        });
      const timer = setTimeout(() => {
        fail();
        child?.kill("SIGKILL");
      }, 90000);
      try {
        await rpc("initialize", {
          clientInfo: { name: "hamlet-tools-check", version: "1" },
          capabilities: { experimentalApi: true },
        });
        child.stdin!.write(JSON.stringify({ method: "initialized" }) + "\n");
        const start = await rpc("thread/start", { cwd: dir, ephemeral: true });
        const threadId = start.thread.id;
        const inventory = await rpc("mcpServerStatus/list", {
          threadId,
          serverName: "hamlet",
        });
        const server = inventory.data.find((x: any) => x.name === "hamlet");
        assert.ok(server);
        assert.deepEqual(Object.keys(server.tools).sort(), [
          "message_force_push",
          "message_to",
        ]);
        const result = await rpc("mcpServer/tool/call", {
          threadId,
          server: "hamlet",
          tool: "message_to",
          arguments: { target: id, prompt: "transport probe" },
        });
        assert.ok(!result.isError);
        assert.equal(value(result).from_id, id);
        assert.equal(value(result).status, "queued");
        const error = await rpc("mcpServer/tool/call", {
          threadId,
          server: "hamlet",
          tool: "message_force_push",
          arguments: { id: "unknown" },
        });
        assert.equal(error.isError, true);
        assert.equal(value(error).statusCode, 404);
      } finally {
        clearTimeout(timer);
        lines.close();
      }
    } finally {
      if (child?.pid) {
        const closed = new Promise<void>((r) =>
          child!.once("close", () => r()),
        );
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
        if (child.exitCode === null && child.signalCode === null) await closed;
      }
      await d.close();
      rmSync(dir, { recursive: true, force: true });
    }
  },
);
