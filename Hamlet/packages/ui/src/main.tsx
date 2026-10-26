import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
type R = Record<string, any>;
async function api(name: string, params: R = {}) {
  const r = await fetch("/api/tools/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const d = await r.json();
  if (!r.ok) {
    const e = new Error(d.error) as any;
    e.details = d.details;
    throw e;
  }
  return d;
}
const pretty = (x: any) => JSON.stringify(x, null, 2),
  stamp = (x: any) => (x ? new Date(x).toLocaleString() : "—");
async function saveAgent(name: string, params: R) {
  try {
    return await api(name, params);
  } catch (e: any) {
    if (
      e.details?.needs_migration &&
      confirm(`目录需要迁移到 main：\n${pretty(e.details)}\n确认迁移？`)
    )
      return api(name, { ...params, migrate: true });
    throw e;
  }
}
function Badge({ status }: { status: string }) {
  return (
    <span className="badge">
      <i className={"dot " + status} />
      {status}
    </span>
  );
}
function Json({ value }: { value: any }) {
  return <pre>{pretty(value)}</pre>;
}
function App() {
  const [data, setData] = useState<R>({
      groups: [],
      agents: [],
      messages: [],
      doorbells: [],
    }),
    [selection, setSelection] = useState({ kind: "overview", id: "" }),
    [tab, setTab] = useState("概况"),
    [search, setSearch] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [modal, setModal] = useState<null | "agent" | "group">(null),
    [detail, setDetail] = useState<R | null>(null),
    [events, setEvents] = useState<R[]>([]),
    [diff, setDiff] = useState<R | null>(null),
    [logs, setLogs] = useState<R>({}),
    [script, setScript] = useState(""),
    [scriptLoading, setScriptLoading] = useState(false),
    [filter, setFilter] = useState({ group: "", target: "", source: "" }),
    [follow, setFollow] = useState(true),
    [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const scriptEdited = useRef(false);
  const reload = useCallback(async () => setData(await api("state")), []);
  const act = useCallback(
    async (fn: () => Promise<any>, message = "已完成") => {
      setError("");
      try {
        const result = await fn();
        if (message) setNotice(message);
        await reload();
        return result;
      } catch (e: any) {
        setError(e.message + (e.details ? "\n" + pretty(e.details) : ""));
        return undefined;
      }
    },
    [reload],
  );
  const choose = (kind: string, id = "") => {
    setSelection({ kind, id });
    setDetail(null);
    setDiff(null);
    setEvents([]);
    setTab(kind === "agent" ? "配置" : "概况");
    setSelectedFiles([]);
  };
  useEffect(() => {
    void reload().catch((e) => setError(e.message));
    const s = new EventSource("/api/events");
    s.addEventListener("change", () => void reload().catch(() => {}));
    s.addEventListener("agent", (e) => {
      const v = JSON.parse((e as MessageEvent).data);
      setEvents((old) => [...old, v].slice(-3000));
    });
    s.onerror = () => setNotice("连接中断，正在重连…");
    s.onopen = () => setNotice("");
    return () => s.close();
  }, [reload]);
  const agent = data.agents.find((a: R) => a.id === selection.id),
    instance = data.agents
      .flatMap((a: R) => a.instances)
      .find((i: R) => i.id === selection.id);
  const loadDetail = useCallback(async () => {
    if (selection.kind === "instance") {
      const d = await api("instance_get", { id: selection.id });
      setDetail(d);
      const r = d.runs.at(-1);
      if (r) setEvents(await api("run_events", { id: r.id }));
      if (tab === "Diff")
        setDiff(await api("instance_diff", { id: selection.id }));
    }
    if (selection.kind === "agent" && tab === "Doorbell") {
      setLogs(await api("doorbell_logs", { logical_id: selection.id }));
    }
  }, [selection, tab]);
  useEffect(() => {
    void loadDetail().catch((e) => setError(e.message));
  }, [loadDetail, data]);
  useEffect(() => {
    let cancelled = false;
    if (selection.kind === "agent" && tab === "Doorbell") {
      scriptEdited.current = false;
      setScriptLoading(true);
      void api("doorbell_script_get", { logical_id: selection.id })
        .then((x) => {
          if (!cancelled && !scriptEdited.current) setScript(x.script);
        })
        .catch((e) => {
          if (!cancelled) setError(e.message);
        })
        .finally(() => {
          if (!cancelled) setScriptLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [selection, tab]);
  useEffect(() => {
    if (follow && tab === "实时日志")
      bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, follow, tab]);
  useEffect(() => {
    if (tab !== "Doorbell") return;
    const timer = setInterval(() => void loadDetail().catch(() => {}), 1500);
    return () => clearInterval(timer);
  }, [tab, loadDetail]);
  const send = (target: string) => {
    const prompt = window.prompt("发送消息（留空则使用模板）");
    if (prompt !== null)
      void act(
        () => api("message_to", { target, ...(prompt ? { prompt } : {}) }),
        "",
      ).then((m) => {
        if (m)
          setNotice(`消息已入队：${m.queue_reason?.description || m.status}`);
      });
  };
  const archive = (i: R) =>
    void act(async () => {
      let changed = false;
      try {
        const d = await api("instance_diff", { id: i.id });
        changed = d.files.length > 0 || d.uncommitted;
      } catch {}
      if (
        !confirm(
          changed
            ? "实例有未合并改动。归档将清理 worktree，确定继续？"
            : "确定归档该实例？",
        )
      )
        return;
      await api("instance_archive", { id: i.id });
    }, "归档操作已完成");
  const createGroup = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const name = new FormData(e.currentTarget).get("name");
    if (await act(() => api("group_create", { name }))) setModal(null);
  };
  const queue = data.messages.filter(
    (m: R) =>
      (!filter.target || m.target_id.includes(filter.target)) &&
      (!filter.source || m.source === filter.source) &&
      (!filter.group ||
        data.agents.some(
          (a: R) =>
            a.group_id === filter.group &&
            (m.target_id === a.id || m.target_id.startsWith(a.id + "-")),
        )),
  );
  return (
    <div className="shell">
      <aside>
        <a className="brand" onClick={() => choose("overview")}>
          <span className="brand-icon">H</span>
          <span>
            Hamlet<small>坊 · AGENT VILLAGE</small>
          </span>
        </a>
        <div className="sidebar-tools">
          <input
            aria-label="搜索"
            placeholder="搜索角色或 ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <nav>
          <button
            className={selection.kind === "overview" ? "selected" : ""}
            onClick={() => choose("overview")}
          >
            ◫　总览
          </button>
          <button
            className={selection.kind === "queue" ? "selected" : ""}
            onClick={() => choose("queue")}
          >
            ☷　消息队列{" "}
            <span>
              {data.messages.filter((m: R) => m.status === "queued").length}
            </span>
          </button>
          <button
            className={selection.kind === "bells" ? "selected" : ""}
            onClick={() => choose("bells")}
          >
            ♧　Doorbell 监控
          </button>
        </nav>
        <div className="side-label">
          坊 · GROUPS{" "}
          <button aria-label="新建坊" onClick={() => setModal("group")}>
            ＋
          </button>
        </div>
        <div className="tree">
          {data.groups.map((g: R) => (
            <div key={g.id}>
              <button className="group" onClick={() => choose("group", g.id)}>
                ⌄　{g.name}
                {g.paused ? "　⏸" : ""}
              </button>
              {data.agents
                .filter(
                  (a: R) =>
                    a.group_id === g.id &&
                    (!search ||
                      `${a.role_name} ${a.id} ${pretty(a.instances)}`.includes(
                        search,
                      )),
                )
                .map((a: R) => (
                  <div key={a.id}>
                    <button
                      className={
                        "logical " + (selection.id === a.id ? "selected" : "")
                      }
                      onClick={() => choose("agent", a.id)}
                    >
                      <span>◇　{a.role_name}</span>
                      <small>{a.identity}</small>
                    </button>
                    {a.instances.map((i: R) => (
                      <button
                        className={
                          "instance " +
                          (selection.id === i.id ? "selected" : "")
                        }
                        key={i.id}
                        onClick={() => choose("instance", i.id)}
                      >
                        <i className={"dot " + i.status} />
                        {a.role_name}#{i.seq}
                        {a.resident_instance_id === i.id ? " ◉" : ""}
                      </button>
                    ))}
                  </div>
                ))}
            </div>
          ))}
        </div>
        <footer>
          <i className="dot running" /> Daemon 在线{" "}
          <small>PID {data.pid}</small>
        </footer>
      </aside>
      <main>
        <header>
          <div className="crumb">
            工作台 /{" "}
            {selection.kind === "instance"
              ? instance?.id
              : selection.kind === "agent"
                ? agent?.role_name
                : selection.kind === "queue"
                  ? "消息队列"
                  : selection.kind === "bells"
                    ? "Doorbell 监控"
                    : "总览"}
          </div>
          <button className="primary" onClick={() => setModal("agent")}>
            ＋ 新建 Agent
          </button>
        </header>
        {error && (
          <div className="alert error" role="alert">
            <button onClick={() => setError("")}>×</button>
            {error}
          </div>
        )}
        {notice && (
          <div className="alert" role="status">
            <button onClick={() => setNotice("")}>×</button>
            {notice}
          </div>
        )}
        <section className="content">
          {(selection.kind === "overview" || selection.kind === "group") && (
            <>
              <div className="page-title">
                <div>
                  <h1>
                    {selection.kind === "group"
                      ? data.groups.find((g: R) => g.id === selection.id)?.name
                      : "坊 · Agent 工作台"}
                  </h1>
                  <p>管理独立运行的 Agent，查看协作与执行进度。</p>
                </div>
                <button onClick={() => setModal("group")}>＋ 新建坊</button>
              </div>
              <div className="stats">
                {[
                  ["逻辑 Agent", data.agents.length],
                  [
                    "运行中",
                    data.agents
                      .flatMap((a: R) => a.instances)
                      .filter((i: R) => i.status === "running").length,
                  ],
                  [
                    "排队消息",
                    data.messages.filter((m: R) => m.status === "queued")
                      .length,
                  ],
                  ["自定义 Doorbell", data.doorbells.length],
                ].map(([label, n]) => (
                  <div className="card" key={label}>
                    <small>{label}</small>
                    <strong>{n}</strong>
                  </div>
                ))}
              </div>
              {data.groups
                .filter(
                  (g: R) => selection.kind !== "group" || g.id === selection.id,
                )
                .map((g: R) => (
                  <div className="card group-card" key={g.id}>
                    <div className="row">
                      <div>
                        <h2>{g.name}</h2>
                        <small>
                          {g.id} · {g.paused ? "调度已暂停" : "调度中"}
                        </small>
                      </div>
                      <div className="actions">
                        <button
                          onClick={() =>
                            void act(() =>
                              api("group_update", {
                                id: g.id,
                                paused: !g.paused,
                              }),
                            )
                          }
                        >
                          {g.paused ? "恢复调度" : "暂停调度"}
                        </button>
                        <button
                          className="danger"
                          onClick={() => {
                            if (confirm("删除此组及其中全部 Agent？"))
                              void act(() => api("group_delete", { id: g.id }));
                          }}
                        >
                          删除坊
                        </button>
                      </div>
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>角色</th>
                          <th>运行模式</th>
                          <th>身份</th>
                          <th>实例</th>
                          <th>最近更新</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {data.agents
                          .filter(
                            (a: R) =>
                              a.group_id === g.id &&
                              (!search ||
                                `${a.role_name} ${a.id}`.includes(search)),
                          )
                          .map((a: R) => (
                            <tr key={a.id}>
                              <td>
                                <a
                                  href="#details"
                                  onClick={() => choose("agent", a.id)}
                                >
                                  {a.role_name}
                                </a>
                                <small>{a.id}</small>
                              </td>
                              <td>{a.run_mode}</td>
                              <td>{a.identity}</td>
                              <td>{a.instances.length}</td>
                              <td>{stamp(a.updated_at)}</td>
                              <td>
                                <button onClick={() => send(a.id)}>触发</button>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              {!data.groups.length && (
                <div className="empty card">
                  <h2>创建你的第一座坊</h2>
                  <p>在组内添加角色，再通过消息触发独立的 Agent。</p>
                  <button className="primary" onClick={() => setModal("group")}>
                    新建坊
                  </button>
                </div>
              )}
            </>
          )}
          {selection.kind === "agent" && agent && (
            <>
              <div className="page-title">
                <div>
                  <h1>
                    {agent.role_name} <Badge status={agent.run_mode} />
                  </h1>
                  <p>
                    {agent.id} · {agent.identity} · {agent.harness}
                  </p>
                </div>
                <div className="actions">
                  <button onClick={() => send(agent.id)}>
                    发送消息 / 触发
                  </button>
                  <button
                    className="danger"
                    onClick={() => {
                      if (confirm("删除 Agent、终止运行并归档全部实例？"))
                        void act(async () => {
                          await api("agent_delete", { id: agent.id });
                          choose("overview");
                        });
                    }}
                  >
                    删除 Agent
                  </button>
                </div>
              </div>
              <Tabs
                tabs={["配置", "Doorbell", "实例"]}
                tab={tab}
                setTab={setTab}
              />
              {tab === "配置" && (
                <>
                  {agent.execution && (
                    <div className="card">
                      <h2>执行语义</h2>
                      <p>{agent.execution.description}</p>
                      {agent.execution.shared_with.length > 0 && (
                        <p className="muted">
                          共用该目录：
                          {agent.execution.shared_with
                            .map((a: R) => `${a.role_name} (${a.logical_id})`)
                            .join("、")}
                        </p>
                      )}
                    </div>
                  )}
                  <AgentForm
                    key={agent.id + agent.updated_at}
                    agent={agent}
                    groups={data.groups}
                    submit={async (p) => {
                      await act(() =>
                        saveAgent("agent_update", { ...p, id: agent.id }),
                      );
                    }}
                  />
                </>
              )}
              {tab === "Doorbell" && (
                <>
                  <div className="card">
                    <div className="row">
                      <h2>自定义 Doorbell</h2>
                      <Badge status={logs.state?.status || "stopped"} />
                    </div>
                    <p>
                      重启次数 {logs.state?.restart_count || 0} · 最近触发{" "}
                      {stamp(logs.state?.last_ring_at)}
                    </p>
                    <p className="muted">
                      保存脚本后自动重启。可使用 #!/usr/bin/env
                      doorbell-python。
                    </p>
                    <textarea
                      aria-label="Doorbell 脚本"
                      disabled={scriptLoading}
                      className="code script"
                      value={script}
                      onChange={(e) => {
                        scriptEdited.current = true;
                        setScript(e.target.value);
                      }}
                    />
                    <div className="actions">
                      <button
                        className="primary"
                        disabled={scriptLoading}
                        onClick={() =>
                          void act(() =>
                            api("doorbell_script_save", {
                              logical_id: agent.id,
                              script,
                            }),
                          )
                        }
                      >
                        保存并重启
                      </button>
                      {["start", "stop", "restart"].map((action) => (
                        <button
                          key={action}
                          onClick={() =>
                            void act(() =>
                              api("doorbell_control", {
                                logical_id: agent.id,
                                action,
                              }),
                            )
                          }
                        >
                          {action}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="card">
                    <h2>实时日志</h2>
                    <pre>{logs.log || "暂无日志"}</pre>
                  </div>
                </>
              )}
              {tab === "实例" && (
                <div className="card">
                  <div className="row">
                    <h2>运行实例</h2>
                    <div className="actions">
                      <button onClick={() => send(agent.id)}>
                        手动触发{" "}
                        {agent.run_mode === "fork" ? "fork" : "常驻实例"}
                      </button>
                      {agent.run_mode === "resident" && (
                        <button
                          onClick={() => {
                            const prompt = window.prompt(
                              "为新 fork 实例发送消息（留空使用模板）",
                            );
                            if (prompt !== null)
                              void act(
                                () =>
                                  api("instance_fork", {
                                    logical_id: agent.id,
                                    ...(prompt ? { prompt } : {}),
                                  }),
                                "Fork 消息已入队",
                              );
                          }}
                        >
                          手动 Fork 新实例
                        </button>
                      )}
                      {agent.run_mode === "resident" && (
                        <button
                          onClick={() => {
                            if (
                              confirm(
                                "归档当前常驻实例？下次触发将创建新实例。",
                              )
                            )
                              void act(() =>
                                api("instance_renew_resident", {
                                  logical_id: agent.id,
                                }),
                              );
                          }}
                        >
                          换新常驻实例
                        </button>
                      )}
                    </div>
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>实例</th>
                        <th>状态</th>
                        <th>最近活动</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {agent.instances.map((i: R) => (
                        <tr key={i.id}>
                          <td>
                            <a
                              href="#details"
                              onClick={() => choose("instance", i.id)}
                            >
                              {agent.role_name}#{i.seq}
                            </a>
                            {agent.resident_instance_id === i.id && (
                              <span className="pill">常驻</span>
                            )}
                            <small>{i.id}</small>
                          </td>
                          <td>
                            <Badge status={i.status} />
                          </td>
                          <td>{stamp(i.last_active_at)}</td>
                          <td>
                            <button
                              disabled={!!i.archived_at}
                              onClick={() => archive(i)}
                            >
                              归档
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
          {selection.kind === "instance" && instance && (
            <>
              <div className="page-title">
                <div>
                  <h1>
                    {instance.config_snapshot.role_name}#{instance.seq}{" "}
                    <Badge status={instance.status} />
                  </h1>
                  <p>{instance.id}</p>
                </div>
                <div className="actions">
                  <button
                    disabled={!!instance.archived_at}
                    onClick={() => send(instance.id)}
                  >
                    发送消息
                  </button>
                  <button
                    disabled={instance.status !== "running"}
                    onClick={() =>
                      void act(() =>
                        api("run_stop", { instance_id: instance.id }),
                      )
                    }
                  >
                    终止运行
                  </button>
                  <button
                    disabled={!!instance.archived_at}
                    onClick={() => archive(instance)}
                  >
                    归档
                  </button>
                </div>
              </div>
              <Tabs
                tabs={["概况", "实时日志", "运行记录", "Diff"]}
                tab={tab}
                setTab={setTab}
              />
              {tab === "概况" && (
                <div className="card">
                  <dl>
                    {[
                      [
                        "Harness 会话",
                        instance.harness_session_id || "等待会话",
                      ],
                      ["工作目录", instance.worktree_path],
                      ["分支", instance.branch || "—"],
                      ["创建时间", stamp(instance.created_at)],
                    ].map(([k, v]) => (
                      <React.Fragment key={k}>
                        <dt>{k}</dt>
                        <dd>{v}</dd>
                      </React.Fragment>
                    ))}
                  </dl>
                  <h2>配置快照</h2>
                  <p className="muted">
                    已有实例沿用创建时的配置。常驻实例更新配置后需换新。
                  </p>
                  <Json value={instance.config_snapshot} />
                </div>
              )}
              {tab === "实时日志" && (
                <div className="card">
                  <div className="row">
                    <h2>标准化事件</h2>
                    <label>
                      <input
                        type="checkbox"
                        checked={follow}
                        onChange={(e) => setFollow(e.target.checked)}
                      />{" "}
                      跟随滚动
                    </label>
                  </div>
                  {events
                    .filter(
                      (e) =>
                        !e.run_id ||
                        detail?.runs.some((r: R) => r.id === e.run_id),
                    )
                    .map((e, n) => (
                      <details
                        key={n}
                        open={
                          e.type === "assistant_message" || e.type === "error"
                        }
                      >
                        <summary>
                          <Badge status={e.type} />{" "}
                          <small>{stamp(e.timestamp)}</small>{" "}
                          {String(e.text || e.message || e.command || "").slice(
                            0,
                            120,
                          )}
                        </summary>
                        <Json value={e} />
                      </details>
                    ))}
                  <div ref={bottom} />
                </div>
              )}
              {tab === "运行记录" && (
                <div className="card">
                  <h2>运行历史</h2>
                  {detail?.runs.map((r: R) => (
                    <details key={r.id}>
                      <summary>
                        <Badge status={r.status} /> {r.kind} ·{" "}
                        {stamp(r.started_at)} ·{" "}
                        {r.ended_at
                          ? (
                              (+new Date(r.ended_at) -
                                +new Date(r.started_at)) /
                              1000
                            ).toFixed(1) + " 秒"
                          : "运行中"}
                      </summary>
                      <p>
                        触发来源：{r.trigger?.sender?.label || "历史来源未知"} /{" "}
                        {r.trigger?.source || "—"} ·{" "}
                        {r.trigger?.sender?.identity || "—"}
                      </p>
                      <p className="muted">
                        排队耗时：
                        {r.queue_wait_ms == null
                          ? "旧记录未保存"
                          : (r.queue_wait_ms / 1000).toFixed(1) + " 秒"}
                        。
                        {!r.trigger_is_snapshot &&
                          "旧记录没有触发快照；下方 prompt 是当时实际发送的内容。"}
                      </p>
                      <h3>实际 Prompt</h3>
                      <pre>{r.prompt}</pre>
                      <h3>最后输出</h3>
                      <pre>{r.final_output || r.error || "—"}</pre>
                      <h3>用量</h3>
                      <Json value={r.usage} />
                      <EventHistory id={r.id} />
                    </details>
                  ))}
                </div>
              )}
              {tab === "Diff" && diff && (
                <div className="card">
                  <div className="row">
                    <h2>
                      {diff.mode === "git"
                        ? "分支相对 main"
                        : "Worktree 相对 base"}
                    </h2>
                    <div className="actions">
                      {diff.mode === "git" && (
                        <>
                          <button
                            className="primary"
                            onClick={() => {
                              if (confirm("将分支合并到 main？"))
                                void act(async () => {
                                  const r = await api("instance_merge", {
                                    id: instance.id,
                                  });
                                  await loadDetail();
                                  return r;
                                });
                            }}
                          >
                            合并到 main
                          </button>
                          {instance.archived_at && (
                            <button
                              onClick={() => {
                                if (confirm("删除保留的 git 分支？"))
                                  void act(() =>
                                    api("branch_delete", { id: instance.id }),
                                  );
                              }}
                            >
                              删除分支
                            </button>
                          )}
                        </>
                      )}
                      {diff.mode === "plain" && (
                        <>
                          <button
                            onClick={() => {
                              if (
                                confirm(
                                  "从 main 刷新 base？只影响之后的 fork。",
                                )
                              )
                                void act(() =>
                                  api("workdir_refresh", {
                                    id: instance.config_snapshot.workdir.id,
                                  }),
                                );
                            }}
                          >
                            从 main 刷新 base
                          </button>
                          <button
                            className="primary"
                            disabled={!selectedFiles.length}
                            onClick={() =>
                              void act(async () => {
                                const conflict = diff.files.some(
                                  (f: R) =>
                                    selectedFiles.includes(f.path) &&
                                    f.conflict,
                                );
                                if (
                                  conflict &&
                                  !confirm(
                                    "所选文件在 main 也已修改，确认覆盖？",
                                  )
                                )
                                  return;
                                const r = await api("instance_merge", {
                                  id: instance.id,
                                  files: selectedFiles,
                                  confirm_overwrite: conflict,
                                });
                                await loadDetail();
                                return r;
                              })
                            }
                          >
                            应用所选到 main
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {diff.dirty_main && (
                    <p className="alert">main 有未提交改动，fork 使用 HEAD。</p>
                  )}
                  {diff.conflicts?.length > 0 && (
                    <p className="alert error">
                      合并冲突：{diff.conflicts.join(", ")}。请在 main 中解决。
                    </p>
                  )}
                  {diff.mode === "direct" ? (
                    <p>direct 模式直接修改工作目录。</p>
                  ) : diff.mode === "git" ? (
                    <pre>{diff.diff || "没有差异"}</pre>
                  ) : (
                    diff.files.map((f: R) => (
                      <details key={f.path}>
                        <summary>
                          <input
                            aria-label={"选择 " + f.path}
                            type="checkbox"
                            checked={selectedFiles.includes(f.path)}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) =>
                              setSelectedFiles(
                                e.target.checked
                                  ? [...selectedFiles, f.path]
                                  : selectedFiles.filter((x) => x !== f.path),
                              )
                            }
                          />{" "}
                          {f.path}{" "}
                          {f.conflict && (
                            <span className="conflict">main 已修改 · 冲突</span>
                          )}
                        </summary>
                        <pre>
                          {f.diff}
                          {f.content && "\n" + f.content}
                        </pre>
                      </details>
                    ))
                  )}
                </div>
              )}
            </>
          )}
          {selection.kind === "queue" && (
            <>
              <div className="page-title">
                <div>
                  <h1>消息队列</h1>
                  <p>查看排队、处理中和失败的消息。</p>
                </div>
              </div>
              <div className="card">
                <div className="actions filters">
                  <select
                    aria-label="按组筛选"
                    value={filter.group}
                    onChange={(e) =>
                      setFilter({ ...filter, group: e.target.value })
                    }
                  >
                    <option value="">全部坊</option>
                    {data.groups.map((g: R) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                  <input
                    placeholder="筛选目标 ID"
                    value={filter.target}
                    onChange={(e) =>
                      setFilter({ ...filter, target: e.target.value })
                    }
                  />
                  <select
                    aria-label="按来源筛选"
                    value={filter.source}
                    onChange={(e) =>
                      setFilter({ ...filter, source: e.target.value })
                    }
                  >
                    <option value="">全部来源</option>
                    {[
                      "ring",
                      "doorbell_script",
                      "message_to",
                      "ui",
                      "host",
                    ].map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>目标 / 来源</th>
                      <th>状态</th>
                      <th>内容</th>
                      <th>时间</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {queue.map((m: R) => (
                      <tr key={m.id}>
                        <td>
                          {m.target_id}
                          <small>
                            {m.source} · {m.sender?.label || m.from_role}
                          </small>
                          <small>
                            {m.sender?.identity} · {m.from_id}
                          </small>
                        </td>
                        <td>
                          <Badge status={m.status} />
                          {m.queue_reason && (
                            <small>{m.queue_reason.description}</small>
                          )}
                          {m.queue_reason?.blocking_instance_id && (
                            <small>
                              阻塞实例：{m.queue_reason.blocking_instance_id}
                              <br />
                              运行：{m.queue_reason.blocking_run_id}
                            </small>
                          )}
                          {m.queue_reason?.ahead_message_id && (
                            <small>
                              前序消息：{m.queue_reason.ahead_message_id}
                            </small>
                          )}
                        </td>
                        <td>
                          <details>
                            <summary>
                              {m.prompt?.slice(0, 50) || "模板变量"}
                            </summary>
                            <Json value={m} />
                          </details>
                          {m.error && (
                            <small className="conflict">{m.error}</small>
                          )}
                        </td>
                        <td>{stamp(m.created_at)}</td>
                        <td>
                          {["failed", "cancelled"].includes(m.status) && (
                            <button
                              onClick={() =>
                                void act(() =>
                                  api("message_retry", { id: m.id }),
                                )
                              }
                            >
                              重试
                            </button>
                          )}
                          {m.status === "rejected" && (
                            <button
                              onClick={() => {
                                if (
                                  confirm(
                                    `${m.error}\n确认将这条消息加入队尾？`,
                                  )
                                )
                                  void act(() =>
                                    api("message_force_push", { id: m.id }),
                                  );
                              }}
                            >
                              确认推送
                            </button>
                          )}
                          {["queued", "rejected"].includes(m.status) && (
                            <button
                              onClick={() =>
                                void act(() =>
                                  api("message_cancel", { id: m.id }),
                                )
                              }
                            >
                              取消
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {selection.kind === "bells" && (
            <>
              <div className="page-title">
                <div>
                  <h1>Doorbell 监控</h1>
                  <p>脚本进程、重启次数和最近触发时间。</p>
                </div>
                <div className="actions">
                  {["start", "stop"].map((action) => (
                    <button
                      key={action}
                      onClick={() =>
                        void act(() =>
                          Promise.all(
                            data.agents
                              .filter((a: R) => a.doorbell_script)
                              .map((a: R) =>
                                api("doorbell_control", {
                                  logical_id: a.id,
                                  action,
                                }),
                              ),
                          ),
                        )
                      }
                    >
                      全部 {action}
                    </button>
                  ))}
                </div>
              </div>
              <div className="card">
                <table>
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>状态</th>
                      <th>PID</th>
                      <th>重启次数</th>
                      <th>最近触发</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {data.doorbells.map((d: R) => (
                      <tr key={d.logical_id}>
                        <td>
                          <a
                            href="#details"
                            onClick={() => {
                              choose("agent", d.logical_id);
                              setTab("Doorbell");
                            }}
                          >
                            {data.agents.find((a: R) => a.id === d.logical_id)
                              ?.role_name || d.logical_id}
                          </a>
                          {d.error && (
                            <small className="conflict">{d.error}</small>
                          )}
                        </td>
                        <td>
                          <Badge status={d.status} />
                        </td>
                        <td>{d.pid || "—"}</td>
                        <td>{d.restart_count}</td>
                        <td>{stamp(d.last_ring_at)}</td>
                        <td>
                          <button
                            onClick={() =>
                              void act(() =>
                                api("doorbell_control", {
                                  logical_id: d.logical_id,
                                  action: "restart",
                                }),
                              )
                            }
                          >
                            重启
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </main>
      {modal && (
        <div className="overlay">
          <div className="modal">
            <div className="row">
              <h2>{modal === "group" ? "新建坊" : "新建逻辑 Agent"}</h2>
              <button onClick={() => setModal(null)}>×</button>
            </div>
            {modal === "group" ? (
              <form onSubmit={createGroup}>
                <label>
                  坊名
                  <input name="name" required autoFocus />
                </label>
                <button className="primary">创建</button>
              </form>
            ) : (
              <AgentForm
                groups={data.groups}
                submit={async (p) => {
                  const result = await act(() => saveAgent("agent_create", p));
                  if (result) {
                    setModal(null);
                    choose("agent", result.id);
                  }
                }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
function Tabs({
  tabs,
  tab,
  setTab,
}: {
  tabs: string[];
  tab: string;
  setTab: (s: string) => void;
}) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button
          className={t === tab ? "active" : ""}
          key={t}
          onClick={() => setTab(t)}
        >
          {t}
        </button>
      ))}
    </div>
  );
}
function EventHistory({ id }: { id: string }) {
  const [events, setEvents] = useState<any>(null);
  return (
    <details
      onToggle={(e) => {
        if (e.currentTarget.open && !events)
          void api("run_events", { id }).then(setEvents);
      }}
    >
      <summary>完整事件</summary>
      {events && <Json value={events} />}
    </details>
  );
}
function AgentForm({
  agent,
  groups,
  submit,
}: {
  agent?: R;
  groups: R[];
  submit: (p: R) => Promise<void>;
}) {
  const [err, setErr] = useState(""),
    [template, setTemplate] = useState(
      (agent?.system_prompt_tpl || "") + "\n" + (agent?.prompt_tpl || ""),
    ),
    [saving, setSaving] = useState(false);
  const vars = [
    ...new Set(
      [...template.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)].map((m) => m[1]),
    ),
  ];
  return (
    <form
      className="card form"
      onSubmit={async (e) => {
        e.preventDefault();
        setErr("");
        setSaving(true);
        try {
          const p = Object.fromEntries(new FormData(e.currentTarget));
          const env = JSON.parse(String(p.extra_env)),
            options = JSON.parse(String(p.harness_options));
          const workdir = String(p.workdir || "");
          await submit({
            ...p,
            extra_env: env,
            harness_options: options,
            worktree: p.worktree !== "false",
            git_init: p.git_init !== "false",
            ...(workdir ? {} : { workdir: undefined }),
          });
        } catch (e: any) {
          setErr(e.message);
        } finally {
          setSaving(false);
        }
      }}
    >
      <div className="form-grid">
        {!agent && (
          <label>
            Group
            <select name="group" required>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          角色名
          <input name="role_name" required defaultValue={agent?.role_name} />
        </label>
        <label>
          身份
          <select name="identity" defaultValue={agent?.identity || "worker"}>
            <option value="worker">Worker</option>
            <option value="admin">Administrator</option>
          </select>
        </label>
        <label>
          Harness
          <select name="harness" defaultValue={agent?.harness || "codex"}>
            <option>codex</option>
            <option>claude</option>
          </select>
        </label>
        <label>
          模型
          <input
            name="model"
            placeholder="使用 harness 默认模型"
            defaultValue={agent?.model || ""}
          />
        </label>
        <label>
          运行模式
          <select name="run_mode" defaultValue={agent?.run_mode || "fork"}>
            <option value="fork">Fork · 每次新建实例</option>
            <option value="resident">Resident · 保留上下文</option>
          </select>
        </label>
        <label>
          工作目录（绝对路径）
          <input
            name="workdir"
            required={!agent}
            defaultValue={agent?.workdir?.root || ""}
            placeholder={
              agent ? "留空沿用当前目录" : "/absolute/path/to/workspace"
            }
          />
        </label>
        <label>
          Worktree
          <select
            name="worktree"
            defaultValue={agent?.workdir?.mode === "direct" ? "false" : "true"}
          >
            <option value="true">开启独立工作目录</option>
            <option value="false">关闭 · 目录全局串行</option>
          </select>
        </label>
        <label>
          新目录初始化 Git
          <select
            name="git_init"
            defaultValue={agent?.workdir?.mode === "plain" ? "false" : "true"}
          >
            <option value="true">是（默认）</option>
            <option value="false">否 · 非 git 快照</option>
          </select>
        </label>
        <label>
          Harness 参数（JSON）
          <textarea
            name="harness_options"
            className="code"
            defaultValue={pretty(agent?.harness_options || {})}
          />
        </label>
        <label>
          额外环境变量（JSON）
          <textarea
            name="extra_env"
            className="code"
            defaultValue={pretty(agent?.extra_env || {})}
          />
        </label>
      </div>
      {["system_prompt_tpl", "prompt_tpl"].map((k) => (
        <label key={k}>
          {k}
          <textarea
            className="code template"
            name={k}
            defaultValue={agent?.[k] || ""}
            onChange={(e) => {
              const form = e.currentTarget.form!;
              setTemplate(
                String(
                  (
                    form.elements.namedItem(
                      "system_prompt_tpl",
                    ) as HTMLTextAreaElement
                  ).value,
                ) +
                  "\n" +
                  String(
                    (
                      form.elements.namedItem(
                        "prompt_tpl",
                      ) as HTMLTextAreaElement
                    ).value,
                  ),
              );
            }}
          />
        </label>
      ))}
      <pre className="template-preview" aria-label="模板预览">
        {template
          .split(/(\{\{.*?\}\})/g)
          .map((part, n) =>
            part.startsWith("{{") ? (
              <mark key={n}>{part}</mark>
            ) : (
              <span key={n}>{part}</span>
            ),
          )}
      </pre>
      <div className="variables">
        模板变量：
        {vars.length
          ? vars.map((v) => <mark key={v}>{"{{" + v + "}}"}</mark>)
          : "无"}
      </div>
      {agent && (
        <p className="muted">
          当前目录模式：{agent.workdir?.mode}。配置变更只影响之后的 fork。
        </p>
      )}
      {err && <p className="conflict">{err}</p>}
      <button
        disabled={saving || (!agent && !groups.length)}
        className="primary"
      >
        {saving ? "保存中…" : agent ? "保存配置" : "创建 Agent"}
      </button>
    </form>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
