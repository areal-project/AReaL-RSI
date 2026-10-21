import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { symlinkSync, mkdirSync, readFileSync, appendFileSync } from "node:fs";
import { OWNER } from "../packages/core/src/index.js";
import { fixture, until } from "./helpers.js";

test("direct 并发约束在创建/查询/入队中可见，原因与调度一致且查询不触发运行", async () => {
  const f = await fixture();
  try {
    const root = join(f.dir, "shared");
    mkdirSync(root);
    symlinkSync(root, join(f.dir, "alias"));
    const a = await f.agent({ workdir: root, worktree: false }),
      b = await f.agent({
        role_name: "审查",
        workdir: join(f.dir, "alias"),
        worktree: false,
      });
    assert.equal(b.execution.concurrency, "serial_workdir");
    assert.equal(b.execution.workdir, root);
    assert.equal(b.execution.shared_with[0].logical_id, a.id);
    assert.match(b.execution.description, /不要.*等待/);
    const m1 = await f.s.call(
        "message_to",
        { target: a.id, prompt: "first" },
        OWNER,
      ),
      m2 = await f.s.call(
        "message_to",
        { target: b.id, prompt: "second" },
        OWNER,
      );
    assert.equal(m1.status, "queued");
    assert.equal(m1.next_action, "fork");
    assert.equal(m2.queue_reason.code, "fifo");
    assert.equal(m2.queue_reason.ahead_message_id, m1.id);
    assert.equal(f.h.calls.length, 0);
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const blocked = await f.s.call("message_get", { id: m2.id }, OWNER);
    assert.equal(blocked.status, "queued");
    assert.equal(blocked.queue_reason.code, "workdir_busy");
    assert.equal(blocked.queue_reason.workdir, root);
    assert.equal(blocked.queue_reason.blocking_instance_id, a.id + "-1");
    assert.equal(
      blocked.queue_reason.blocking_run_id,
      f.s.store.all("runs")[0].id,
    );
    assert.equal(blocked.runs.length, 0);
    const repeated = await f.s.call("agent_get", { id: a.id }, OWNER);
    assert.equal(repeated.execution.shared_with[0].logical_id, b.id);
    assert.equal(f.h.calls.length, 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    assert.equal(
      (await f.s.call("message_get", { id: m2.id }, OWNER)).queue_reason.code,
      "awaiting_dispatch",
    );
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    f.h.calls[1].finish();
    await until(() => f.s.active.size === 0);
    const completed = await f.s.call("message_get", { id: m2.id }, OWNER);
    assert.equal(completed.queue_reason, null);
    assert.equal(completed.runs[0].kind, "fork");
    assert.ok(completed.runs[0].queue_wait_ms >= 0);
  } finally {
    await f.close();
  }
});

test("实例 FIFO 与 Group 暂停原因可区分；worktree fork 不会错误标为串行", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ run_mode: "resident" });
    assert.equal(l.execution.concurrency, "parallel_instances");
    const first = f.s.ring(OWNER, { target: l.id, prompt: "a" }),
      second = f.s.ring(OWNER, { target: l.id, prompt: "b" });
    assert.equal(second.queue_reason.code, "fifo");
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    let view = await f.s.call("message_get", { id: second.id }, OWNER);
    assert.equal(view.queue_reason.code, "instance_busy");
    assert.equal(view.next_action, "resume");
    assert.equal(view.planned_instance_id, l.id + "-1");
    await f.s.call("group_update", { id: f.g.id, paused: true }, OWNER);
    view = await f.s.call("message_get", { id: second.id }, OWNER);
    assert.equal(view.queue_reason.code, "group_paused");
    const detail = await f.s.call("instance_get", { id: l.id + "-1" }, OWNER);
    assert.equal(detail.pending_messages[0].id, second.id);
    const parallel = await f.agent({
      role_name: "并行",
      workdir: join(f.dir, "parallel"),
    });
    const p1 = f.s.ring(OWNER, { target: parallel.id, prompt: "a" }),
      p2 = f.s.ring(OWNER, { target: parallel.id, prompt: "b" });
    await f.s.call("group_update", { id: f.g.id, paused: false }, OWNER);
    assert.equal(
      (await f.s.call("message_get", { id: p2.id }, OWNER)).queue_reason.code,
      "awaiting_dispatch",
    );
  } finally {
    await f.close();
  }
});

test("Owner 查询支持来源/组/目标/时间过滤及稳定分页；历史与重试、删除相互独立", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ run_mode: "resident", worktree: false });
    const m = await f.s.call(
      "message_to",
      { target: l.id, prompt: "original" },
      OWNER,
    );
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish({ exitCode: 1, error: "failure" });
    await until(() => f.s.active.size === 0);
    const first = f.s.store.all("runs")[0];
    const originalTrigger = first.trigger_snapshot;
    await f.s.call("message_retry", { id: m.id }, OWNER);
    assert.notEqual(f.s.store.get("messages", m.id)?.queued_at, m.queued_at);
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    f.h.calls[1].finish({ finalOutput: "result" });
    await until(() => f.s.active.size === 0);
    const attempts = await f.s.call("message_get", { id: m.id }, OWNER);
    assert.equal(attempts.runs.length, 2);
    assert.equal(attempts.runs[0].status, "failed");
    assert.equal(attempts.runs[1].status, "succeeded");
    assert.deepEqual(attempts.runs[0].trigger_snapshot, originalTrigger);
    assert.equal(attempts.runs[0].trigger.run_id, undefined);
    assert.equal(attempts.runs[0].trigger_is_snapshot, true);
    const runs = await f.s.call(
      "run_list",
      {
        group: f.g.id,
        logical_id: l.id,
        source: "host",
        from_id: "owner",
        message_id: m.id,
        limit: 1,
        order: "desc",
      },
      OWNER,
    );
    assert.equal(runs.length, 1);
    assert.equal(runs[0].status, "succeeded");
    assert.equal(runs[0].final_output, "result");
    assert.ok(runs[0].duration_ms >= 0);
    const older = await f.s.call(
      "run_list",
      { instance_id: l.id + "-1", limit: 1, offset: 1, order: "desc" },
      OWNER,
    );
    assert.equal(older[0].id, first.id);
    assert.deepEqual(
      await f.s.call("run_list", { since: "2100-01-01T00:00:00Z" }, OWNER),
      [],
    );
    assert.equal(
      (
        await f.s.call(
          "message_list",
          {
            group: f.g.id,
            status: "done",
            logical_id: l.id,
            target: l.id,
            from_id: "owner",
            source: "host",
            since: "2000-01-01T00:00:00Z",
            limit: 1,
          },
          OWNER,
        )
      )[0].id,
      m.id,
    );
    await assert.rejects(f.s.call("run_list", { limit: 501 }, OWNER), /limit/);
    await assert.rejects(
      f.s.call("message_list", { offset: -1 }, OWNER),
      /offset/,
    );
    await assert.rejects(
      f.s.call("run_list", { since: "nonsense" }, OWNER),
      /since/,
    );
    const events = await f.s.call("run_events", { id: first.id }, OWNER),
      eventPage = await f.s.call(
        "run_events",
        { id: first.id, limit: 1, offset: 1 },
        OWNER,
      );
    assert.deepEqual(eventPage, [events[1]]);
    await f.s.call("agent_delete", { id: l.id }, OWNER);
    assert.equal(
      (await f.s.call("run_get", { id: first.id }, OWNER)).status,
      "failed",
    );
    assert.equal(
      (await f.s.call("instance_get", { id: l.id + "-1" }, OWNER)).runs.length,
      2,
    );
    assert.equal(
      (await f.s.call("message_list", { group: f.g.id }, OWNER)).length,
      1,
    );
    assert.equal(
      (await f.s.call("run_events", { id: first.id }, OWNER)).length,
      events.length,
    );
    const g2 = await f.s.call("group_create", { name: "other" }, OWNER);
    assert.deepEqual(
      await f.s.call("message_list", { group: g2.id }, OWNER),
      [],
    );
  } finally {
    await f.close();
  }
});

test("来源区分权限与入口，不把 Owner 当真人；doorbell 与实例身份保持准确", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ worktree: false });
    const host = await f.s.call(
        "message_to",
        { target: l.id, prompt: "host" },
        OWNER,
      ),
      ring = f.s.ring(OWNER, { target: l.id, prompt: "cli" }),
      ui = f.s.ring(OWNER, { target: l.id, prompt: "ui" }, "ui"),
      bell = f.s.ring(
        { kind: "doorbell", id: l.id },
        { target: l.id, prompt: "script" },
      );
    assert.equal(host.sender.label, "宿主调用方");
    assert.equal(host.sender.identity, "owner");
    assert.equal(ring.sender.label, "ring 调用方");
    assert.equal(ui.sender.label, "Web UI");
    assert.equal(bell.sender.kind, "doorbell");
    assert.equal(bell.source, "doorbell_script");
    assert.equal(bell.from_id, l.id);
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    assert.match(f.h.calls[0].options.prompt, /^\[来自 宿主调用方 \(owner\)\]/);
    assert.doesNotMatch(f.h.calls[0].options.prompt, /来自 用户/);
    const actor = { kind: "instance" as const, id: l.id + "-1" },
      sent = await f.s.call(
        "message_to",
        {
          target: l.id,
          prompt: "reply",
          from_id: "owner",
          from_role: "用户",
          source: "ui",
        },
        actor,
      );
    assert.equal(sent.from_id, actor.id);
    assert.equal(sent.source, "message_to");
    assert.equal(sent.sender.kind, "instance");
    // Older data is annotated, not rewritten to claim a source that wasn't recorded.
    f.s.store.update("messages", host.id, { from_role: "用户" });
    const legacy = await f.s.call("message_get", { id: host.id }, OWNER);
    assert.equal(legacy.from_role, "用户");
    assert.equal(legacy.sender.label, "宿主调用方");
    const run = f.s.store.all("runs")[0];
    f.s.store.update("runs", run.id, {
      trigger_snapshot: null,
      prompt: "[来自 用户]\nlegacy",
    });
    const old = await f.s.call("run_get", { id: run.id }, OWNER);
    assert.equal(old.trigger_is_snapshot, false);
    assert.equal(old.prompt, "[来自 用户]\nlegacy");
    assert.equal(old.trigger.sender.label, "宿主调用方");
    for (const tool of ["message_get", "run_list", "run_get"])
      await assert.rejects(f.s.call(tool, { id: run.id }, actor), /无权限/);
  } finally {
    await f.close();
  }
});

test("跨组 direct 阻塞不向 Worker 泄露其他组消息、实例、run ID", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ run_mode: "resident", worktree: false });
    const g2 = await f.s.call("group_create", { name: "隔离" }, OWNER),
      b = await f.agent({ group: g2.id, role_name: "B", worktree: false });
    f.s.ring(OWNER, { target: a.id, prompt: "a" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    const actor = { kind: "instance" as const, id: a.id + "-1" },
      other = f.s.ring(OWNER, { target: b.id, prompt: "b" }),
      mine = f.s.ring(OWNER, { target: a.id, prompt: "a" });
    let messages = await f.s.call("message_list", {}, actor);
    const reason = messages.find((m: any) => m.id === mine.id).queue_reason;
    assert.equal(reason.code, "fifo");
    assert.equal(reason.ahead_message_id, undefined);
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    messages = await f.s.call("message_list", {}, actor);
    const locked = messages.find((m: any) => m.id === mine.id).queue_reason;
    assert.equal(locked.code, "workdir_busy");
    assert.equal(locked.blocking_instance_id, undefined);
    assert.equal(locked.blocking_run_id, undefined);
    assert.equal(
      messages.some((m: any) => m.id === other.id),
      false,
    );
  } finally {
    await f.close();
  }
});

test("旧库增量迁移保留原消息/运行；损坏的日志尾行可审计而不会使查询失败", async () => {
  const { Store, Service } = await import("../packages/core/src/index.js");
  const { Controlled } = await import("./helpers.js");
  const { default: Database } = await import("better-sqlite3");
  const f = await fixture();
  let reopened: InstanceType<typeof Service> | undefined;
  try {
    const l = await f.agent({ worktree: false });
    const m = f.s.ring(OWNER, { target: l.id, prompt: "legacy" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    const run = f.s.store.all("runs")[0];
    appendFileSync(run.events_path, '{"type":"incomplete');
    await f.s.close();
    const old = new Database(join(f.dir, "data/state.db"));
    old.exec(
      "ALTER TABLE messages DROP COLUMN queued_at; ALTER TABLE runs DROP COLUMN trigger_snapshot; ALTER TABLE runs DROP COLUMN queue_wait_ms;",
    );
    old.close();
    reopened = new Service(
      join(f.dir, "data"),
      { codex: new Controlled() },
      "/inner",
    );
    const got = await reopened.call("message_get", { id: m.id }, OWNER);
    assert.equal(got.prompt, "legacy");
    assert.equal(got.runs[0].status, "succeeded");
    assert.equal(got.runs[0].trigger_is_snapshot, false);
    assert.equal(got.runs[0].queue_wait_ms, null);
    assert.equal(got.runs[0].trigger.sender.label, "ring 调用方");
    const events = await reopened.call("run_events", { id: run.id }, OWNER);
    assert.equal(events.at(-1).type, "warning");
    assert.match(events.at(-1).message, /无法解析/);
    reopened.store.close();
    reopened = undefined;
  } finally {
    await reopened?.close();
    const { rmSync } = await import("node:fs");
    rmSync(f.dir, { recursive: true, force: true });
  }
});

test("fork 消息重试到新实例后，仍可按旧实例查询关联消息", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ worktree: false });
    const m = await f.s.call(
      "message_to",
      { target: l.id, prompt: "fork" },
      OWNER,
    );
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish({ exitCode: 1, error: "retry" });
    await until(() => f.s.active.size === 0);
    await f.s.call("message_retry", { id: m.id }, OWNER);
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    f.h.calls[1].finish();
    await until(() => f.s.active.size === 0);
    const historical = await f.s.call(
      "message_list",
      { instance_id: l.id + "-1" },
      OWNER,
    );
    assert.equal(historical[0].id, m.id);
    assert.equal(historical[0].resolved_instance_id, l.id + "-2");
    assert.equal(
      (await f.s.call("message_get", { id: m.id }, OWNER)).runs.length,
      2,
    );
  } finally {
    await f.close();
  }
});
