import { EventEmitter } from "node:events";
import { randomBytes, randomUUID } from "node:crypto";
import {
  appendFileSync,
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { join, isAbsolute, dirname } from "node:path";
import { Store } from "./store.js";
import { Auth } from "./auth.js";
import {
  AppError,
  now,
  render,
  type Actor,
  type Row,
  type Harness,
  type RunHandle,
} from "./types.js";
import * as wd from "./workdirs.js";
import { agentSkillPath, installAgentSkill } from "./skills.js";
import {
  instanceEnvironment,
  workdirMetadataDir,
  childEnvironment,
} from "./runtime.js";
import { Observer, senderInfo, triggerSnapshot, page } from "./observe.js";
const short = (n: number) => {
  const alphabet = "abcdefghijklmnopqrstuvwxyz234567";
  return [...randomBytes(n)].map((x) => alphabet[x % 32]).join("");
};
export const OWNER: Actor = { kind: "owner" };
export class Service extends EventEmitter {
  store: Store;
  auth: Auth;
  observer: Observer;
  url = "";
  uiUrl = "";
  stopping = false;
  active = new Map<
    string,
    { handle: RunHandle; harness: Harness; run: string; task: Promise<void> }
  >();
  onDoorbellChange?: (id: string, action: string) => Promise<unknown>;
  private timer?: NodeJS.Timeout;
  private deletingGroups = new Set<string>();
  private stopRequests = new Map<string, "stopped" | "interrupted">();
  constructor(
    public dir: string,
    public harnesses: Record<string, Harness>,
    public cliPath: string,
    public skillDir = join(dirname(cliPath), "../skills/hamlet-agent"),
  ) {
    super();
    this.store = new Store(dir);
    this.auth = new Auth(dir);
    this.observer = new Observer(this);
    mkdirSync(join(dir, "runs"), { recursive: true });
  }
  start() {
    // A fresh daemon never replays an in-flight message.
    this.store.db.transaction(() => {
      for (const run of this.store
        .all("runs")
        .filter((r) => r.status === "running"))
        this.store.update("runs", run.id, {
          status: "interrupted",
          ended_at: now(),
          pid: null,
          error: "daemon 重启中断了运行",
        });
      for (const message of this.store
        .all("messages")
        .filter((m) => m.status === "dispatched")) {
        const run = message.run_id && this.store.get("runs", message.run_id);
        this.store.update("messages", message.id, {
          status: run?.status === "succeeded" ? "done" : "failed",
          error:
            run?.status === "succeeded"
              ? null
              : run?.error || "daemon 在执行或准备实例时中断；可手动重试",
        });
      }
    })();
    for (const i of this.store
      .all("instances")
      .filter((i) => ["preparing", "running"].includes(i.status))) {
      if (i.status === "preparing") {
        try {
          wd.cleanup(i.config_snapshot.workdir, i);
        } catch {}
        this.store.update("instances", i.id, {
          status: "archived",
          archived_at: now(),
        });
        const l = this.store.get("logical_agents", i.logical_id);
        if (l?.resident_instance_id === i.id)
          this.store.update("logical_agents", i.logical_id, {
            resident_instance_id: null,
          });
      } else this.store.update("instances", i.id, { status: "idle" });
    }
    for (const instance of this.store.all("instances"))
      this.releaseUnstartedResident(instance.id);
    this.timer = setInterval(() => this.tick(), 100);
    this.tick();
  }
  changed() {
    this.emit("change");
  }
  context(a: Actor): Row {
    if (a.kind === "owner") return { identity: "owner" };
    const l =
      a.kind === "doorbell"
        ? this.store.get("logical_agents", a.id)
        : this.store.get(
            "logical_agents",
            this.store.get("instances", a.id)?.logical_id || "",
          );
    if (
      !l ||
      l.deleted_at ||
      (a.kind === "instance" && this.store.get("instances", a.id)?.archived_at)
    )
      throw new AppError("不存在", 404);
    return {
      ...l,
      actor_id: a.kind === "instance" ? a.id : l.id,
      kind: a.kind,
    };
  }
  logical(id: string, a: Actor, manage = false): Row {
    const l = this.store.get("logical_agents", id),
      c = this.context(a);
    if (!l || l.deleted_at || (a.kind !== "owner" && l.group_id !== c.group_id))
      throw new AppError("不存在", 404);
    if (
      manage &&
      a.kind !== "owner" &&
      (a.kind !== "instance" ||
        c.identity !== "admin" ||
        l.identity !== "worker")
    )
      throw new AppError("无权限", 403);
    return l;
  }
  instance(id: string, a: Actor, manage = false): Row {
    const i = this.store.get("instances", id);
    if (!i) throw new AppError("不存在", 404);
    this.logical(i.logical_id, a, manage);
    return i;
  }
  owner(a: Actor) {
    if (a.kind !== "owner") throw new AppError("无权限", 403);
  }
  resolve(target: string, a: Actor, group?: string): Row {
    const c = this.context(a),
      g = a.kind === "owner" ? group : c.group_id;
    let l = this.store.get("logical_agents", target);
    if (l) return { logical: this.logical(l.id, a) };
    const i = this.store.get("instances", target);
    if (i)
      return {
        logical: this.logical(i.logical_id, a),
        instance: this.instance(i.id, a),
      };
    const match = target.match(/^(.*)#(\d+)$/),
      role = match ? match[1] : target;
    const candidates = this.store
      .all("logical_agents")
      .filter(
        (l) =>
          !l.deleted_at && l.role_name === role && (!g || l.group_id === g),
      );
    if (candidates.length !== 1)
      throw new AppError(
        candidates.length ? "角色名不唯一，请指定 group" : "不存在",
        404,
      );
    l = this.logical(candidates[0].id, a);
    if (match) {
      const found = this.store
        .all("instances")
        .find((i) => i.logical_id === l!.id && i.seq === Number(match[2]));
      if (!found) throw new AppError("不存在", 404);
      return { logical: l, instance: found };
    }
    return { logical: l };
  }
  ring(a: Actor, p: Row, source = "ring"): Row {
    if (typeof p.target !== "string" || !p.target)
      throw new AppError("target 必填");
    for (const k of ["prompt", "system_prompt", "dedup_key"])
      if (p[k] !== undefined && typeof p[k] !== "string")
        throw new AppError(`${k} 必须是字符串`);
    if (
      p.vars !== undefined &&
      (!p.vars || Array.isArray(p.vars) || typeof p.vars !== "object")
    )
      throw new AppError("vars 必须是对象");
    const { logical: l, instance: i } = this.resolve(p.target, a, p.group);
    if (i?.archived_at) throw new AppError("实例已归档", 409);
    if (a.kind === "doorbell" && l.id !== a.id)
      throw new AppError("不存在", 404);
    if (p.force_fork) this.owner(a);
    const target = i?.id || l.id;
    const fromId = a.kind === "owner" ? "owner" : a.id;
    const window = duration(p.dedup_window ?? "24h");
    if (p.dedup_key) {
      const old = this.store
        .all("messages")
        .find(
          (m) =>
            m.from_id === fromId &&
            m.target_id === target &&
            m.dedup_key === p.dedup_key &&
            m.dedup_until > now(),
        );
      if (old) {
        if (old.status === "rejected") this.rejectMessage(old);
        return { ...this.observer.message(old, a), deduplicated: true };
      }
    }
    const c = this.context(a),
      from =
        a.kind === "owner"
          ? senderInfo({ from_id: "owner", source }).label
          : a.kind === "doorbell"
            ? `doorbell: ${l.role_name}`
            : `${c.role_name}#${this.store.get("instances", a.id)!.seq}`;
    const rejection = this.queueRejection({
      target_id: target,
      force_fork: !!p.force_fork,
    });
    const rejected = !!rejection;
    const m = {
      id: randomUUID(),
      target_id: target,
      from_id: fromId,
      from_role: from,
      source: a.kind === "doorbell" ? "doorbell_script" : source,
      prompt: p.prompt ?? null,
      system_prompt: p.system_prompt ?? null,
      vars: p.vars || {},
      dedup_key: p.dedup_key || null,
      dedup_until: new Date(Date.now() + window).toISOString(),
      status: rejected ? "rejected" : "queued",
      rejection,
      error: rejection ? this.rejectionText(rejection.queued_count) : null,
      created_at: now(),
      queued_at: rejected ? null : now(),
      queue_seq: rejected ? null : this.nextQueueSeq(),
      force_fork: !!p.force_fork,
    };
    this.store.insert("messages", m);
    if (a.kind === "doorbell")
      this.store.update("doorbells", a.id, { last_ring_at: now() });
    this.changed();
    if (rejected) this.rejectMessage(m);
    return this.observer.message(m, a);
  }
  private rejectionText(count: number) {
    return `目标 Agent 已有 ${count} 条排队消息，本次消息已拒绝。是否确认继续推送？`;
  }
  private queueRejection(m: Row) {
    const queue = this.pressureQueue(m);
    const count = queue
      ? this.queuedMessages().filter(
          (other) => this.pressureQueue(other) === queue,
        ).length
      : 0;
    return count > 5
      ? {
          code: "target_queue_overloaded",
          queued_count: count,
          threshold: 5,
          rejected_at: now(),
        }
      : null;
  }
  private pressureQueue(m: Row): string | null {
    try {
      const { l, i } = this.route(m);
      if (i) return `instance:${i.id}`;
      // Before first dispatch, resident messages share the future session queue.
      if (l.run_mode === "resident" && !m.force_fork) return `resident:${l.id}`;
      return null; // A fork logical target creates a separate session per message.
    } catch {
      return null; // Invalid queued targets are handled by the scheduler.
    }
  }
  private nextQueueSeq(): number {
    return (
      this.store.db
        .prepare("SELECT COALESCE(MAX(queue_seq),0)+1 AS n FROM messages")
        .get() as { n: number }
    ).n;
  }
  private queuedMessages() {
    return this.store
      .all("messages")
      .filter((m) => m.status === "queued")
      .sort((a, b) => a.queue_seq - b.queue_seq);
  }
  private rejectMessage(m: Row): never {
    throw new AppError(m.error, 409, {
      ...m.rejection,
      message_id: m.id,
      target_id: m.target_id,
      status: "rejected",
      confirmation: { tool: "message_force_push", params: { id: m.id } },
    });
  }
  private forcePush(p: Row, a: Actor) {
    this.context(a);
    if (typeof p.id !== "string" || !p.id)
      throw new AppError("id 必须是被拒绝消息的 ID");
    const m = this.store.get("messages", p.id);
    if (!m) throw new AppError("消息不存在", 404);
    const t = this.resolve(m.target_id, a);
    if (
      a.kind !== "owner" &&
      (m.from_id !== a.id || senderInfo(m).kind !== "instance")
    )
      throw new AppError("只能强推自己被拒绝的消息", 403);
    if (m.status !== "rejected")
      throw new AppError(
        "只能强推被拒绝的消息；此消息已推送或不是 rejected 状态",
        409,
      );
    if (t.instance?.archived_at) throw new AppError("目标实例已归档", 409);
    // No await between checking status and updating: concurrent calls cannot enqueue twice.
    this.store.update("messages", m.id, {
      status: "queued",
      error: null,
      queued_at: now(),
      queue_seq: this.nextQueueSeq(),
      rejection: {
        ...m.rejection,
        forced_at: now(),
        forced_by: a.kind === "owner" ? "owner" : a.id,
      },
    });
    this.changed();
    return this.observer.message(this.store.get("messages", m.id)!, a);
  }
  async call(name: string, p: Row, a: Actor): Promise<any> {
    if (a.kind === "doorbell") throw new AppError("无权限", 403);
    switch (name) {
      case "whoami": {
        const c = this.context(a);
        const i = a.kind === "instance" ? this.instance(a.id, a) : null;
        const w = i?.config_snapshot.workdir;
        return {
          id: i?.id || "owner",
          role_name: c.role_name || "用户",
          group: c.group_id,
          identity: c.identity,
          shared_dir: w?.shared_path || null,
        };
      }
      case "group_create":
        this.owner(a);
        if (!p.name?.trim()) throw new AppError("name 必填");
        return this.insert("groups", {
          id: "g-" + short(4),
          name: p.name,
          paused: 0,
          created_at: now(),
        });
      case "group_list":
        this.owner(a);
        return this.store.all("groups");
      case "group_update": {
        this.owner(a);
        if (!this.store.get("groups", p.id)) throw new AppError("不存在", 404);
        if (this.deletingGroups.has(p.id))
          throw new AppError("Group 正在删除", 409);
        this.store.update("groups", p.id, {
          ...(p.name !== undefined ? { name: p.name } : {}),
          ...(p.paused !== undefined ? { paused: !!p.paused } : {}),
        });
        this.changed();
        return this.store.get("groups", p.id);
      }
      case "group_delete": {
        this.owner(a);
        if (this.deletingGroups.has(p.id))
          throw new AppError("Group 正在删除", 409);
        this.deletingGroups.add(p.id);
        this.store.update("groups", p.id, { paused: true });
        try {
          for (const l of this.store
            .all("logical_agents")
            .filter((l) => l.group_id === p.id && !l.deleted_at))
            await this.deleteAgent(l.id, a);
          this.store.db.prepare("DELETE FROM groups WHERE id=?").run(p.id);
          this.changed();
          return { ok: true };
        } finally {
          this.deletingGroups.delete(p.id);
        }
      }
      case "agent_create":
        return this.createAgent(p, a);
      case "agent_update":
        return this.updateAgent(p, a);
      case "agent_delete":
        await this.deleteAgent(p.id, a);
        return { ok: true };
      case "group_agents":
      case "agent_list": {
        const c = this.context(a);
        return this.store
          .all("logical_agents")
          .filter(
            (l) =>
              !l.deleted_at &&
              (a.kind === "owner"
                ? !p.group || l.group_id === p.group
                : l.group_id === c.group_id),
          )
          .map((l) => ({
            ...l,
            workdir: this.store.get("workdirs", l.workdir_id),
            execution: this.observer.execution(l, a),
            instances: this.store
              .all("instances")
              .filter((i) => i.logical_id === l.id),
          }));
      }
      case "agent_get": {
        const l = this.logical(p.id, a);
        return {
          ...l,
          workdir: this.store.get("workdirs", l.workdir_id),
          execution: this.observer.execution(l, a),
          instances: this.store
            .all("instances")
            .filter((i) => i.logical_id === l.id),
          doorbell: this.store.get("doorbells", l.id),
        };
      }
      case "instance_get": {
        const i =
          a.kind === "owner"
            ? this.store.get("instances", p.id)
            : this.instance(p.id, a);
        if (!i) throw new AppError("不存在", 404);
        return {
          ...i,
          execution: this.observer.execution(
            this.store.get("logical_agents", i.logical_id)!,
            a,
            i.config_snapshot.workdir,
          ),
          pending_messages: this.queuedMessages()
            .filter((m) => this.pressureQueue(m) === `instance:${i.id}`)
            .map((m) => this.observer.message(m, a)),
          runs: this.store
            .all("runs")
            .filter((r) => r.instance_id === i.id)
            .map((r) => this.observer.run(r, a)),
        };
      }
      case "instance_archive":
        await this.archive(p.id, a);
        return { ok: true };
      case "instance_renew_resident": {
        const l = this.logical(p.logical_id, a, true);
        if (l.run_mode !== "resident") throw new AppError("不是常驻模式");
        if (l.resident_instance_id)
          await this.archive(l.resident_instance_id, a);
        return { ok: true };
      }
      case "run_stop": {
        this.instance(p.instance_id, a, true);
        await this.stop(p.instance_id);
        return { ok: true };
      }
      case "instance_fork": {
        this.owner(a);
        const l = this.logical(p.logical_id, a);
        return this.ring(
          a,
          { target: l.id, prompt: p.prompt, vars: p.vars, force_fork: true },
          "ui",
        );
      }
      case "message_to":
        return this.ring(a, p, a.kind === "owner" ? "host" : "message_to");
      case "message_force_push":
        return this.forcePush(p, a);
      case "message_list":
        return this.observer.messages(p, a);
      case "message_get":
        this.owner(a);
        return this.observer.messageGet(p.id, a);
      case "run_list":
        return this.observer.runs(p, a);
      case "run_get":
        return this.observer.runGet(p.id, a);
      case "message_retry":
      case "message_cancel": {
        this.owner(a);
        const m = this.store.get("messages", p.id);
        if (!m) throw new AppError("不存在", 404);
        if (name === "message_cancel") {
          if (!["queued", "rejected"].includes(m.status))
            throw new AppError("只能取消排队或被拒绝的消息");
          this.store.update("messages", m.id, {
            status: "cancelled",
            error: null,
          });
        } else {
          if (!["failed", "cancelled"].includes(m.status))
            throw new AppError("只能重试失败或已取消的消息");
          const t = this.resolve(m.target_id, a);
          if (t.instance?.archived_at) throw new AppError("实例已归档");
          const rejection = this.queueRejection(m);
          this.store.update("messages", m.id, {
            status: rejection ? "rejected" : "queued",
            error: rejection
              ? this.rejectionText(rejection.queued_count)
              : null,
            rejection: rejection || m.rejection,
            run_id: null,
            resolved_instance_id: null,
            queued_at: rejection ? null : now(),
            queue_seq: rejection ? null : this.nextQueueSeq(),
          });
          if (rejection) {
            this.changed();
            this.rejectMessage(this.store.get("messages", m.id)!);
          }
        }
        this.changed();
        return { ok: true };
      }
      case "run_events": {
        const r = this.store.get("runs", p.id);
        if (!r) throw new AppError("不存在", 404);
        if (a.kind !== "owner") this.instance(r.instance_id, a);
        const events = existsSync(r.events_path)
          ? readFileSync(r.events_path, "utf8")
              .split("\n")
              .filter(Boolean)
              .map((s, line) => {
                try {
                  return JSON.parse(s);
                } catch {
                  return {
                    type: "warning",
                    message: "事件日志中的一行无法解析（可能因进程中断）",
                    line_number: line + 1,
                    fragment: s.slice(0, 4000),
                  };
                }
              })
          : [];
        return p.limit !== undefined ||
          p.offset !== undefined ||
          p.order !== undefined
          ? page(events, p)
          : events;
      }
      case "doorbell_control": {
        this.logical(p.logical_id, a, true);
        if (!["start", "stop", "restart"].includes(p.action))
          throw new AppError("action 无效");
        if (!this.onDoorbellChange)
          throw new AppError("doorbell supervisor 未启动");
        return this.onDoorbellChange(p.logical_id, p.action);
      }
      case "doorbell_logs": {
        this.logical(p.logical_id, a, true);
        const d = this.store.get("doorbells", p.logical_id);
        return {
          state: d,
          log:
            d && existsSync(d.log_path)
              ? readFileSync(d.log_path, "utf8").slice(-200000)
              : "",
        };
      }
      case "doorbell_script_get": {
        const l = this.logical(p.logical_id, a, true);
        return {
          script:
            l.doorbell_script && existsSync(l.doorbell_script)
              ? readFileSync(l.doorbell_script, "utf8")
              : "",
        };
      }
      case "doorbell_script_save": {
        const l = this.logical(p.logical_id, a, true);
        if (typeof p.script !== "string") throw new AppError("script 必填");
        const d = join(this.dir, "doorbells", l.id);
        mkdirSync(d, { recursive: true });
        const path = join(d, "script");
        writeFileSync(path, p.script, { mode: 0o700 });
        chmodSync(path, 0o700);
        this.store.update("logical_agents", l.id, {
          doorbell_script: path,
          doorbell_enabled: 1,
          updated_at: now(),
        });
        await this.onDoorbellChange?.(l.id, "restart");
        return { ok: true };
      }
      case "instance_diff":
      case "instance_merge": {
        this.owner(a);
        const i = this.instance(p.id, a);
        const w = i.config_snapshot.workdir;
        if (name === "instance_diff") return wd.diff(w, i);
        if (i.status === "running")
          throw new AppError("请先终止运行或等待空闲");
        return wd.merge(w, i, p);
      }
      case "workdir_refresh": {
        this.owner(a);
        const w = this.store.get("workdirs", p.id);
        if (!w) throw new AppError("不存在", 404);
        wd.refreshBase(w);
        return { ok: true };
      }
      case "branch_delete": {
        this.owner(a);
        const i = this.instance(p.id, a);
        if (!i.archived_at) throw new AppError("先归档实例才能删除分支");
        if (i.config_snapshot.workdir.mode !== "git")
          throw new AppError("没有 git 分支");
        wd.git(
          i.config_snapshot.workdir.main_path,
          "branch",
          p.force ? "-D" : "-d",
          i.branch,
        );
        return { ok: true };
      }
      case "ui_open":
        this.owner(a);
        return {
          url: `${this.uiUrl || this.url}/?token=${this.auth.ownerToken}`,
        };
      case "state": {
        this.owner(a);
        return {
          groups: this.store.all("groups"),
          agents: await this.call("agent_list", {}, a),
          messages: this.store
            .all("messages")
            .map((m) => this.observer.message(m, a)),
          doorbells: this.store.all("doorbells"),
          pid: process.pid,
        };
      }
      default:
        throw new AppError("未知工具", 404);
    }
  }
  insert(table: string, row: Row) {
    this.store.insert(table, row);
    this.changed();
    return row;
  }
  private validateConfig(p: Row) {
    if (!["worker", "admin"].includes(p.identity))
      throw new AppError("identity 必须是 worker 或 admin");
    if (!["fork", "resident"].includes(p.run_mode))
      throw new AppError("run_mode 无效");
    if (!this.harnesses[p.harness]) throw new AppError("不支持的 harness");
    if (!p.role_name?.trim()) throw new AppError("role_name 必填");
    for (const key of ["extra_env", "harness_options"])
      if (!p[key] || typeof p[key] !== "object" || Array.isArray(p[key]))
        throw new AppError(`${key} 必须是对象`);
    for (const v of Object.values(p.extra_env))
      if (typeof v !== "string") throw new AppError("环境变量值必须是字符串");
    for (const k of ["system_prompt_tpl", "prompt_tpl"])
      if (typeof p[k] !== "string") throw new AppError(`${k} 必须是字符串`);
  }
  private createAgent(p: Row, a: Actor) {
    const c = this.context(a);
    if (
      a.kind !== "owner" &&
      (c.identity !== "admin" || p.identity === "admin")
    )
      throw new AppError("无权限", 403);
    const group = p.group || c.group_id;
    if (a.kind !== "owner" && group !== c.group_id)
      throw new AppError("不存在", 404);
    if (!this.store.get("groups", group)) throw new AppError("不存在", 404);
    if (this.deletingGroups.has(group))
      throw new AppError("Group 正在删除", 409);
    const cfg: Row = {
      identity: "worker",
      harness: "codex",
      model: null,
      harness_options: {},
      extra_env: {},
      system_prompt_tpl: "",
      prompt_tpl: "",
      run_mode: "fork",
      ...p,
    };
    this.validateConfig(cfg);
    if (
      this.store
        .all("logical_agents")
        .some(
          (l) =>
            l.group_id === group &&
            !l.deleted_at &&
            l.role_name === cfg.role_name,
        )
    )
      throw new AppError("角色名已存在");
    if (typeof p.workdir !== "string" || !isAbsolute(p.workdir))
      throw new AppError("workdir 必须是绝对路径");
    const root = wd.canonical(p.workdir);
    let w = this.store.all("workdirs").find((w) => w.root === root);
    if (w && (p.worktree === false) !== (w.mode === "direct"))
      throw new AppError("同一目录不能混用 direct 与 worktree 模式");
    if (!w) {
      w = wd.setupWorkdir(p);
      this.store.insert("workdirs", w);
    }
    installAgentSkill(
      this.skillDir,
      w.mode === "direct" ? w.root : w.main_path,
      cfg.harness,
    );
    if (w.mode === "plain")
      installAgentSkill(this.skillDir, w.base_path, cfg.harness);
    const id = short(6);
    const l = {
      id,
      group_id: group,
      role_name: cfg.role_name,
      identity: cfg.identity,
      harness: cfg.harness,
      model: cfg.model,
      harness_options: cfg.harness_options,
      extra_env: cfg.extra_env,
      system_prompt_tpl: cfg.system_prompt_tpl,
      prompt_tpl: cfg.prompt_tpl,
      run_mode: cfg.run_mode,
      resident_instance_id: null,
      workdir_id: w.id,
      doorbell_script: p.doorbell_script || null,
      doorbell_enabled: !!p.doorbell_script && p.doorbell_enabled !== false,
      created_at: now(),
      updated_at: now(),
      deleted_at: null,
    };
    this.insert("logical_agents", l);
    if (l.doorbell_enabled) void this.onDoorbellChange?.(id, "start");
    return { ...l, workdir: w, execution: this.observer.execution(l, a) };
  }
  private async updateAgent(p: Row, a: Actor) {
    const l = this.logical(p.id, a, true);
    if (a.kind !== "owner" && p.identity && p.identity !== "worker")
      throw new AppError("无权限", 403);
    const keys = [
      "role_name",
      "identity",
      "harness",
      "model",
      "harness_options",
      "extra_env",
      "system_prompt_tpl",
      "prompt_tpl",
      "run_mode",
      "doorbell_script",
      "doorbell_enabled",
    ];
    const changes = Object.fromEntries(
      keys.filter((k) => k in p).map((k) => [k, p[k]]),
    );
    this.validateConfig({ ...l, ...changes });
    if (
      p.role_name &&
      this.store
        .all("logical_agents")
        .some(
          (x) =>
            x.id !== l.id &&
            !x.deleted_at &&
            x.group_id === l.group_id &&
            x.role_name === p.role_name,
        )
    )
      throw new AppError("角色名已存在");
    if (p.workdir) {
      const root = wd.canonical(p.workdir);
      let w = this.store.all("workdirs").find((w) => w.root === root);
      if (w && (p.worktree === false) !== (w.mode === "direct"))
        throw new AppError("目录模式冲突");
      if (!w) {
        w = wd.setupWorkdir(p);
        this.store.insert("workdirs", w);
      }
      changes.workdir_id = w.id;
    }
    const workdir = this.store.get(
      "workdirs",
      changes.workdir_id || l.workdir_id,
    )!;
    installAgentSkill(
      this.skillDir,
      workdir.mode === "direct" ? workdir.root : workdir.main_path,
      changes.harness || l.harness,
    );
    if (workdir.mode === "plain")
      installAgentSkill(
        this.skillDir,
        workdir.base_path,
        changes.harness || l.harness,
      );
    this.store.update("logical_agents", l.id, {
      ...changes,
      updated_at: now(),
    });
    if ("doorbell_script" in changes || "doorbell_enabled" in changes)
      await this.onDoorbellChange?.(
        l.id,
        changes.doorbell_enabled === false ? "stop" : "restart",
      );
    this.changed();
    return this.call("agent_get", { id: l.id }, a);
  }
  private async deleteAgent(id: string, a: Actor) {
    const l = this.logical(id, a, true);
    this.store.update("logical_agents", id, {
      deleted_at: now(),
      doorbell_enabled: 0,
    });
    for (const m of this.store
      .all("messages")
      .filter(
        (m) =>
          m.status === "queued" &&
          (m.target_id === id || m.target_id.startsWith(id + "-")),
      ))
      this.store.update("messages", m.id, {
        status: "cancelled",
        error: "逻辑 Agent 已删除",
      });
    await this.onDoorbellChange?.(id, "stop");
    for (const i of this.store
      .all("instances")
      .filter((i) => i.logical_id === id && !i.archived_at))
      await this.archiveInternal(i);
    this.changed();
  }
  async archive(id: string, a: Actor) {
    await this.archiveInternal(this.instance(id, a, true));
  }
  private async archiveInternal(i: Row) {
    if (i.archived_at) return;
    this.store.update("instances", i.id, {
      status: "archived",
      archived_at: now(),
    });
    for (const m of this.store
      .all("messages")
      .filter((m) => m.status === "queued" && m.target_id === i.id))
      this.store.update("messages", m.id, {
        status: "cancelled",
        error: "实例已归档",
      });
    await this.stop(i.id);
    wd.cleanup(i.config_snapshot.workdir, i);
    const l = this.store.get("logical_agents", i.logical_id);
    if (l && l.resident_instance_id === i.id)
      this.store.update("logical_agents", l.id, { resident_instance_id: null });
    this.changed();
  }
  async stop(id: string) {
    const active = this.active.get(id);
    if (!active) return;
    if (!this.stopRequests.has(active.run))
      this.stopRequests.set(active.run, "stopped");
    await Promise.resolve();
    if (active.handle) await active.harness.stop(active.handle);
    await active.task;
  }
  tick() {
    if (this.stopping) return;
    for (const m of this.queuedMessages()) {
      try {
        this.dispatch(m);
      } catch (e) {
        this.store.update("messages", m.id, {
          status: "failed",
          error: String(e),
        });
        this.changed();
      }
    }
  }
  private route(m: Row) {
    const { logical: l, instance: direct } = this.resolve(m.target_id, OWNER);
    let i = direct;
    if (
      !i &&
      !m.force_fork &&
      l.run_mode === "resident" &&
      l.resident_instance_id
    ) {
      const found = this.store.get("instances", l.resident_instance_id);
      if (found && !found.archived_at) i = found;
    }
    if (i?.archived_at) throw new AppError("实例已归档");
    const w = i
      ? i.config_snapshot.workdir
      : this.store.get("workdirs", l.workdir_id)!;
    return { l, i, w };
  }
  scheduling(m: Row): Row {
    const { l, i, w } = this.route(m);
    const result: Row = {
      next_action: i ? "resume" : "fork",
      instance_id: i?.id || null,
      workdir: w.root,
      workdir_mode: w.mode,
      queue_reason: null,
    };
    if (this.store.get("groups", l.group_id)?.paused)
      return {
        ...result,
        queue_reason: {
          code: "group_paused",
          description: "Group 调度已暂停；消息保留在队列",
          group_id: l.group_id,
        },
      };
    // Renewing a resident must finish stopping the old session before its replacement runs.
    const retiring =
      !i &&
      !m.force_fork &&
      l.run_mode === "resident" &&
      l.resident_instance_id &&
      this.active.get(l.resident_instance_id);
    if (retiring)
      return {
        ...result,
        queue_reason: {
          code: "instance_busy",
          description: "等待旧常驻实例结束并完成归档",
          blocking_instance_id: l.resident_instance_id,
          blocking_run_id: retiring.run,
        },
      };
    const active = i && this.active.get(i.id);
    if (active)
      return {
        ...result,
        queue_reason: {
          code: "instance_busy",
          description:
            "该实例正在执行上一回合；当前消息按 FIFO 等待，不会实时送入运行中的会话",
          blocking_instance_id: i.id,
          blocking_run_id: active.run,
        },
      };
    if (w.mode === "direct")
      for (const [id, run] of this.active) {
        if (
          this.store.get("instances", id)?.config_snapshot.workdir.root ===
          w.root
        )
          return {
            ...result,
            queue_reason: {
              code: "workdir_busy",
              description:
                "同一 direct 工作目录全局串行；等待当前回合结束并释放目录",
              workdir: w.root,
              blocking_instance_id: id,
              blocking_run_id: run.run,
            },
          };
      }
    for (const earlier of this.queuedMessages()) {
      if (earlier.id === m.id) break;
      if (earlier.status !== "queued") continue;
      try {
        const r = this.route(earlier);
        if (this.store.get("groups", r.l.group_id)?.paused) continue;
        const sameInstance = i && r.i?.id === i.id;
        const sameResident =
          !i &&
          !r.i &&
          l.id === r.l.id &&
          l.run_mode === "resident" &&
          !m.force_fork &&
          !earlier.force_fork;
        const sameWorkdir = w.mode === "direct" && r.w.root === w.root;
        if (sameInstance || sameResident || sameWorkdir)
          return {
            ...result,
            queue_reason: {
              code: "fifo",
              description: "等待同一执行队列中更早的消息被调度",
              ahead_message_id: earlier.id,
              workdir: sameWorkdir ? w.root : undefined,
            },
          };
      } catch {} // Invalid earlier targets will fail when the scheduler visits them.
    }
    return {
      ...result,
      queue_reason: {
        code: "awaiting_dispatch",
        description:
          "消息已入队，等待调度器启动下一回合；入队成功不表示已经运行",
      },
    };
  }
  private dispatch(m: Row) {
    const current = this.store.get("messages", m.id);
    if (!current || current.status !== "queued") return;
    m = current;
    const decision = this.scheduling(m);
    if (decision.queue_reason.code !== "awaiting_dispatch") return;
    const { l, i: instance, w } = this.route(m);
    let i = instance;
    const fresh = !i,
      config = fresh ? l : i!.config_snapshot;
    const seq = fresh
      ? Math.max(
          0,
          ...this.store
            .all("instances")
            .filter((i) => i.logical_id === l.id)
            .map((i) => i.seq),
        ) + 1
      : i!.seq;
    const id = i?.id || `${l.id}-${seq}`,
      path =
        i?.worktree_path ||
        (w.mode === "direct" ? w.root : join(w.root, "worktrees", id));
    const vars = {
      ...m.vars,
      agent_id: id,
      role: config.role_name,
      group: l.group_id,
      from: m.from_id,
      from_role: senderInfo(m).label,
      worktree: path,
      shared_dir: w.shared_path || "",
      now: now(),
    };
    // Render before allocating an instance or worktree.
    const prompt = m.prompt ?? render(config.prompt_tpl, vars);
    const system = fresh
      ? (m.system_prompt ?? render(config.system_prompt_tpl, vars)) +
        (w.shared_path ? `\n共享目录: ${w.shared_path}` : "")
      : "";
    const sender = senderInfo(m);
    const adminHint =
      l.identity === "admin"
        ? `\n\n需要查询或管理同组 Agent 时，参考 hamlet-agent skill：${join(agentSkillPath(path, config.harness), "SKILL.md")}。`
        : "";
    const actual = `[来自 ${sender.label}${sender.kind === "owner" ? " (owner)" : sender.kind === "doorbell" ? "" : ` (${m.from_id})`}]\n${prompt}${adminHint}`;
    if (fresh) {
      i = {
        id,
        logical_id: l.id,
        seq,
        harness_session_id: null,
        worktree_path: path,
        branch: w.mode === "git" ? `agent/${id}` : null,
        status: "preparing",
        config_snapshot: {
          ...l,
          workdir: { ...w },
          prompt: actual,
          system_prompt: system,
        },
        created_at: now(),
        last_active_at: now(),
        archived_at: null,
      };
      this.store.insert("instances", i!);
      this.store.update("messages", m.id, {
        status: "dispatched",
        resolved_instance_id: id,
      });
      if (l.run_mode === "resident" && !m.force_fork)
        this.store.update("logical_agents", l.id, { resident_instance_id: id });
      try {
        wd.prepare(w, id);
        if (w.mode === "plain") {
          i!.config_snapshot.workdir.base_path = join(
            workdirMetadataDir(w.root),
            "bases",
            id,
          );
          this.store.update("instances", id, {
            config_snapshot: i!.config_snapshot,
          });
        }
      } catch (e) {
        this.store.update("instances", id, {
          status: "archived",
          archived_at: now(),
        });
        if (l.run_mode === "resident" && !m.force_fork)
          this.store.update("logical_agents", l.id, {
            resident_instance_id: null,
          });
        throw e;
      }
    }
    const run = {
      id: randomUUID(),
      instance_id: id,
      message_id: m.id,
      kind: fresh ? "fork" : "resume",
      status: "running",
      started_at: now(),
      events_path: "",
      prompt: actual,
      system_prompt: system,
      usage: {},
      trigger_snapshot: triggerSnapshot(m),
      queue_wait_ms: Math.max(
        0,
        Date.now() - Date.parse(m.queued_at || m.created_at),
      ),
    };
    run.events_path = join(this.dir, "runs", run.id + ".jsonl");
    this.store.db.transaction(() => {
      this.store.insert("runs", run);
      this.store.update("messages", m.id, {
        status: "dispatched",
        resolved_instance_id: id,
        run_id: run.id,
      });
      this.store.update("instances", id, {
        status: "running",
        last_active_at: now(),
      });
    })();
    const emit = (e: Row) => {
      const event = { timestamp: now(), ...e };
      appendFileSync(run.events_path, JSON.stringify(event) + "\n");
      this.emit("event", { run_id: run.id, ...event });
    };
    if (!fresh && m.system_prompt !== null)
      emit({
        type: "warning",
        message: "resume 无法更换 system prompt，已忽略",
      });
    if (
      fresh &&
      w.mode === "git" &&
      wd.git(w.main_path, "status", "--porcelain")
    )
      emit({
        type: "warning",
        message: "main 有未提交改动，本次从 HEAD 创建 worktree",
      });
    const task = this.execute(i!, run, m, fresh, system, actual, emit); // execute yields before starting the harness.
    this.active.set(id, {
      handle: null as any,
      harness: this.harnesses[config.harness],
      run: run.id,
      task,
    });
    this.changed();
  }
  private async execute(
    i: Row,
    run: Row,
    m: Row,
    fresh: boolean,
    system: string,
    prompt: string,
    emit: (e: Row) => void,
  ) {
    await Promise.resolve();
    const config = i.config_snapshot,
      w = config.workdir,
      h = this.harnesses[config.harness];
    try {
      if (
        this.stopRequests.has(run.id) ||
        ["stopped", "interrupted"].includes(
          this.store.get("runs", run.id)?.status,
        )
      )
        throw new AppError("运行已终止");
      if (!fresh && !i.harness_session_id)
        throw new AppError("harness 会话不可用，请换新实例");
      const localSkill = installAgentSkill(
        this.skillDir,
        i.worktree_path,
        config.harness,
      );
      const token = this.auth.sign({ kind: "instance", id: i.id }),
        innerEnv = instanceEnvironment(this.url, token, i.id);
      const opts = {
        cwd: i.worktree_path,
        systemPrompt: system,
        prompt,
        model: config.model,
        options: config.harness_options,
        env: {
          ...childEnvironment(config.extra_env, {}),
          ...innerEnv,
          HAMLET_CLI: this.cliPath,
          HAMLET_AGENT_TOOLS: join(
            dirname(this.cliPath),
            "hamlet-agent-tools.js",
          ),
          HAMLET_NODE: process.execPath,
          HAMLET_SKILL_DIR: localSkill,
          HAMLET_RUN_ID: run.id,
          DETACHED_RUN_ID: run.id,
        },
      };
      const handle = fresh
        ? h.start(opts)
        : h.resume({ ...opts, sessionId: i.harness_session_id });
      const active = this.active.get(i.id)!;
      active.handle = handle;
      this.store.update("runs", run.id, { pid: handle.pid || null });
      let sessionError = "";
      const session = handle.sessionId
        .then((s) =>
          this.store.update("instances", i.id, { harness_session_id: s }),
        )
        .catch((e) => {
          sessionError = String(e);
          emit({ type: "error", message: sessionError });
        });
      const raw = handle.rawEvents
        ? (async () => {
            for await (const e of handle.rawEvents!)
              appendFileSync(
                run.events_path.replace(".jsonl", ".raw.jsonl"),
                JSON.stringify(e) + "\n",
              );
          })()
        : Promise.resolve();
      for await (const e of handle.events) emit(e);
      const result = await handle.done;
      await session;
      await raw;
      let error = result.error || sessionError;
      try {
        wd.commit(w, i, run.id);
      } catch (e) {
        error = `自动提交失败: ${String(e)}`;
        emit({ type: "error", message: error });
      }
      const finalStatus =
        this.stopRequests.get(run.id) || this.store.get("runs", run.id)?.status;
      const stopped =
        finalStatus === "stopped" || finalStatus === "interrupted";
      const success = !stopped && result.exitCode === 0 && !error;
      this.store.db.transaction(() => {
        this.store.update("runs", run.id, {
          status: stopped ? finalStatus : success ? "succeeded" : "failed",
          ended_at: now(),
          final_output: result.finalOutput,
          usage: result.usage,
          error: error || null,
          pid: null,
        });
        this.store.update("messages", m.id, {
          status: success ? "done" : "failed",
          error: success
            ? null
            : error ||
              (stopped ? "运行已终止" : `harness 退出码 ${result.exitCode}`),
        });
      })();
    } catch (e) {
      emit({ type: "error", message: String(e) });
      this.store.db.transaction(() => {
        this.store.update("runs", run.id, {
          status:
            this.stopRequests.get(run.id) ||
            (["stopped", "interrupted"].includes(
              this.store.get("runs", run.id)?.status,
            )
              ? this.store.get("runs", run.id)!.status
              : "failed"),
          ended_at: now(),
          error: String(e),
          pid: null,
        });
        this.store.update("messages", m.id, {
          status: "failed",
          error: String(e),
        });
      })();
    } finally {
      this.active.delete(i.id);
      this.stopRequests.delete(run.id);
      this.releaseUnstartedResident(i.id);
      if (!this.store.get("instances", i.id)?.archived_at)
        this.store.update("instances", i.id, {
          status: "idle",
          last_active_at: now(),
        });
      this.changed();
    }
  }
  private releaseUnstartedResident(id: string) {
    const instance = this.store.get("instances", id);
    const logical =
      instance && this.store.get("logical_agents", instance.logical_id);
    if (
      instance &&
      !instance.harness_session_id &&
      logical?.resident_instance_id === id
    )
      this.store.update("logical_agents", logical.id, {
        resident_instance_id: null,
      });
  }
  async close() {
    this.stopping = true;
    clearInterval(this.timer);
    await Promise.all(
      [...this.active.entries()].map(async ([id, v]) => {
        this.stopRequests.set(v.run, "interrupted");
        await Promise.resolve();
        if (v.handle) await v.harness.stop(v.handle);
        await v.task;
      }),
    );
    this.store.close();
  }
}
export function duration(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0)
    return value * 1000;
  const m = String(value).match(/^(\d+(?:\.\d+)?)(s|m|h|d)$/);
  if (!m) throw new AppError("无效去重窗口，示例 24h / 7d");
  return Number(m[1]) * { s: 1000, m: 60000, h: 3600000, d: 86400000 }[m[2]]!;
}
