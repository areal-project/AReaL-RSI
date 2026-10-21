import type { Service } from "./service.js";
import { AppError, type Actor, type Row } from "./types.js";

export function senderInfo(message: Row) {
  const source = message.source;
  if (message.from_id === "owner")
    return {
      kind: "owner",
      id: "owner",
      source,
      label:
        source === "ui"
          ? "Web UI"
          : source === "ring"
            ? "ring 调用方"
            : "宿主调用方",
      identity: "owner",
    };
  if (source === "doorbell_script")
    return {
      kind: "doorbell",
      id: message.from_id,
      source,
      label: message.from_role,
      identity: "doorbell",
    };
  return {
    kind: "instance",
    id: message.from_id,
    source,
    label: message.from_role,
    identity: "instance",
  };
}
export function triggerSnapshot(m: Row) {
  return Object.fromEntries(
    [
      "id",
      "target_id",
      "from_id",
      "from_role",
      "source",
      "prompt",
      "system_prompt",
      "vars",
      "dedup_key",
      "force_fork",
      "created_at",
      "queued_at",
    ].map((k) => [k, m[k] ?? null]),
  );
}
export function page(rows: Row[], p: Row, defaultLimit = 100) {
  const limit = p.limit ?? defaultLimit,
    offset = p.offset ?? 0;
  if (!Number.isInteger(limit) || limit < 1 || limit > 500)
    throw new AppError("limit 必须是 1–500 的整数");
  if (!Number.isInteger(offset) || offset < 0)
    throw new AppError("offset 必须是非负整数");
  if (p.order !== undefined && !["asc", "desc"].includes(p.order))
    throw new AppError("order 必须是 asc 或 desc");
  return (p.order === "desc" ? [...rows].reverse() : rows).slice(
    offset,
    offset + limit,
  );
}
function matchesTime(value: string, p: Row) {
  for (const k of ["since", "until"])
    if (
      p[k] !== undefined &&
      (typeof p[k] !== "string" || !Number.isFinite(Date.parse(p[k])))
    )
      throw new AppError(`${k} 必须是有效的时间字符串`);
  return (
    (!p.since || Date.parse(value) >= Date.parse(p.since)) &&
    (!p.until || Date.parse(value) <= Date.parse(p.until))
  );
}
export class Observer {
  constructor(private service: Service) {}
  private get store() {
    return this.service.store;
  }
  logicalForMessage(m: Row) {
    const i = this.store.get(
      "instances",
      m.resolved_instance_id || m.target_id,
    );
    return this.store.get("logical_agents", i?.logical_id || m.target_id);
  }
  private visible(l: Row | undefined, a: Actor) {
    return (
      a.kind === "owner" ||
      (!!l && l.group_id === this.service.context(a).group_id)
    );
  }
  execution(l: Row, a: Actor, w = this.store.get("workdirs", l.workdir_id)!) {
    const direct = w.mode === "direct";
    return {
      workdir: w.root,
      workdir_mode: w.mode,
      concurrency: direct ? "serial_workdir" : "parallel_instances",
      message_delivery: "queued_turn",
      description: direct
        ? "此 direct 目录由所有使用它的 Agent 全局串行执行；本轮结束才会释放目录。message_to 只入队，不会打断正在运行的回合；不要在本轮等待共用此目录的 Agent 回复。"
        : "不同实例可并行；同一实例按 FIFO 逐条执行。每条 message_to 都会触发一次运行，不会实时插入正在执行的回合。",
      shared_with: direct
        ? this.store
            .all("logical_agents")
            .filter(
              (x) =>
                !x.deleted_at &&
                x.id !== l.id &&
                this.store.get("workdirs", x.workdir_id)?.root === w.root &&
                this.visible(x, a),
            )
            .map((x) => ({
              logical_id: x.id,
              role_name: x.role_name,
              group_id: x.group_id,
            }))
        : [],
    };
  }
  message(m: Row, a: Actor) {
    if (!this.visible(this.logicalForMessage(m), a))
      throw new AppError("不存在", 404);
    let scheduling: Row | null = null;
    if (m.status === "queued") {
      try {
        scheduling = this.service.scheduling(m);
      } catch (e) {
        scheduling = {
          next_action: null,
          instance_id: null,
          queue_reason: { code: "target_unavailable", description: String(e) },
        };
      }
      // Group-scoped readers must not learn IDs of a different group's blockers.
      if (a.kind !== "owner" && scheduling.queue_reason?.blocking_instance_id) {
        const i = this.store.get(
            "instances",
            scheduling.queue_reason.blocking_instance_id,
          ),
          l = i && this.store.get("logical_agents", i.logical_id);
        if (!this.visible(l, a))
          scheduling = {
            ...scheduling,
            queue_reason: {
              code: scheduling.queue_reason.code,
              description: "该工作目录被其他运行占用",
              workdir: scheduling.queue_reason.workdir,
            },
          };
      }
    }
    if (a.kind !== "owner" && scheduling?.queue_reason?.ahead_message_id) {
      const earlier = this.store.get(
        "messages",
        scheduling.queue_reason.ahead_message_id,
      );
      if (earlier && !this.visible(this.logicalForMessage(earlier), a))
        scheduling = {
          ...scheduling,
          queue_reason: {
            code: "fifo",
            description: "等待该工作目录中更早的消息被调度",
            workdir: scheduling.queue_reason.workdir,
          },
        };
    }
    const queuedAt = m.queued_at || m.created_at;
    const run = m.run_id ? this.store.get("runs", m.run_id) : null;
    return {
      ...m,
      group_id: this.logicalForMessage(m)?.group_id || null,
      logical_id: this.logicalForMessage(m)?.id || null,
      sender: senderInfo(m),
      queue_reason: scheduling?.queue_reason || null,
      next_action: scheduling?.next_action || null,
      planned_instance_id: scheduling?.instance_id || null,
      queued_for_ms:
        m.status === "queued"
          ? Math.max(0, Date.now() - Date.parse(queuedAt))
          : null,
      queue_wait_ms: run?.queue_wait_ms ?? null,
    };
  }
  messages(p: Row, a: Actor) {
    this.service.context(a);
    matchesTime(new Date().toISOString(), p);
    if (
      p.status !== undefined &&
      !["queued", "dispatched", "done", "failed", "cancelled"].includes(
        p.status,
      )
    )
      throw new AppError("消息 status 无效");
    const rows = this.store.all("messages").filter((m) => {
      const l = this.logicalForMessage(m);
      return (
        this.visible(l, a) &&
        (!p.group || l?.group_id === p.group) &&
        (!p.logical_id || l?.id === p.logical_id) &&
        (!p.instance_id ||
          m.resolved_instance_id === p.instance_id ||
          m.target_id === p.instance_id ||
          this.store
            .all("runs")
            .some(
              (r) => r.message_id === m.id && r.instance_id === p.instance_id,
            )) &&
        (!p.target || m.target_id === p.target) &&
        (!p.from_id || m.from_id === p.from_id) &&
        (!p.source || m.source === p.source) &&
        (!p.status || m.status === p.status) &&
        matchesTime(m.created_at, p)
      );
    });
    // Preserve unpaged HTTP queries; callers can explicitly request a bounded page.
    const selected =
      p.limit === undefined && p.offset === undefined && p.order === undefined
        ? rows
        : page(rows, p);
    return selected.map((m) => this.message(m, a));
  }
  messageGet(id: string, a: Actor) {
    const m = this.store.get("messages", id);
    if (!m) throw new AppError("不存在", 404);
    return {
      ...this.message(m, a),
      runs: this.store
        .all("runs")
        .filter((r) => r.message_id === id)
        .map((r) => this.run(r, a)),
    };
  }
  run(r: Row, a: Actor) {
    const i = this.store.get("instances", r.instance_id),
      l = i && this.store.get("logical_agents", i.logical_id);
    if (!this.visible(l, a)) throw new AppError("不存在", 404);
    const saved =
      r.trigger_snapshot && Object.keys(r.trigger_snapshot).length > 0;
    const m = this.store.get("messages", r.message_id);
    const trigger = saved ? r.trigger_snapshot : m ? triggerSnapshot(m) : null;
    return {
      ...r,
      logical_id: l?.id || null,
      group_id: l?.group_id || null,
      trigger: trigger ? { ...trigger, sender: senderInfo(trigger) } : null,
      trigger_is_snapshot: !!saved,
      duration_ms: Math.max(
        0,
        Date.parse(r.ended_at || new Date().toISOString()) -
          Date.parse(r.started_at),
      ),
      queue_wait_ms: r.queue_wait_ms ?? null,
    };
  }
  runs(p: Row, a: Actor) {
    this.service.owner(a);
    matchesTime(new Date().toISOString(), p);
    if (
      p.status !== undefined &&
      !["running", "succeeded", "failed", "stopped", "interrupted"].includes(
        p.status,
      )
    )
      throw new AppError("运行 status 无效");
    const rows = this.store.all("runs").filter((r) => {
      const i = this.store.get("instances", r.instance_id),
        l = i && this.store.get("logical_agents", i.logical_id),
        m =
          r.trigger_snapshot && Object.keys(r.trigger_snapshot).length
            ? r.trigger_snapshot
            : this.store.get("messages", r.message_id);
      return (
        (!p.group || l?.group_id === p.group) &&
        (!p.logical_id || l?.id === p.logical_id) &&
        (!p.instance_id || i?.id === p.instance_id) &&
        (!p.message_id || r.message_id === p.message_id) &&
        (!p.source || m?.source === p.source) &&
        (!p.from_id || m?.from_id === p.from_id) &&
        (!p.status || r.status === p.status) &&
        matchesTime(r.started_at, p)
      );
    });
    return page(rows, p).map((r) => this.run(r, a));
  }
  runGet(id: string, a: Actor) {
    this.service.owner(a);
    const r = this.store.get("runs", id);
    if (!r) throw new AppError("不存在", 404);
    return this.run(r, a);
  }
}
