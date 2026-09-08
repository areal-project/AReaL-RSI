"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  runId: localStorage.getItem("rsi.workbench.run") || null,
  run: null,
  runs: [],
  events: new Map(),
  cursors: new Map(),
  selected: new Map(),
  graphSignature: "",
  detailSignature: "",
  messageSignature: "",
  tab: "chat",
  polling: false,
  sending: false,
  busy: false,
  drafts: new Map(),
  graphRunId: null,
  detailNodeId: null,
  refreshSequence: 0,
  latestAppliedSequence: 0,
  allowSimulation: false,
  defaultSimulated: false,
  hasConfig: false,
};
const emptyChat = $("messages").firstElementChild.cloneNode(true);
const statuses = {
  planned: "待确认",
  running: "进行中",
  waiting: "等待消息",
  paused: "已暂停",
  stopped: "已停止",
  failed: "失败",
  pending: "待执行",
  queued: "排队中",
  completed: "已完成",
  succeeded: "已完成",
  cancelled: "已取消",
  created: "已创建",
};
const eventNames = {
  run_created: "创建研究",
  run_started: "研究开始",
  run_paused: "研究暂停",
  run_resumed: "研究继续",
  run_stopped: "研究停止",
  agent_created: "建立研究方向",
  assignment_created: "下发研究任务",
  report_submitted: "收到研究报告",
  evidence_shared: "共享研究经验",
  candidate_saved: "保存候选产物",
  verification_completed: "验证完成",
  agent_waiting: "等待下一条消息",
  agent_failed: "Agent 遇到问题",
  agent_stopped: "停止研究方向",
  job_submitted: "提交作业",
  job_completed: "作业完成",
  budget_exhausted: "达到预算边界",
  session_compacted: "保存会话检查点",
  agent_retry_requested: "恢复 Agent",
};

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null && text !== undefined) node.textContent = String(text);
  return node;
}
function replace(id, ...children) {
  $(id).replaceChildren(...children);
}
function label(value) {
  return statuses[value] || value || "未知";
}
function badge(value) {
  return element("span", `status ${value || ""}`, label(value));
}
function score(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 4 })
    : "—";
}
function integer(value) {
  return Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}
function time(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
function notice(message = "") {
  $("notice").hidden = !message;
  $("notice").textContent = message;
}
function connected(ok) {
  $("connection").textContent = ok ? "已连接" : "重连中";
  $("connection-dot").classList.toggle("offline", !ok);
  $("last-updated").textContent = ok
    ? `${time(Date.now())} 更新`
    : "重新连接中…";
}
async function api(path, body) {
  const options = {
    headers: {},
    cache: "no-store",
    credentials: "same-origin",
  };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-RSI-Token": state.token,
    };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    if (response.status === 403) state.token = "";
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `请求失败（${response.status}）`);
  }
  if (response.headers.get("Content-Disposition")) return response;
  return response.json();
}

async function loadRuns() {
  const result = await api("/api/runs");
  state.runs = result.runs || [];
  if (!state.runs.some((run) => run.run_id === state.runId))
    state.runId = state.runs[0]?.run_id || null;
  renderRuns();
  $("welcome").hidden = !!state.runId;
  $("workspace").hidden = !state.runId;
  if (!state.runId) {
    state.run = null;
    $("run-short").textContent = "准备开始";
  }
}
function renderRuns() {
  const buttons = state.runs.map((run) => {
    const button = element(
      "button",
      `run-item ${run.run_id === state.runId ? "selected" : ""}`,
    );
    button.append(element("span", "run-indicator"));
    const body = element("span");
    body.append(element("span", "run-name", run.objective || "未命名研究"));
    body.append(
      element(
        "small",
        "",
        `${label(run.status)}${run.simulated ? " · 模拟" : ""}`,
      ),
    );
    button.append(body);
    button.addEventListener("click", () => selectRun(run.run_id));
    return button;
  });
  replace(
    "run-list",
    ...(buttons.length ? buttons : [element("p", "muted", "还没有研究记录")]),
  );
  $("mobile-runs").replaceChildren(
    ...state.runs.map((run) => {
      const option = element("option", "", run.objective || "未命名研究");
      option.value = run.run_id;
      option.selected = run.run_id === state.runId;
      return option;
    }),
  );
}
async function selectRun(runId) {
  if (state.runId === runId && state.run) return;
  if (state.runId) state.drafts.set(state.runId, $("chat-input").value);
  state.runId = runId;
  $("chat-input").value = state.drafts.get(runId) || "";
  state.run = null;
  state.graphSignature = state.detailSignature = state.messageSignature = "";
  state.events.clear();
  state.cursors.set(runId, 0);
  localStorage.setItem("rsi.workbench.run", runId);
  const saved = localStorage.getItem(`rsi.workbench.node.${runId}`);
  if (saved) state.selected.set(runId, saved);
  notice();
  renderRuns();
  await refresh(true);
}

async function refresh(force = false) {
  if (!state.runId || (state.polling && !force)) return;
  const runId = state.runId;
  const sequence = ++state.refreshSequence;
  state.polling = true;
  try {
    const after = state.cursors.get(runId) || 0;
    const run = await api(
      `/api/runs/${encodeURIComponent(runId)}?after=${after}`,
    );
    if (state.runId !== runId || sequence < state.latestAppliedSequence) return;
    state.latestAppliedSequence = sequence;
    if (Number.isInteger(run.event_cursor) && run.event_cursor < after)
      state.events.clear();
    for (const event of run.events || [])
      state.events.set(event.sequence, event);
    while (state.events.size > 100)
      state.events.delete(state.events.keys().next().value);
    state.cursors.set(runId, run.event_cursor ?? after);
    state.run = run;
    const listed = state.runs.find((item) => item.run_id === runId);
    if (
      listed &&
      (listed.status !== run.status || listed.objective !== run.objective)
    ) {
      Object.assign(listed, { status: run.status, objective: run.objective });
      renderRuns();
    }
    render();
    connected(true);
  } catch (error) {
    connected(false);
    if (force) notice(error.message);
  } finally {
    state.polling = false;
  }
}

function render() {
  const run = state.run;
  if (!run) return;
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  $("objective").textContent =
    run.objective || run.brief?.objective || "未命名研究";
  $("run-short").textContent = run.run_id;
  $("simulated").hidden = !run.simulated;
  const needsResume = run.status === "running" && run.worker_alive === false;
  $("run-status").textContent = needsResume ? "待恢复" : label(run.status);
  $("run-status").className = `status ${run.status}`;
  $("start").hidden = run.status !== "planned";
  $("pause").hidden = run.status !== "running";
  $("resume").hidden = run.status !== "paused" && !needsResume;
  $("resume").textContent = needsResume ? "恢复执行" : "继续";
  $("stop").hidden = !["running", "paused"].includes(run.status);
  $("export").disabled = !run.best?.candidate_id || state.busy;
  $("best-score").textContent = score(run.best?.score);
  $("best-caption").textContent = run.best
    ? `${run.brief?.verification?.metric || "验证分数"}${run.best.simulated ? " · 模拟结果" : " · 同一验证条件"}`
    : "尚无可比较的验证结果";
  replace(
    "baseline-score",
    document.createTextNode(score(run.baseline?.score)),
    element("i", "", "/"),
    document.createTextNode(score(run.final?.score)),
  );
  $("baseline-caption").textContent = run.baseline
    ? "起点结果 / 独立最终确认"
    : "未设置起点 · 暂不计算相对提升";
  const nodes = run.nodes || [];
  $("node-count").textContent = integer(nodes.length);
  $("node-caption").textContent =
    `${nodes.filter((node) => node.state === "completed").length} 已完成 · ${(run.agents || []).filter((agent) => agent.direction && agent.state !== "stopped").length} 个研究方向`;
  $("model-count").textContent =
    `${integer(run.budget?.model_calls ?? 0)} / ${integer(run.brief?.limits?.max_model_calls)}`;
  $("token-count").textContent =
    `${integer(run.budget?.total_tokens ?? 0)} tokens${run.budget?.reserved_tokens ? ` · ${integer(run.budget.reserved_tokens)} 已预留` : ""}`;
  $("plan").textContent =
    run.plan || "研究开始后，RSI 会在这里说明当前方向和下一步。";
  $("plan").classList.toggle("muted", !run.plan);
  renderDirections(run);
  renderGraph(run);
  renderMessages(run);
  renderNode(run);
  renderBrief(run);
  renderJobs(run);
  renderEvents();
  const questions = run.waiting_for_user || [];
  $("pending-questions").hidden = !questions.length;
  $("pending-questions").textContent = questions.length
    ? `需要你补充：${questions.map((question) => (typeof question === "string" ? question : question.text || question.question || "请查看对话")).join("\n")}`
    : "";
  const interaction = (run.agents || []).find(
    (agent) => agent.agent_id === "interaction",
  );
  $("chat-status").textContent = state.sending
    ? "正在发送…"
    : interaction?.state === "running"
      ? "Interaction Agent 正在整理你的需求…"
      : "Enter 发送 · Shift+Enter 换行";
  if (run.budget_stop_reason) notice(run.budget_stop_reason);
}

function renderDirections(run) {
  replace(
    "directions",
    ...(run.agents || [])
      .filter((agent) => agent.direction)
      .map((agent) => {
        const item = element(
          "span",
          `direction ${agent.state}`,
          `${agent.direction} · ${agent.wait_reason === "rsi_reply" ? "等待 RSI 回复" : label(agent.state)}`,
        );
        if (agent.error) item.title = agent.error;
        if (agent.state === "failed") {
          const retry = element("button", "", "重试");
          retry.addEventListener("click", () =>
            control("retry", { agent_id: agent.agent_id }),
          );
          item.append(retry);
        }
        return item;
      }),
  );
}

function renderGraph(run) {
  const nodes = run.nodes || [];
  const selected = state.selected.get(run.run_id);
  const signature = JSON.stringify([
    run.run_id,
    nodes,
    run.edges,
    run.agents?.map((agent) => [agent.agent_id, agent.direction]),
    selected,
  ]);
  if (signature === state.graphSignature) return;
  state.graphSignature = signature;
  const viewport = $("graph-viewport");
  let scroll = { left: viewport.scrollLeft, top: viewport.scrollTop };
  if (state.graphRunId !== run.run_id) {
    try {
      scroll = JSON.parse(
        localStorage.getItem(`rsi.workbench.viewport.${run.run_id}`),
      ) || { left: 0, top: 0 };
    } catch {
      scroll = { left: 0, top: 0 };
    }
    state.graphRunId = run.run_id;
  }
  const focused = document.activeElement?.dataset.node;
  const canvas = $("graph-canvas");
  canvas.replaceChildren();
  canvas.hidden = !nodes.length;
  $("graph-empty").hidden = !!nodes.length;
  if (!nodes.length) return;
  const byId = new Map(nodes.map((node) => [node.iteration_id, node]));
  const agents = [...new Set(nodes.map((node) => node.agent_id))];
  const positions = new Map();
  function depth(node, visited = new Set()) {
    if (!node?.parent_iteration_id || visited.has(node.iteration_id)) return 0;
    visited.add(node.iteration_id);
    return Math.min(
      100,
      1 + depth(byId.get(node.parent_iteration_id), visited),
    );
  }
  let widest = 1;
  agents.forEach((agentId, row) => {
    const direction =
      run.agents?.find((agent) => agent.agent_id === agentId)?.direction ||
      `研究方向 ${row + 1}`;
    const lane = element("div", "graph-lane", direction);
    lane.style.top = `${row * 153 + 20}px`;
    canvas.append(lane);
    nodes
      .filter((node) => node.agent_id === agentId)
      .forEach((node, index) => {
        const column = Math.max(index, depth(node));
        widest = Math.max(widest, column + 1);
        const position = { x: 24 + column * 232, y: row * 153 + 46 };
        positions.set(node.iteration_id, position);
        const button = element(
          "button",
          `graph-node ${selected === node.iteration_id ? "selected" : ""}`,
        );
        button.dataset.node = node.iteration_id;
        button.style.left = `${position.x}px`;
        button.style.top = `${position.y}px`;
        const top = element("div", "node-top");
        top.append(element("span", "", `尝试 ${index + 1}`), badge(node.state));
        button.append(
          top,
          element(
            "h3",
            "",
            node.title || node.assignment?.instruction || "研究尝试",
          ),
        );
        if (typeof node.verification?.score === "number")
          button.append(
            element(
              "p",
              "node-score",
              `${score(node.verification.score)}${node.verification.simulated ? " · 模拟" : ""}`,
            ),
          );
        button.setAttribute(
          "aria-pressed",
          String(selected === node.iteration_id),
        );
        button.addEventListener("click", () => {
          state.selected.set(run.run_id, node.iteration_id);
          localStorage.setItem(
            `rsi.workbench.node.${run.run_id}`,
            node.iteration_id,
          );
          switchTab("node");
          renderGraph(run);
          renderNode(run);
          if (window.matchMedia("(max-width: 720px)").matches)
            $("node-panel")
              .closest(".inspector")
              .scrollIntoView({ block: "start" });
        });
        canvas.append(button);
      });
  });
  const edges = [...(run.edges || [])];
  for (const node of nodes)
    if (
      node.parent_iteration_id &&
      !edges.some(
        (edge) =>
          edge.source === node.parent_iteration_id &&
          edge.target === node.iteration_id,
      )
    )
      edges.push({
        source: node.parent_iteration_id,
        target: node.iteration_id,
        kind: "parent",
      });
  for (const edge of edges) {
    const source = positions.get(edge.source),
      target = positions.get(edge.target);
    if (!source || !target || edge.source === edge.target) continue;
    const x1 = source.x + 190,
      y1 = source.y + 46,
      x2 = target.x,
      y2 = target.y + 46;
    const line = element(
      "div",
      `graph-edge ${["reference", "merge"].includes(edge.kind) ? edge.kind : "parent"}`,
    );
    Object.assign(line.style, {
      left: `${x1}px`,
      top: `${y1}px`,
      width: `${Math.hypot(x2 - x1, y2 - y1)}px`,
      transform: `rotate(${Math.atan2(y2 - y1, x2 - x1)}rad)`,
    });
    line.setAttribute("aria-hidden", "true");
    line.title =
      edge.label || (edge.kind === "parent" ? "继续探索" : "引用 / 合并经验");
    canvas.prepend(line);
  }
  canvas.style.width = `${widest * 232 + 22}px`;
  canvas.style.height = `${agents.length * 153 + 16}px`;
  viewport.scrollTo({ left: scroll.left, top: scroll.top });
  if (focused)
    [...canvas.querySelectorAll("button")]
      .find((button) => button.dataset.node === focused)
      ?.focus({ preventScroll: true });
}

function section(title, value) {
  const block = element("section", "detail-section");
  block.append(element("h4", "", title), element("p", "", value || "暂无记录"));
  return block;
}
function renderNode(run) {
  const node = run.nodes?.find(
    (item) => item.iteration_id === state.selected.get(run.run_id),
  );
  const signature = JSON.stringify([run.run_id, node]);
  if (signature === state.detailSignature) return;
  state.detailSignature = signature;
  if (!node) {
    const blank = element("div", "empty-detail");
    blank.append(
      element("span", "empty-symbol", "↖"),
      element("h3", "", "选择一个研究节点"),
      element("p", "", "查看具体任务、尝试、问题、结果和共享证据。"),
    );
    replace("node-panel", blank);
    return;
  }
  const position =
    state.detailNodeId === node.iteration_id ? $("node-panel").scrollTop : 0;
  state.detailNodeId = node.iteration_id;
  const heading = element("div", "detail-header");
  heading.append(
    badge(node.state),
    element("h3", "", node.assignment?.instruction || "研究尝试"),
    element("p", "muted", node.iteration_id),
  );
  const blocks = [
    heading,
    section("完成条件", node.assignment?.completion_criteria),
  ];
  if (node.report) {
    const names = {
      task_status: "任务执行情况",
      work: "具体尝试",
      problems: "遇到的问题",
      results: "实际结果",
      interpretation: "结论与不确定性",
      next_steps: "建议的下一步",
    };
    for (const [name, title] of Object.entries(names))
      blocks.push(section(title, node.report[name]));
    if (node.report.shared_findings?.length)
      blocks.push(
        section("可共享的发现", node.report.shared_findings.join("\n")),
      );
  } else
    blocks.push(
      section(
        "本轮报告",
        ["pending", "running"].includes(node.state)
          ? "研究仍在进行，尚未提交报告。此节点不一定产生候选版本或验证分数。"
          : "此节点未产生报告。",
      ),
    );
  if (node.verification)
    blocks.push(
      section(
        "验证记录",
        `${score(node.verification.score)} · ${node.verification.phase || "search"}${node.verification.simulated ? " · 模拟结果" : ""}`,
      ),
    );
  for (const evidence of node.evidence || []) {
    const item = element("section", "evidence-card");
    item.append(
      element("h4", "", evidence.title || "研究证据"),
      element("p", "preserve", evidence.summary),
    );
    if (evidence.limitations)
      item.append(
        element("p", "muted", `适用范围与限制：${evidence.limitations}`),
      );
    item.append(element("div", "record-ref", evidence.evidence_id));
    blocks.push(item);
  }
  const refs = [...(node.references || []), ...(node.report?.references || [])];
  if (refs.length) {
    const block = element("section", "detail-section");
    block.append(element("h4", "", "记录引用"));
    for (const uri of new Set(refs.map((ref) => ref.uri).filter(Boolean)))
      block.append(element("div", "record-ref", uri));
    blocks.push(block);
  }
  replace("node-panel", ...blocks);
  $("node-panel").scrollTop = position;
}

function renderMessages(run) {
  const messages = run.messages || [];
  const signature = JSON.stringify([run.run_id, messages]);
  if (signature === state.messageSignature) return;
  state.messageSignature = signature;
  if (!messages.length) {
    replace("messages", emptyChat.cloneNode(true));
    return;
  }
  const container = $("messages");
  const follow =
    container.scrollHeight - container.scrollTop - container.clientHeight < 50;
  const previous = container.scrollTop;
  const blocks = messages.map((message) => {
    const block = element(
      "article",
      `message ${message.sender === "user" ? "user" : "assistant"}`,
    );
    const heading = element("div", "message-label");
    heading.append(
      element(
        "span",
        "",
        message.sender === "user"
          ? "你"
          : message.sender === "rsi"
            ? "RSI Agent"
            : "Interaction Agent",
      ),
      element("time", "", time(message.created_at)),
    );
    block.append(heading, element("div", "message-content", message.text));
    return block;
  });
  container.replaceChildren(...blocks);
  container.scrollTop = follow ? container.scrollHeight : previous;
}
function renderBrief(run) {
  const brief = run.brief || {},
    limits = brief.limits || {},
    verification = brief.verification;
  const blocks = [
    section("研究目标", brief.objective || run.objective),
    section("给 RSI 的要求与建议", brief.instructions),
  ];
  const grid = element("dl", "brief-grid");
  const rows = [
    ["Research Agents 上限", limits.max_agents],
    ["节点上限", limits.max_nodes],
    ["模型调用上限", limits.max_model_calls],
    ["Token 上限", integer(limits.max_total_tokens)],
    ["作业并发上限", limits.max_jobs],
    ["GPU", limits.gpu_ids?.join(", ") || "尚未配置"],
    ["允许修改", limits.allowed_paths?.join(", ") || "待确认"],
    ["验证指标", verification?.metric || "尚未设置"],
    ["目标", score(verification?.target)],
  ];
  for (const [key, value] of rows)
    grid.append(element("dt", "", key), element("dd", "", value ?? "—"));
  blocks.push(grid);
  if (verification?.description)
    blocks.push(section("验证条件", verification.description));
  replace("brief-panel", ...blocks);
}
function renderJobs(run) {
  const jobs = run.jobs || [];
  $("job-count").textContent = jobs.length;
  replace(
    "jobs",
    ...(jobs.length
      ? jobs.map((job) => {
          const row = element("div", "job-row");
          const body = element("div");
          body.append(
            element("h3", "", job.description || job.job_id),
            element(
              "p",
              "",
              job.progress_message ||
                job.error ||
                `${job.gpu_ids?.length ? `GPU ${job.gpu_ids.join(", ")} · ` : ""}${job.agent_id || ""}`,
            ),
          );
          row.append(body, badge(job.status));
          return row;
        })
      : [
          element(
            "p",
            "muted",
            "当前没有作业。长时间执行的任务将在这里显示状态。",
          ),
        ]),
  );
}
function renderEvents() {
  const events = [...state.events.values()]
    .filter((event) => eventNames[event.kind])
    .slice(-20)
    .reverse();
  replace(
    "activity",
    ...(events.length
      ? events.map((event) => {
          const row = element("div", "event-row");
          const detail =
            event.data?.title ||
            event.data?.direction ||
            event.data?.reason ||
            "";
          row.append(
            element("time", "", time(event.created_at)),
            element(
              "span",
              "",
              `${eventNames[event.kind]}${detail ? ` · ${detail}` : ""}`,
            ),
          );
          return row;
        })
      : [element("p", "muted", "开始研究后，这里会记录关键进展。")]),
  );
}
function switchTab(tab) {
  state.tab = tab;
  for (const name of ["chat", "node", "brief"]) {
    $(`${name}-panel`).hidden = tab !== name;
    $(`${name}-tab`).classList.toggle("selected", tab === name);
    $(`${name}-tab`).setAttribute("aria-selected", String(tab === name));
  }
}

async function control(action, params = {}) {
  if (!state.runId || state.busy) return;
  state.busy = true;
  notice();
  for (const button of $("controls").querySelectorAll("button"))
    button.disabled = true;
  try {
    const result = await api(
      `/api/runs/${encodeURIComponent(state.runId)}/${action}`,
      params,
    );
    if (result instanceof Response) {
      const blob = await result.blob();
      const url = URL.createObjectURL(blob);
      const link = element("a");
      link.href = url;
      const disposition = result.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      link.download = filename
        ? decodeURIComponent(filename)
        : "rsi-artifact.zip";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } else if (result.notice) notice(result.notice);
    await refresh(true);
  } catch (error) {
    notice(error.message);
  } finally {
    state.busy = false;
    for (const button of $("controls").querySelectorAll("button"))
      button.disabled = false;
    $("export").disabled = !state.run?.best?.candidate_id;
  }
}

function openCreate() {
  $("create-error").hidden = true;
  $("create-simulated").checked = state.defaultSimulated;
  updateCreateEnvironment();
  $("create-dialog").showModal();
  $("new-objective").focus();
}
$("new-run").addEventListener("click", openCreate);
$("mobile-new").addEventListener("click", openCreate);
$("welcome-new").addEventListener("click", openCreate);
$("close-dialog").addEventListener("click", () => $("create-dialog").close());
$("mobile-runs").addEventListener("change", (event) =>
  selectRun(event.target.value),
);
for (const name of ["chat", "node", "brief"])
  $(`${name}-tab`).addEventListener("click", () => switchTab(name));
for (const button of $("controls").querySelectorAll("button"))
  button.addEventListener("click", () => control(button.dataset.action));
$("graph-reset").addEventListener("click", () =>
  $("graph-viewport").scrollTo({ left: 0, top: 0, behavior: "smooth" }),
);
$("graph-viewport").addEventListener(
  "scroll",
  () => {
    if (state.runId)
      localStorage.setItem(
        `rsi.workbench.viewport.${state.runId}`,
        JSON.stringify({
          left: $("graph-viewport").scrollLeft,
          top: $("graph-viewport").scrollTop,
        }),
      );
  },
  { passive: true },
);
for (const name of ["jobs", "activity"])
  $(`${name}-tab`).addEventListener("click", () => {
    for (const other of ["jobs", "activity"]) {
      $(other).hidden = other !== name;
      $(`${other}-tab`).classList.toggle("selected", other === name);
      $(`${other}-tab`).setAttribute("aria-selected", String(other === name));
    }
  });
$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("create-submit").disabled = true;
  $("create-error").hidden = true;
  try {
    const result = await api("/api/runs", {
      template: $("template").value,
      objective: $("new-objective").value.trim(),
      instructions: $("new-instructions").value.trim(),
      max_agents: Number($("max-agents").value),
      simulated: $("create-simulated").checked,
    });
    if (!result.run_id)
      throw new Error("研究已创建，但没有返回记录编号，请刷新列表。");
    $("create-dialog").close();
    await loadRuns();
    state.runId = null;
    await selectRun(result.run_id);
    switchTab("chat");
  } catch (error) {
    $("create-error").textContent = error.message;
    $("create-error").hidden = false;
  } finally {
    updateCreateEnvironment();
  }
});
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text || !state.runId || state.sending) return;
  const runId = state.runId;
  state.sending = true;
  $("send-message").disabled = true;
  $("chat-status").textContent = "正在发送…";
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      text,
      message_id: crypto.randomUUID(),
    });
    state.drafts.set(runId, "");
    if (state.runId === runId && $("chat-input").value.trim() === text)
      $("chat-input").value = "";
    await refresh(true);
    $("messages").scrollTop = $("messages").scrollHeight;
  } catch (error) {
    notice(error.message);
  } finally {
    state.sending = false;
    $("send-message").disabled = false;
    if (state.run) render();
  }
});
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("chat-form").requestSubmit();
  }
});

function updateCreateEnvironment() {
  const simulated = $("create-simulated").checked;
  $("create-submit").disabled =
    !state.token || (!simulated && !state.hasConfig);
  $("create-environment").textContent = simulated
    ? "使用模拟作业验证流程，研究 Agent 仍会调用已配置的模型。"
    : state.hasConfig
      ? "使用启动工作台时提供的执行配置。创建后可继续确认目标与约束。"
      : "尚未配置执行环境。请用 --config 指定配置，或用 --simulate-gpu 启动流程演练。";
}
async function bootstrap() {
  const config = await api("/api/bootstrap");
  state.token = config.csrf_token;
  state.allowSimulation = config.allow_simulation === true;
  state.defaultSimulated =
    state.allowSimulation && config.default_simulated === true;
  state.hasConfig = config.has_config === true;
  $("create-simulated").disabled = !state.allowSimulation;
  $("create-simulated").checked = state.defaultSimulated;
  $("simulation-help").textContent = state.allowSimulation
    ? "验证流程，不代表真实成绩"
    : "启动时未启用模拟作业";
  const openResearch = $("template").querySelector('[value="research"]');
  openResearch.disabled = !state.hasConfig;
  openResearch.hidden = !state.hasConfig;
  updateCreateEnvironment();
}
$("create-simulated").addEventListener("change", updateCreateEnvironment);

async function boot() {
  try {
    await bootstrap();
    await loadRuns();
    if (state.runId) {
      const runId = state.runId;
      state.runId = null;
      await selectRun(runId);
    }
    connected(true);
  } catch (error) {
    connected(false);
    notice(error.message);
  }
  setInterval(async () => {
    if (!state.token) {
      try {
        await bootstrap();
        await loadRuns();
      } catch {
        connected(false);
        return;
      }
    }
    if (!document.hidden) await refresh();
  }, 1500);
}
boot();
