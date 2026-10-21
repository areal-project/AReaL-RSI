import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
  chmodSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import { Client } from "../packages/core/src/client.js";
import {
  CodexHarness,
  codexArgs,
  normalize,
} from "../packages/harness-codex/src/index.js";
import {
  claudeArgs,
  normalizeClaude,
} from "../packages/harness-claude/src/index.js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { childEnvironment } from "../packages/core/src/runtime.js";
import { OWNER } from "../packages/core/src/index.js";
import { until, Controlled } from "./helpers.js";
const exec = promisify(execFile);
const fixture = resolve("tests/fixtures/fake-codex.mjs");

test("HTTP 认证、cookie、Origin / Host、防跨组 SSE；worker 无管理权限", async () => {
  const dir = mkdtempSync(join(tmpdir(), "detached-http-")),
    h = new Controlled();
  const d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    const url = await d.listen(),
      token = d.service.auth.ownerToken,
      c = new Client(url, token);
    assert.equal((await fetch(url + "/api/identity")).status, 401);
    assert.equal(
      (
        await fetch(url + "/api/tools/group_list", {
          method: "POST",
          headers: {
            Authorization: "Bearer " + token,
            Origin: "http://evil.test",
            "Content-Type": "application/json",
          },
          body: "{}",
        })
      ).status,
      403,
    );
    const r = await fetch(url + "/?token=" + token, { redirect: "manual" });
    assert.equal(r.status, 302);
    assert.match(r.headers.get("set-cookie")!, /HttpOnly/);
    assert.equal(
      (
        await fetch(url + "/", {
          headers: { Cookie: r.headers.get("set-cookie")!.split(";")[0] },
        })
      ).status,
      200,
    );
    assert.equal(
      (
        await d.app.inject({
          method: "GET",
          url: "/health",
          headers: { host: "evil.test" },
        })
      ).statusCode,
      403,
    );
    const g = await c.call("group_create", { name: "g" }),
      l = await c.call("agent_create", {
        group: g.id,
        role_name: "worker",
        workdir: join(dir, "w"),
        worktree: false,
      });
    await c.request("/api/ring", { target: l.id, prompt: "x" });
    await until(() => h.calls.length === 1);
    const wa = { kind: "instance" as const, id: l.id + "-1" },
      wc = new Client(url, d.service.auth.sign(wa));
    await assert.rejects(wc.call("group_create", { name: "bad" }), /无权限/);
    assert.equal((await wc.call("whoami")).id, wa.id);
    await assert.rejects(
      wc.call("agent_create", { role_name: "bad", workdir: join(dir, "bad") }),
      /无权限/,
    );
    assert.equal((await wc.call("group_agents"))[0].id, l.id);
    const abort = new AbortController();
    const stream = await fetch(url + "/api/events", {
      headers: { Authorization: "Bearer " + token },
      signal: abort.signal,
    });
    assert.equal(stream.headers.get("content-type"), "text/event-stream");
    const reader = stream.body!.getReader();
    assert.match(
      new TextDecoder().decode((await reader.read()).value),
      /event: ready/,
    );
    await c.call("group_update", { id: g.id, paused: true });
    assert.match(
      new TextDecoder().decode((await reader.read()).value),
      /event: change/,
    );
    abort.abort();
    await reader.cancel().catch(() => {});
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("真实子进程适配：stdin 安全传参、session/resume、事件和原始日志、错误、终止", async () => {
  const dir = mkdtempSync(join(tmpdir(), "detached-process-"));
  const d = await createDaemon({
    dir,
    harnesses: { codex: new CodexHarness(fixture) },
  });
  try {
    await d.listen();
    const s = d.service,
      g = await s.call("group_create", { name: "test" }, OWNER),
      l = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "执行者",
          run_mode: "resident",
          workdir: join(dir, "work"),
          extra_env: {
            FAKE_INVOCATIONS: join(dir, "invocations"),
            FAKE_EDIT: "1",
          },
          system_prompt_tpl: "系统 {{role}}",
        },
        OWNER,
      );
    const text = '换行\n"引号" $(touch /tmp/should-never-exist-detached)';
    const m = s.ring(OWNER, { target: l.id, prompt: text });
    await until(() => s.store.get("messages", m.id)?.status === "done");
    const i = s.store.all("instances")[0];
    assert.match(i.harness_session_id, /session-/);
    assert.equal(s.store.all("runs")[0].final_output, "STARTED");
    assert.equal(s.store.all("runs")[0].usage.input_tokens, 12);
    const run = s.store.all("runs")[0];
    assert.match(readFileSync(run.events_path, "utf8"), /command/);
    assert.match(
      readFileSync(run.events_path.replace(".jsonl", ".raw.jsonl"), "utf8"),
      /command_execution/,
    );
    assert.equal(existsSync("/tmp/should-never-exist-detached"), false);
    const invoke = JSON.parse(
      readFileSync(join(dir, "invocations"), "utf8").split("\n")[0],
    );
    assert.match(invoke.prompt, /换行/);
    assert.equal(invoke.token, true);
    assert.ok(!invoke.args.some((x: string) => x.includes("mcp_servers")));
    const resume = s.ring(OWNER, { target: i.id, prompt: "followup" });
    await until(() => s.store.get("messages", resume.id)?.status === "done");
    assert.equal(s.store.all("runs")[1].kind, "resume");
    assert.equal(s.store.all("runs")[1].final_output, "RESUMED");
    const fail = s.ring(OWNER, { target: i.id, prompt: "NO_SESSION" });
    await until(() => s.store.get("messages", fail.id)?.status === "failed");
    assert.match(
      s.store.get("messages", fail.id)?.error,
      /session unavailable/,
    );
    const waiting = s.ring(OWNER, { target: i.id, prompt: "WAIT" });
    await until(
      () => s.store.get("messages", waiting.id)?.status === "dispatched",
    );
    await until(() => !!s.active.get(i.id)?.handle);
    await s.call("run_stop", { instance_id: i.id }, OWNER);
    assert.equal(s.store.all("runs").at(-1)?.status, "stopped");
    assert.equal(s.store.get("instances", i.id)?.status, "idle");
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("CLI/HTTP：Owner 查询排队与运行，Worker 保持实例身份和权限", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-cli-transport-")),
    h = new Controlled();
  const d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    const url = await d.listen();
    const env = {
      ...childEnvironment(),
      HAMLET_HOME: dir,
      HAMLET_URL: url,
      HAMLET_TOKEN: d.service.auth.ownerToken,
    };
    const query = async (name: string, params: any = {}, identityEnv = env) =>
      JSON.parse(
        (
          await exec(
            process.execPath,
            [resolve("bin/hamlet.js"), "call", name, JSON.stringify(params)],
            { env: identityEnv },
          )
        ).stdout,
      );
    const g = await query("group_create", { name: "from CLI" });
    const l = await query("agent_create", {
      group: g.id,
      role_name: "worker",
      workdir: join(dir, "w"),
      worktree: false,
    });
    await query("message_to", { target: l.id, prompt: "go" });
    await until(() => h.calls.length === 1);
    const waiting = await query("message_to", { target: l.id, prompt: "next" });
    assert.equal(waiting.queue_reason.code, "workdir_busy");
    assert.equal(waiting.sender.label, "宿主调用方");
    const queued = await query("message_list", {
      group: g.id,
      status: "queued",
      limit: 1,
    });
    assert.equal(queued[0].id, waiting.id);
    const inspected = await query("message_get", { id: waiting.id });
    assert.equal(inspected.queue_reason.blocking_instance_id, l.id + "-1");
    const runs = await query("run_list", {
      group: g.id,
      status: "running",
      limit: 1,
    });
    assert.equal(runs[0].trigger.sender.identity, "owner");
    assert.equal((await query("run_get", { id: runs[0].id })).id, runs[0].id);
    assert.ok(
      (await query("run_events", { id: runs[0].id, limit: 1 })).length <= 1,
    );
    const workerEnv = {
      ...env,
      HAMLET_TOKEN: d.service.auth.sign({ kind: "instance", id: l.id + "-1" }),
    };
    assert.equal((await query("whoami", {}, workerEnv)).id, l.id + "-1");
    assert.equal((await query("group_agents", {}, workerEnv))[0].id, l.id);
    await assert.rejects(
      query("agent_delete", { id: l.id }, workerEnv),
      /无权限/,
    );
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("自定义 doorbell 注入 Python、状态持久化、自动重启/禁用/启动恢复", async () => {
  const dir = mkdtempSync(join(tmpdir(), "detached-bell-")),
    h = new Controlled();
  const d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    await d.listen();
    const s = d.service,
      g = await s.call("group_create", { name: "bell" }, OWNER),
      l = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "监听",
          workdir: join(dir, "w"),
          worktree: false,
        },
        OWNER,
      );
    const script =
      '#!/usr/bin/env doorbell-python\nfrom doorbell import ring,state\nimport time\nstate.set("launches",state.get("launches",0)+1)\nprint("ready",flush=True)\nring(prompt="from python",dedup_key="boot")\nwhile True: time.sleep(1)\n';
    await s.call("doorbell_script_save", { logical_id: l.id, script }, OWNER);
    await until(() => s.store.all("messages").length === 1);
    assert.equal(s.store.all("messages")[0].source, "doorbell_script");
    assert.equal(s.store.get("doorbells", l.id)?.status, "running");
    assert.ok(s.store.get("doorbells", l.id)?.last_ring_at);
    await s.call(
      "doorbell_control",
      { logical_id: l.id, action: "restart" },
      OWNER,
    );
    await until(
      () =>
        JSON.parse(
          readFileSync(
            join(dir, "doorbells", l.id, "state/state.json"),
            "utf8",
          ),
        ).launches === 2,
    );
    assert.equal(s.store.all("messages").length, 1);
    assert.match(
      (await s.call("doorbell_logs", { logical_id: l.id }, OWNER)).log,
      /ready/,
    );
    await s.call(
      "doorbell_control",
      { logical_id: l.id, action: "stop" },
      OWNER,
    );
    assert.equal(s.store.get("doorbells", l.id)?.status, "stopped");
    assert.equal(s.store.get("logical_agents", l.id)?.doorbell_enabled, 0);
    await s.call(
      "doorbell_script_save",
      { logical_id: l.id, script: "#!/bin/sh\nexit 1\n" },
      OWNER,
    );
    await until(() => s.store.get("doorbells", l.id)?.status === "crashed");
    assert.equal(s.store.get("doorbells", l.id)?.restart_count, 1);
    await until(() => s.store.get("doorbells", l.id)?.restart_count >= 2);
    assert.ok(d.bells.timers.has(l.id));
    await s.call(
      "doorbell_control",
      { logical_id: l.id, action: "stop" },
      OWNER,
    );
    assert.equal(d.bells.timers.has(l.id), false);
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("harness 参数与标准事件契约，包括第二个 Claude harness", () => {
  const o = {
    cwd: "/tmp",
    systemPrompt: "system",
    prompt: "prompt",
    model: "model",
    options: { model_reasoning_effort: "low" },
    env: {},
  };
  const fork = codexArgs(o),
    resume = codexArgs({ ...o, sessionId: "session" });
  assert.ok(fork.includes("--dangerously-bypass-approvals-and-sandbox"));
  assert.ok(fork.includes("--skip-git-repo-check"));
  assert.ok(fork.some((a) => a.startsWith("developer_instructions=")));
  assert.ok(!resume.some((a) => a.startsWith("developer_instructions=")));
  assert.equal(resume[2], "session");
  assert.deepEqual(normalize({ type: "thread.started", thread_id: "id" }), [
    { type: "session_started", session_id: "id" },
  ]);
  assert.equal(
    normalize({
      type: "item.completed",
      item: { type: "file_change", changes: [] },
    })[0].type,
    "file_change",
  );
  assert.equal(
    normalizeClaude({
      type: "user",
      message: { content: [{ type: "tool_result", content: "ok" }] },
    })[0].type,
    "tool_result",
  );
  assert.equal(
    normalizeClaude({ type: "result", is_error: true, result: "bad" }).at(-1)
      ?.type,
    "error",
  );
  assert.ok(claudeArgs({ ...o, sessionId: "sid" }).includes("--resume"));
  assert.ok(claudeArgs(o).includes("--append-system-prompt"));
});

test("第二个 harness 经同一个调度器 fork/resume，保留 Claude 会话与事件", async () => {
  const { ClaudeHarness } =
    await import("../packages/harness-claude/src/index.js");
  const dir = mkdtempSync(join(tmpdir(), "detached-claude-"));
  const d = await createDaemon({
    dir,
    harnesses: {
      claude: new ClaudeHarness(resolve("tests/fixtures/fake-claude.mjs")),
    },
  });
  try {
    await d.listen();
    const s = d.service,
      g = await s.call("group_create", { name: "Claude" }, OWNER),
      l = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "Claude",
          harness: "claude",
          run_mode: "resident",
          workdir: join(dir, "work"),
          worktree: false,
        },
        OWNER,
      );
    const m = s.ring(OWNER, { target: l.id, prompt: "first" });
    await until(() => s.store.get("messages", m.id)?.status === "done");
    const i = s.store.all("instances")[0];
    assert.match(i.harness_session_id, /claude-session-/);
    assert.equal(s.store.all("runs")[0].final_output, "CLAUDE_STARTED");
    const follow = s.ring(OWNER, { target: l.id, prompt: "followup" });
    await until(() => s.store.get("messages", follow.id)?.status === "done");
    assert.equal(s.store.all("runs")[1].final_output, "CLAUDE_RESUMED");
    assert.equal(
      s.store.get("instances", i.id)?.harness_session_id,
      i.harness_session_id,
    );
    assert.match(
      readFileSync(s.store.all("runs")[0].events_path, "utf8"),
      /tool_result/,
    );
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("doorbell daemon 重启恢复 enabled 脚本；窗口内第六次崩溃停止重试", async () => {
  const dir = mkdtempSync(join(tmpdir(), "detached-restore-")),
    h = new Controlled();
  let d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    await d.listen();
    let s = d.service;
    const g = await s.call("group_create", { name: "restore" }, OWNER),
      l = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "monitor",
          workdir: join(dir, "work"),
          worktree: false,
        },
        OWNER,
      );
    await s.call(
      "doorbell_script_save",
      {
        logical_id: l.id,
        script:
          '#!/usr/bin/env doorbell-python\nfrom doorbell import state\nimport time\nstate.set("launches",state.get("launches",0)+1)\nwhile True: time.sleep(1)\n',
      },
      OWNER,
    );
    const state = join(dir, "doorbells", l.id, "state/state.json");
    await until(() => existsSync(state));
    await d.close();
    d = await createDaemon({ dir, harnesses: { codex: h } });
    await d.listen();
    s = d.service;
    await until(() => JSON.parse(readFileSync(state, "utf8")).launches === 2);
    await s.call(
      "doorbell_script_save",
      { logical_id: l.id, script: "#!/bin/sh\nsleep 0.1\nexit 1\n" },
      OWNER,
    );
    d.bells.crashes.set(l.id, Array(5).fill(Date.now()));
    await until(() => s.store.get("doorbells", l.id)?.status === "failed");
    assert.equal(d.bells.timers.has(l.id), false);
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
