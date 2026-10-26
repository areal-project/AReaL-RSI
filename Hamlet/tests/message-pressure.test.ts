import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { Service, OWNER } from "../packages/core/src/index.js";
import { fixture, until, Controlled } from "./helpers.js";

function rejected(fn: () => unknown) {
  let error: any;
  try {
    fn();
  } catch (e) {
    error = e;
  }
  assert.equal(error?.statusCode, 409);
  assert.equal(error.details.code, "target_queue_overloaded");
  assert.equal(error.details.status, "rejected");
  assert.equal(error.details.confirmation.params.id, error.details.message_id);
  return error.details;
}

test("queue threshold counts existing queued messages; rejected dedup retains its ID", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    for (let n = 0; n < 6; n++)
      assert.equal(f.s.ring(OWNER, { target: a.id }).status, "queued");
    const p = {
      target: a.id,
      prompt: "retained",
      vars: { k: "v" },
      dedup_key: "overload",
    };
    const detail = rejected(() => f.s.ring(OWNER, p));
    assert.equal(detail.queued_count, 6);
    assert.equal(
      rejected(() => f.s.ring(OWNER, p)).message_id,
      detail.message_id,
    );
    const m = f.s.store.get("messages", detail.message_id)!;
    assert.equal(m.prompt, "retained");
    assert.deepEqual(m.vars, { k: "v" });
    assert.equal(m.queued_at, null);
    assert.equal(m.queue_seq, null);
    assert.equal(
      (await f.s.call("message_list", { status: "rejected" }, OWNER)).length,
      1,
    );
    await assert.rejects(f.s.call("message_retry", { id: m.id }, OWNER));
    await f.s.call(
      "message_cancel",
      { id: f.s.store.all("messages")[0].id },
      OWNER,
    );
    assert.equal(f.s.ring(OWNER, { target: a.id }).status, "queued");
    assert.equal(f.s.store.get("messages", m.id)!.status, "rejected");
  } finally {
    await f.close();
  }
});

test("logical ID, role name and resident instance share the same pressure count; running is excluded", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const id = f.s.store.all("instances")[0].id;
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: n % 2 ? id : a.id });
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: a.role_name, group: f.g.id }))
        .queued_count,
      6,
    );
    const other = await f.agent({
      role_name: "other",
      worktree: false,
      workdir: join(f.dir, "other"),
    });
    assert.equal(f.s.ring(OWNER, { target: other.id }).status, "queued");
  } finally {
    await f.close();
  }
});

test("force push preserves payload, appends to FIFO and cannot enqueue the same message twice", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    for (let n = 0; n < 6; n++)
      f.s.ring(OWNER, { target: a.id, prompt: `old-${n}` });
    const d = rejected(() =>
      f.s.ring(OWNER, { target: a.id, prompt: "forced", dedup_key: "force" }),
    );
    await f.s.call(
      "message_cancel",
      { id: f.s.store.all("messages")[0].id },
      OWNER,
    );
    const later = f.s.ring(OWNER, { target: a.id, prompt: "later" });
    const replies = await Promise.allSettled([
      f.s.call("message_force_push", { id: d.message_id }, OWNER),
      f.s.call("message_force_push", { id: d.message_id }, OWNER),
    ]);
    assert.equal(replies.filter((r) => r.status === "fulfilled").length, 1);
    const m = f.s.store.get("messages", d.message_id)!;
    assert.equal(m.status, "queued");
    assert.equal(m.from_id, "owner");
    assert.equal(m.prompt, "forced");
    assert.ok(m.queue_seq > later.queue_seq);
    assert.equal(m.rejection.forced_by, "owner");
    assert.equal(
      f.s.ring(OWNER, { target: a.id, dedup_key: "force" }).id,
      m.id,
    );
    for (let n = 0; n < 7; n++) {
      f.s.tick();
      await until(() => f.h.calls.length === n + 1);
      assert.ok(
        f.h.calls[n].options.prompt.includes(
          n < 5 ? `old-${n + 1}` : n === 5 ? "later" : "forced",
        ),
      );
      f.h.calls[n].finish();
      await until(() => f.s.active.size === 0);
    }
    await assert.rejects(
      f.s.call("message_force_push", { id: m.id }, OWNER),
      (e: any) => e.statusCode === 409,
    );
    await assert.rejects(
      f.s.call("message_force_push", { id: "unknown" }, OWNER),
      (e: any) => e.statusCode === 404,
    );
    await assert.rejects(
      f.s.call("message_force_push", {}, OWNER),
      (e: any) => e.statusCode === 400,
    );
  } finally {
    await f.close();
  }
});

test("only original sender or Owner may force a rejected message; archived target remains rejected", async () => {
  const f = await fixture();
  try {
    const sender = await f.agent({ worktree: false });
    const peer = await f.agent({
      role_name: "peer",
      worktree: false,
      workdir: join(f.dir, "peer"),
    });
    for (const a of [sender, peer]) f.s.ring(OWNER, { target: a.id });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    const actor = { kind: "instance" as const, id: `${sender.id}-1` };
    const other = { kind: "instance" as const, id: `${peer.id}-1` };
    const target = other.id;
    for (let n = 0; n < 6; n++) f.s.ring(actor, { target });
    const d = rejected(() =>
      f.s.ring(actor, { target, prompt: "from sender" }),
    );
    await assert.rejects(
      f.s.call("message_force_push", { id: d.message_id }, other),
      (e: any) => e.statusCode === 403,
    );
    await assert.rejects(
      f.s.call(
        "message_force_push",
        { id: d.message_id },
        { kind: "doorbell", id: peer.id },
      ),
      (e: any) => e.statusCode === 403,
    );
    const isolatedGroup = await f.s.call(
      "group_create",
      { name: "isolated" },
      OWNER,
    );
    const isolated = await f.agent({
      group: isolatedGroup.id,
      role_name: "outsider",
      worktree: false,
      workdir: join(f.dir, "isolated"),
    });
    f.s.ring(OWNER, { target: isolated.id });
    f.s.tick();
    await until(() => f.h.calls.length === 3);
    await assert.rejects(
      f.s.call(
        "message_force_push",
        { id: d.message_id },
        { kind: "instance", id: `${isolated.id}-1` },
      ),
      (e: any) => e.statusCode === 404,
    );
    const pushed = await f.s.call(
      "message_force_push",
      { id: d.message_id },
      actor,
    );
    assert.equal(pushed.from_id, actor.id);
    const d2 = rejected(() => f.s.ring(actor, { target }));
    await f.s.archive(target, OWNER);
    await assert.rejects(
      f.s.call("message_force_push", { id: d2.message_id }, actor),
      (e: any) => e.statusCode === 409,
    );
    assert.equal(f.s.store.get("messages", d2.message_id)!.status, "rejected");
  } finally {
    await f.close();
  }
});

test("rejected messages survive restart without automatic dispatch", async () => {
  const f = await fixture();
  let reopened: Service | undefined;
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: a.id });
    const d = rejected(() => f.s.ring(OWNER, { target: a.id }));
    for (const m of f.s.store
      .all("messages")
      .filter((m) => m.status === "queued"))
      await f.s.call("message_cancel", { id: m.id }, OWNER);
    await f.s.close();
    const h = new Controlled();
    reopened = new Service(
      join(f.dir, "data"),
      { codex: h },
      "/inner.js",
      process.cwd() + "/skills/hamlet-agent",
    );
    reopened.start();
    assert.equal(h.calls.length, 0);
    assert.equal(
      reopened.store.get("messages", d.message_id)!.status,
      "rejected",
    );
    await reopened.call("message_force_push", { id: d.message_id }, OWNER);
    reopened.tick();
    await until(() => h.calls.length === 1);
    h.calls[0].finish();
    await until(() => reopened!.active.size === 0);
  } finally {
    await reopened?.close();
    // f.close() can close the already closed SQLite handle safely.
    await f.close();
  }
});

test("HTTP and CLI return actionable rejection details and force only that record", async () => {
  const { createDaemon } = await import("../packages/daemon/src/index.js");
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const { childEnvironment } = await import("../packages/core/src/runtime.js");
  const dir = mkdtempSync(join(tmpdir(), "hamlet-pressure-http-"));
  const d = await createDaemon({ dir, harnesses: { codex: new Controlled() } });
  try {
    const url = await d.listen();
    const g = await d.service.call("group_create", { name: "test" }, OWNER);
    await d.service.call("group_update", { id: g.id, paused: true }, OWNER);
    const a = await d.service.call(
      "agent_create",
      {
        group: g.id,
        role_name: "target",
        worktree: false,
        workdir: join(dir, "work"),
        run_mode: "resident",
      },
      OWNER,
    );
    const request = (tool: string, payload: any) =>
      fetch(`${url}/api/tools/${tool}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${d.service.auth.ownerToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
    for (let n = 0; n < 6; n++)
      assert.equal((await request("message_to", { target: a.id })).status, 200);
    const response = await request("message_to", {
      target: a.id,
      dedup_key: "cli",
    });
    assert.equal(response.status, 409);
    const body = (await response.json()) as any;
    assert.match(body.error, /6 条/);
    const id = body.details.message_id;
    assert.equal(body.details.confirmation.tool, "message_force_push");
    await assert.rejects(
      promisify(execFile)(
        process.execPath,
        [
          join(process.cwd(), "bin/hamlet.js"),
          "call",
          "message_to",
          JSON.stringify({ target: a.id, dedup_key: "cli" }),
        ],
        {
          env: {
            ...childEnvironment(),
            HAMLET_URL: url,
            HAMLET_TOKEN: d.service.auth.ownerToken,
          },
        },
      ),
      (e: any) => {
        assert.equal(e.code, 1);
        assert.equal(JSON.parse(e.stderr).details.message_id, id);
        return true;
      },
    );
    const forced = await request("message_force_push", { id });
    assert.equal(forced.status, 200);
    assert.equal(((await forced.json()) as any).id, id);
    assert.equal((await request("message_force_push", { id })).status, 409);
    assert.equal(
      (await request("message_force_push", { id: "unknown" })).status,
      404,
    );
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("fork logical submissions do not aggregate pressure; real instances have independent queues", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "fork" });
    // Create two real sessions in the same direct work directory.
    for (let n = 0; n < 2; n++) {
      f.s.ring(OWNER, { target: a.id });
      f.s.tick();
      await until(() => f.h.calls.length === n + 1);
      f.h.calls[n].finish();
      await until(() => f.s.active.size === 0);
    }
    const [first, second] = f.s.store.all("instances");
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: first.id });
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: first.id })).queued_count,
      6,
    );
    for (let n = 0; n < 6; n++)
      assert.equal(f.s.ring(OWNER, { target: second.id }).status, "queued");
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: second.id })).queued_count,
      6,
    );
    for (let n = 0; n < 10; n++)
      assert.equal(f.s.ring(OWNER, { target: a.id }).status, "queued");
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: first.id })).queued_count,
      6,
    );
  } finally {
    await f.close();
  }
});

test("explicit resident forks bypass the resident queue and keep an independent real queue", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false, run_mode: "resident" });
    for (let n = 0; n < 2; n++) {
      if (n === 0) f.s.ring(OWNER, { target: a.id });
      else await f.s.call("instance_fork", { logical_id: a.id }, OWNER);
      f.s.tick();
      await until(() => f.h.calls.length === n + 1);
      f.h.calls[n].finish();
      await until(() => f.s.active.size === 0);
    }
    const [resident, fork] = f.s.store.all("instances");
    assert.equal(
      f.s.store.get("logical_agents", a.id)!.resident_instance_id,
      resident.id,
    );
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: a.id });
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: resident.id })).queued_count,
      6,
    );
    for (let n = 0; n < 6; n++) f.s.ring(OWNER, { target: fork.id });
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: fork.id })).queued_count,
      6,
    );
    for (let n = 0; n < 8; n++)
      assert.equal(
        (await f.s.call("instance_fork", { logical_id: a.id }, OWNER)).status,
        "queued",
      );
    assert.equal(
      rejected(() => f.s.ring(OWNER, { target: a.id })).queued_count,
      6,
    );
  } finally {
    await f.close();
  }
});
