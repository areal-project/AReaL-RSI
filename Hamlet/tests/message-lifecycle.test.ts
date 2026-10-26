import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { Service, OWNER } from "../packages/core/src/index.js";
import { fixture, until, Controlled } from "./helpers.js";

const errorDetails = (fn: () => unknown) => {
  try {
    fn();
  } catch (e: any) {
    assert.equal(e.statusCode, 409);
    return e.details;
  }
  assert.fail("Expected queue rejection");
};

test("dedup is scoped to sender, including rejected and cancelled messages", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false });
    for (let n = 0; n < 2; n++) {
      f.s.ring(OWNER, { target: a.id });
      f.s.tick();
      await until(() => f.h.calls.length === n + 1);
      f.h.calls[n].finish();
      await until(() => f.s.active.size === 0);
    }
    const target = `${a.id}-1`;
    const first = { kind: "instance" as const, id: target },
      second = { kind: "instance" as const, id: `${a.id}-2` };
    const p = { target, prompt: "task", dedup_key: "same" };
    const one = f.s.ring(first, p),
      two = f.s.ring(second, p),
      owner = f.s.ring(OWNER, p);
    assert.notEqual(one.id, two.id);
    assert.notEqual(one.id, owner.id);
    assert.equal(f.s.ring(first, p).id, one.id);
    assert.equal(f.s.ring(second, p).id, two.id);
    for (let n = 0; n < 3; n++) f.s.ring(OWNER, { target });
    const denied1 = errorDetails(() =>
      f.s.ring(first, { target, dedup_key: "over" }),
    );
    const denied2 = errorDetails(() =>
      f.s.ring(second, { target, dedup_key: "over" }),
    );
    assert.notEqual(denied1.message_id, denied2.message_id);
    await f.s.call("message_cancel", { id: denied1.message_id }, OWNER);
    const repeated = f.s.ring(first, { target, dedup_key: "over" });
    assert.equal(repeated.id, denied1.message_id);
    assert.equal(repeated.status, "cancelled");
  } finally {
    await f.close();
  }
});

test("cancel and retry cannot bypass rejection; retry and force append at the tail", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    const cancelled = f.s.ring(OWNER, { target: a.id });
    await f.s.call("message_cancel", { id: cancelled.id }, OWNER);
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: a.id });
    await assert.rejects(
      f.s.call("message_retry", { id: cancelled.id }, OWNER),
      (e: any) => e.statusCode === 409 && e.details.message_id === cancelled.id,
    );
    assert.equal(f.s.store.get("messages", cancelled.id)!.status, "rejected");
    await f.s.call("message_cancel", { id: cancelled.id }, OWNER);
    await assert.rejects(
      f.s.call("message_force_push", { id: cancelled.id }, OWNER),
      (e: any) => e.statusCode === 409,
    );
    const head = f.s.store.all("messages").find((m) => m.status === "queued")!;
    await f.s.call("message_cancel", { id: head.id }, OWNER);
    await f.s.call("message_retry", { id: cancelled.id }, OWNER);
    assert.equal(f.s.store.get("messages", cancelled.id)!.status, "queued");
    assert.ok(
      f.s.store.get("messages", cancelled.id)!.queue_seq >
        Math.max(
          ...f.s.store
            .all("messages")
            .filter((m) => m.id !== cancelled.id)
            .map((m) => m.queue_seq || 0),
        ),
    );
  } finally {
    await f.close();
  }
});

test("resident pending messages exclude explicit forks and stop reporting after mode changes", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const id = `${a.id}-1`;
    const logical = f.s.ring(OWNER, { target: a.id }),
      direct = f.s.ring(OWNER, { target: id });
    await f.s.call("instance_fork", { logical_id: a.id }, OWNER);
    let i = await f.s.call("instance_get", { id }, OWNER);
    assert.deepEqual(
      i.pending_messages.map((m: any) => m.id),
      [logical.id, direct.id],
    );
    await f.s.call("agent_update", { id: a.id, run_mode: "fork" }, OWNER);
    i = await f.s.call("instance_get", { id }, OWNER);
    assert.deepEqual(
      i.pending_messages.map((m: any) => m.id),
      [direct.id],
    );
  } finally {
    await f.close();
  }
});

test("a queued snapshot cannot dispatch a message cancelled during the same tick", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false });
    f.s.ring(OWNER, { target: a.id });
    const second = f.s.ring(OWNER, { target: a.id });
    f.s.once("change", () => {
      void f.s.call("message_cancel", { id: second.id }, OWNER);
    });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    f.s.tick();
    assert.equal(f.s.store.get("messages", second.id)!.status, "cancelled");
    assert.equal(f.h.calls.length, 1);
  } finally {
    await f.close();
  }
});

test("archive cancels queued resumes before awaiting stop and resident renewal waits for the old run", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({
      worktree: true,
      git_init: false,
      run_mode: "resident",
    });
    f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const id = `${a.id}-1`;
    const direct = f.s.ring(OWNER, { target: id }),
      logical = f.s.ring(OWNER, { target: a.id });
    let release!: () => void;
    const stop = f.h.stop.bind(f.h);
    f.h.stop = async (handle) => {
      await new Promise<void>((r) => {
        release = r;
      });
      await stop(handle);
    };
    const retiring = f.s.archive(id, OWNER);
    await until(() => !!release);
    assert.equal(f.s.store.get("messages", direct.id)!.status, "cancelled");
    f.s.tick();
    assert.equal(f.h.calls.length, 1);
    assert.equal(f.s.store.get("messages", logical.id)!.status, "queued");
    assert.equal(
      f.s.scheduling(f.s.store.get("messages", logical.id)!).queue_reason.code,
      "instance_busy",
    );
    release();
    await retiring;
    f.h.stop = stop;
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(
      f.s.store.get("messages", logical.id)!.resolved_instance_id,
      `${a.id}-2`,
    );
  } finally {
    await f.close();
  }
});

test("delete cancels queued messages before awaiting doorbell shutdown", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false });
    const m = f.s.ring(OWNER, { target: a.id });
    let release!: () => void;
    f.s.onDoorbellChange = async () =>
      new Promise<void>((r) => {
        release = r;
      });
    const removing = f.s.call("agent_delete", { id: a.id }, OWNER);
    await until(() => !!release);
    f.s.tick();
    assert.equal(f.s.store.get("messages", m.id)!.status, "cancelled");
    assert.equal(f.h.calls.length, 0);
    release();
    await removing;
  } finally {
    await f.close();
  }
});

test("resident startup failure keeps records but releases the invalid session binding", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    const start = f.h.start.bind(f.h);
    f.h.start = () => {
      throw new Error("start failed");
    };
    const first = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.s.active.size === 0);
    assert.equal(f.s.store.get("messages", first.id)!.status, "failed");
    assert.equal(
      f.s.store.get("logical_agents", a.id)!.resident_instance_id,
      null,
    );
    assert.ok(f.s.store.get("instances", `${a.id}-1`));
    f.h.start = start;
    const second = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    assert.equal(
      f.s.store.get("messages", second.id)!.resolved_instance_id,
      `${a.id}-2`,
    );
    assert.equal(f.h.calls[0].kind, "fork");
  } finally {
    await f.close();
  }
});

test("restart reconciles terminal runs, interrupted runs and preparation gaps without replay", async () => {
  const f = await fixture();
  let reopened: Service | undefined;
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    const completed = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    const failed = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    f.h.calls[1].finish({ exitCode: 1, error: "failed task" });
    await until(() => f.s.active.size === 0);
    const interrupted = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 3);
    f.h.calls[2].finish();
    await until(() => f.s.active.size === 0);
    const runs = f.s.store.all("runs");
    for (const m of [completed, failed, interrupted])
      f.s.store.update("messages", m.id, { status: "dispatched" });
    f.s.store.update("runs", runs[2].id, {
      status: "running",
      ended_at: null,
      pid: 123,
    });
    const preparing = f.s.ring(OWNER, { target: a.id });
    f.s.store.update("messages", preparing.id, { status: "dispatched" });
    const queued = f.s.ring(OWNER, { target: a.id });
    await f.s.call("group_update", { id: f.g.id, paused: true }, OWNER);
    await f.s.close();
    const h = new Controlled();
    reopened = new Service(
      join(f.dir, "data"),
      { codex: h },
      "/inner.js",
      process.cwd() + "/skills/hamlet-agent",
    );
    reopened.start();
    assert.equal(reopened.store.get("messages", completed.id)!.status, "done");
    assert.equal(reopened.store.get("messages", failed.id)!.status, "failed");
    assert.equal(
      reopened.store.get("messages", interrupted.id)!.status,
      "failed",
    );
    assert.equal(reopened.store.get("runs", runs[2].id)!.status, "interrupted");
    assert.equal(reopened.store.get("runs", runs[2].id)!.pid, null);
    assert.equal(
      reopened.store.get("messages", preparing.id)!.status,
      "failed",
    );
    assert.equal(reopened.store.get("messages", queued.id)!.status, "queued");
    assert.equal(h.calls.length, 0);
  } finally {
    await reopened?.close();
    await f.close();
  }
});

test("group deletion blocks new agents and queued dispatch until all members are removed", async () => {
  const f = await fixture();
  let release: (() => void) | undefined;
  try {
    await f.agent({ worktree: false });
    const b = await f.agent({
      role_name: "second",
      worktree: false,
      workdir: join(f.dir, "second"),
    });
    const pending = f.s.ring(OWNER, { target: b.id });
    let first = true;
    f.s.onDoorbellChange = async () => {
      if (first) {
        first = false;
        await new Promise<void>((r) => {
          release = r;
        });
      }
    };
    const removing = f.s.call("group_delete", { id: f.g.id }, OWNER);
    await until(() => !!release);
    await assert.rejects(
      f.agent({ role_name: "late", worktree: false }),
      (e: any) => e.statusCode === 409,
    );
    await assert.rejects(
      f.s.call("group_update", { id: f.g.id, paused: false }, OWNER),
      (e: any) => e.statusCode === 409,
    );
    f.s.tick();
    assert.equal(f.h.calls.length, 0);
    release!();
    await removing;
    assert.equal(f.s.store.get("messages", pending.id)!.status, "cancelled");
    assert.equal(f.s.store.get("groups", f.g.id), undefined);
  } finally {
    release?.();
    await f.close();
  }
});

test("stop keeps a run running until the process has actually stopped", async () => {
  const f = await fixture();
  const originalStop = f.h.stop.bind(f.h);
  let release: (() => void) | undefined;
  try {
    const a = await f.agent({ worktree: false });
    const m = f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    f.h.stop = async (handle) => {
      await new Promise<void>((r) => {
        release = r;
      });
      await originalStop(handle);
    };
    const stopping = f.s.stop(`${a.id}-1`);
    await until(() => !!release);
    const run = f.s.store.all("runs")[0];
    assert.equal(f.s.store.get("runs", run.id)!.status, "running");
    assert.equal(f.s.store.get("messages", m.id)!.status, "dispatched");
    release!();
    await stopping;
    assert.equal(f.s.store.get("runs", run.id)!.status, "stopped");
    assert.ok(f.s.store.get("runs", run.id)!.ended_at);
    assert.equal(f.s.store.get("messages", m.id)!.status, "failed");
  } finally {
    f.h.stop = originalStop;
    release?.();
    await f.close();
  }
});
