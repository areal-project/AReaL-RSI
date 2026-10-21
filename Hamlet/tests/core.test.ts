import { test } from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  readlinkSync,
} from "node:fs";
import { join } from "node:path";
import { Service, OWNER, git, render } from "../packages/core/src/index.js";
import { fixture, until, Controlled } from "./helpers.js";

test("fork 并行、来源头、自动提交、指定实例 resume、快照不被更新污染", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({
      prompt_tpl: "处理 {{task}}",
      system_prompt_tpl: "角色 {{role}}",
    });
    const m = f.s.ring(OWNER, { target: l.id, vars: { task: "A" } });
    f.s.ring(OWNER, { target: l.id, prompt: "B" });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(f.s.store.all("instances").length, 2);
    assert.match(f.h.calls[0].options.prompt, /来自 ring 调用方.*\n处理 A/s);
    const i = f.s.store.all("instances")[0];
    writeFileSync(join(i.worktree_path, "a.txt"), "hello");
    f.h.calls[0].finish();
    f.h.calls[1].finish();
    await until(() => f.s.active.size === 0);
    assert.equal(f.s.store.get("messages", m.id)?.status, "done");
    assert.match(
      git(i.worktree_path, "log", "-1", "--format=%s"),
      /Hamlet run/,
    );
    await f.s.call(
      "agent_update",
      { id: l.id, prompt_tpl: "new {{missing}}", model: "new-model" },
      OWNER,
    );
    f.s.ring(OWNER, {
      target: i.id,
      vars: { task: "追问" },
      system_prompt: "忽略",
    });
    f.s.tick();
    await until(() => f.h.calls.length === 3);
    assert.equal(f.h.calls[2].kind, "resume");
    assert.match(f.h.calls[2].options.prompt, /处理 追问/);
    assert.notEqual(f.h.calls[2].options.model, "new-model");
    f.h.calls[2].finish();
    await until(() => f.s.active.size === 0);
    const r = f.s.store.all("runs").at(-1)!;
    assert.match(readFileSync(r.events_path, "utf8"), /resume 无法更换/);
  } finally {
    await f.close();
  }
});

test("常驻首次触发创建、FIFO、失败后继续、归档后换新与序号不复用", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ run_mode: "resident" });
    for (const prompt of ["一", "二", "三"])
      f.s.ring(OWNER, { target: l.id, prompt });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const resident = f.s.store.get(
      "logical_agents",
      l.id,
    )?.resident_instance_id;
    assert.ok(resident);
    assert.equal(f.s.store.all("instances").length, 1);
    f.h.calls[0].finish({ exitCode: 1, error: "bad" });
    await until(() => f.s.active.size === 0);
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(f.h.calls[1].kind, "resume");
    assert.match(f.h.calls[1].options.prompt, /二/);
    f.h.calls[1].finish();
    await until(() => f.s.active.size === 0);
    f.s.tick();
    await until(() => f.h.calls.length === 3);
    f.h.calls[2].finish();
    await until(() => f.s.active.size === 0);
    assert.equal(f.s.store.all("instances").length, 1);
    await f.s.call("instance_archive", { id: resident }, OWNER);
    assert.equal(
      existsSync(f.s.store.get("instances", resident)?.worktree_path),
      false,
    );
    assert.ok(
      git(join(f.dir, "work/main"), "branch", "--list", `agent/${resident}`),
    );
    assert.throws(
      () => f.s.ring(OWNER, { target: resident, prompt: "x" }),
      /归档/,
    );
    f.s.ring(OWNER, { target: l.id, prompt: "新" });
    f.s.tick();
    await until(() => f.h.calls.length === 4);
    assert.equal(
      f.s.store.get("logical_agents", l.id)?.resident_instance_id,
      l.id + "-2",
    );
  } finally {
    await f.close();
  }
});

test("缺模板变量不创建实例；显式空 prompt 与 system prompt 分别覆盖模板", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({
      prompt_tpl: "{{missing}}",
      system_prompt_tpl: "{{secret}}",
    });
    const m = f.s.ring(OWNER, { target: l.id, prompt: "" });
    f.s.tick();
    assert.equal(f.s.store.get("messages", m.id)?.status, "failed");
    assert.equal(f.s.store.all("instances").length, 0);
    f.s.ring(OWNER, { target: l.id, prompt: "", system_prompt: "" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    assert.match(
      f.h.calls[0].options.prompt,
      /ring 调用方 \(owner\)\]\n\n\n\[Hamlet runtime\]/,
    );
    assert.throws(() => render("{{toString}}", {}), /缺少/);
  } finally {
    await f.close();
  }
});

test("direct 同路径跨逻辑 Agent / group 串行，暂停保留消息，失败可重试/取消", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({ worktree: false });
    const g2 = await f.s.call("group_create", { name: "其他" }, OWNER);
    const b = await f.agent({
      role_name: "第二",
      group: g2.id,
      worktree: false,
    });
    await f.s.call("group_update", { id: f.g.id, paused: true }, OWNER);
    const m = f.s.ring(OWNER, { target: a.id, prompt: "a" });
    f.s.ring(OWNER, { target: b.id, prompt: "b" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    assert.equal(f.s.store.get("messages", m.id)?.status, "queued");
    await f.s.call("group_update", { id: f.g.id, paused: false }, OWNER);
    f.s.tick();
    assert.equal(f.h.calls.length, 1);
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    f.h.calls[1].finish({ exitCode: 1, error: "fail" });
    await until(() => f.s.active.size === 0);
    await f.s.call("message_retry", { id: m.id }, OWNER);
    await f.s.call("message_cancel", { id: m.id }, OWNER);
    assert.equal(f.s.store.get("messages", m.id)?.status, "cancelled");
  } finally {
    await f.close();
  }
});

test("去重规范化角色/ID、默认窗口与过期；doorbell 令牌只可触发自己", async () => {
  const f = await fixture();
  try {
    const l = await f.agent(),
      b = await f.agent({ role_name: "B" });
    const actor = { kind: "doorbell" as const, id: l.id };
    const m = f.s.ring(actor, {
      target: l.role_name,
      dedup_key: "same",
      prompt: "x",
    });
    const repeat = f.s.ring(actor, {
      target: l.id,
      dedup_key: "same",
      prompt: "x",
    });
    assert.equal(m.id, repeat.id);
    assert.equal(repeat.deduplicated, true);
    f.s.store.update("messages", m.id, { dedup_until: "2000-01-01" });
    assert.notEqual(
      f.s.ring(actor, { target: l.id, dedup_key: "same" }).id,
      m.id,
    );
    assert.throws(() => f.s.ring(actor, { target: b.id }), /不存在/);
    await assert.rejects(f.s.call("state", {}, actor), /无权限/);
  } finally {
    await f.close();
  }
});

test("身份权限、同组解析和跨组隐藏、worker 无管理权限", async () => {
  const f = await fixture();
  try {
    const worker = await f.agent({ worktree: false }),
      admin = await f.agent({
        role_name: "管理员",
        identity: "admin",
        worktree: false,
        workdir: join(f.dir, "admin"),
      });
    const g2 = await f.s.call("group_create", { name: "隔离" }, OWNER),
      other = await f.agent({
        role_name: "其他",
        group: g2.id,
        workdir: join(f.dir, "other"),
      });
    for (const id of [worker.id, admin.id])
      f.s.ring(OWNER, { target: id, prompt: "x" });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    const wa = { kind: "instance" as const, id: worker.id + "-1" },
      aa = { kind: "instance" as const, id: admin.id + "-1" };
    assert.equal((await f.s.call("group_agents", {}, wa)).length, 2);
    await assert.rejects(f.s.call("agent_get", { id: other.id }, wa), /不存在/);
    assert.throws(() => f.s.ring(wa, { target: other.id }), /不存在/);
    await assert.rejects(
      f.s.call("agent_update", { id: worker.id, model: "x" }, wa),
      /无权限/,
    );
    await assert.rejects(
      f.s.call("agent_update", { id: admin.id, model: "x" }, aa),
      /无权限/,
    );
    await assert.rejects(
      f.s.call(
        "agent_create",
        { role_name: "bad", identity: "admin", workdir: join(f.dir, "bad") },
        aa,
      ),
      /无权限/,
    );
    const made = await f.s.call(
      "agent_create",
      { role_name: "组员", workdir: join(f.dir, "new") },
      aa,
    );
    assert.equal(made.group_id, f.g.id);
    await f.s.call("agent_update", { id: worker.id, model: "ok" }, aa);
    assert.equal(f.s.resolve("开发者#1", wa).instance.id, wa.id);
    const me = await f.s.call("whoami", {}, wa);
    assert.equal(me.identity, "worker");
    assert.equal(me.id, wa.id);
    const token = f.s.auth.sign(wa);
    assert.deepEqual(f.s.auth.verify(token), wa);
    assert.throws(() => f.s.auth.verify(token.slice(0, -4) + "aaaa"), /认证/);
  } finally {
    await f.close();
  }
});

test("迁移必须显式授权；非 git diff 冲突、应用、刷新 base 不改变老实例基准", async () => {
  const f = await fixture();
  try {
    const root = join(f.dir, "plain");
    mkdirSync(root);
    writeFileSync(join(root, "file.txt"), "base\n");
    await assert.rejects(
      f.agent({ workdir: root, git_init: false }),
      (e) => !!(e as any).details.needs_migration,
    );
    await assert.rejects(
      f.agent({ workdir: root, git_init: false, migrate: "false" }),
      (e) => !!(e as any).details.needs_migration,
    );
    symlinkSync("missing-target", join(root, "link"));
    const l = await f.agent({ workdir: root, git_init: false, migrate: true });
    f.s.ring(OWNER, { target: l.id, prompt: "x" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const i = f.s.store.all("instances")[0];
    writeFileSync(join(i.worktree_path, "file.txt"), "agent\n");
    writeFileSync(join(root, "main/file.txt"), "main\n");
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    let d = await f.s.call("instance_diff", { id: i.id }, OWNER);
    assert.equal(d.files[0].conflict, true);
    unlinkSync(join(i.worktree_path, "link"));
    symlinkSync("changed-target", join(i.worktree_path, "link"));
    await f.s.call("instance_merge", { id: i.id, files: ["link"] }, OWNER);
    assert.equal(readlinkSync(join(root, "main/link")), "changed-target");
    await assert.rejects(
      f.s.call("instance_merge", { id: i.id, files: ["file.txt"] }, OWNER),
      /冲突/,
    );
    await f.s.call("workdir_refresh", { id: l.workdir_id }, OWNER);
    d = await f.s.call("instance_diff", { id: i.id }, OWNER);
    assert.equal(d.files[0].conflict, true);
    await f.s.call(
      "instance_merge",
      { id: i.id, files: ["file.txt"], confirm_overwrite: true },
      OWNER,
    );
    assert.equal(readFileSync(join(root, "main/file.txt"), "utf8"), "agent\n");
    await assert.rejects(
      f.s.call("instance_merge", { id: i.id, files: ["../../evil"] }, OWNER),
      /不在 diff/,
    );
    f.s.ring(OWNER, { target: l.id, prompt: "new" });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(
      readFileSync(
        join(f.s.store.all("instances")[1].worktree_path, "file.txt"),
        "utf8",
      ),
      "main\n",
    );
    await f.s.call("instance_archive", { id: i.id }, OWNER);
    assert.equal(existsSync(i.worktree_path), false);
  } finally {
    await f.close();
  }
});

test("git merge、未提交 main 警告、worktree 失败清理", async () => {
  const f = await fixture();
  try {
    const l = await f.agent();
    const w = f.s.store.get("workdirs", l.workdir_id)!;
    writeFileSync(join(w.main_path, "dirty"), "uncommitted");
    f.s.ring(OWNER, { target: l.id, prompt: "x" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const i = f.s.store.all("instances")[0];
    assert.equal(existsSync(join(i.worktree_path, "dirty")), false);
    writeFileSync(join(i.worktree_path, "result"), "done");
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    await f.s.call("instance_merge", { id: i.id }, OWNER);
    assert.equal(readFileSync(join(w.main_path, "result"), "utf8"), "done");
    assert.match(
      readFileSync(f.s.store.all("runs")[0].events_path, "utf8"),
      /未提交改动/,
    );
    f.s.store.update("workdirs", w.id, { base_ref: "does-not-exist" });
    const m = f.s.ring(OWNER, { target: l.id, prompt: "bad" });
    f.s.tick();
    assert.equal(f.s.store.get("messages", m.id)?.status, "failed");
    assert.equal(existsSync(join(w.root, "worktrees", l.id + "-2")), false);
  } finally {
    await f.close();
  }
});

test("终止/删除正在运行 Agent，取消队列并拒绝已删除身份", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ run_mode: "resident" });
    f.s.ring(OWNER, { target: l.id, prompt: "x" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const m = f.s.ring(OWNER, { target: l.id, prompt: "queued" });
    await f.s.call("agent_delete", { id: l.id }, OWNER);
    assert.equal(f.s.store.all("runs")[0].status, "stopped");
    assert.equal(f.s.store.get("messages", m.id)?.status, "cancelled");
    assert.equal(f.s.store.all("instances")[0].status, "archived");
    assert.throws(
      () => f.s.context({ kind: "instance", id: l.id + "-1" }),
      /不存在/,
    );
    assert.throws(() => f.s.ring(OWNER, { target: l.id }), /不存在/);
  } finally {
    await f.close();
  }
});

test("重启恢复：running 标 interrupted、消息不重放，queued 继续执行", async () => {
  const f = await fixture();
  let recovered: Service | undefined;
  try {
    const l = await f.agent({ run_mode: "resident", worktree: false });
    const m = f.s.ring(OWNER, { target: l.id, prompt: "first" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    const queued = f.s.ring(OWNER, { target: l.id, prompt: "second" });
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    const r = f.s.store.all("runs")[0];
    f.s.store.update("runs", r.id, { status: "running" });
    f.s.store.update("messages", m.id, { status: "dispatched" });
    f.s.store.update("instances", l.id + "-1", { status: "running" });
    f.s.store.close();
    const h = new Controlled();
    recovered = new Service(join(f.dir, "data"), { codex: h }, "/inner.js");
    recovered.start();
    assert.equal(recovered.store.get("runs", r.id)?.status, "interrupted");
    assert.equal(recovered.store.get("messages", m.id)?.status, "failed");
    await until(() => h.calls.length === 1);
    assert.equal(h.calls[0].kind, "resume");
    assert.equal(
      recovered.store.get("messages", queued.id)?.status,
      "dispatched",
    );
    h.calls[0].finish();
    await until(() => recovered!.active.size === 0);
  } finally {
    await recovered?.close(); /* original database deliberately closed to model crash */
    rmSync(f.dir, { recursive: true, force: true });
  }
});

test("UI 手动 fork 常驻逻辑 Agent 时不替换已登记的常驻实例", async () => {
  const f = await fixture();
  try {
    const l = await f.agent({ run_mode: "resident" });
    f.s.ring(OWNER, { target: l.id, prompt: "resident" });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    await f.s.call(
      "instance_fork",
      { logical_id: l.id, prompt: "fork" },
      OWNER,
    );
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(f.h.calls[1].kind, "fork");
    assert.equal(
      f.s.store.get("logical_agents", l.id)?.resident_instance_id,
      l.id + "-1",
    );
    assert.equal(f.s.store.all("instances").length, 2);
  } finally {
    await f.close();
  }
});
