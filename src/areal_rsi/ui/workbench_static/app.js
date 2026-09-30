"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  runId: new URLSearchParams(window.location.search).get("run") ||
    localStorage.getItem("rsi.workbench.run") || null,
  run: null,
  runCache: new Map(),
  eventCache: new Map(),
  refreshController: null,
  loading: false,
  runs: [],
  groupExpansion: new Map(),
  events: new Map(),
  cursors: new Map(),
  selected: new Map(),
  assignmentExpansion: new Map(),
  reportExpansion: new Map(),
  graphSignature: "",
  detailSignature: "",
  messageSignature: "",
  tab: "chat",
  polling: false,
  sending: new Set(),
  chatErrors: new Map(),
  outgoing: new Map(),
  busy: false,
  deleting: false,
  deleteTarget: null,
  drafts: new Map(),
  replyTargets: new Map(),
  graphRunId: null,
  graphZoom: 1,
  graphAllJobs: false,
  graphCollaborationOnly: false,
  detailNodeId: null,
  refreshSequence: 0,
  latestAppliedSequence: 0,
  creating: false,
  models: [],
  defaultModelSettings: {},
  createEffort: "medium",
  modelDrafts: new Map(),
  modelFormRun: null,
  modelSavingRun: null,
  collaborationCatalog: {modes: [], modules: []},
  defaultCollaboration: {mode: "passk", modules: []},
  goalHistory: new Map(),
  agentTraces: new Map(),
  traceAvailable: false,
  traceExpansion: new Set(),
};
const emptyChat = $("messages").firstElementChild.cloneNode(true);
const mobileRunPicker = $("mobile-run-picker");
document.addEventListener("click", (event) => {
  if (!mobileRunPicker.contains(event.target)) mobileRunPicker.open = false;
});
mobileRunPicker.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    mobileRunPicker.open = false;
    mobileRunPicker.querySelector("summary").focus();
  }
});
const recipientDropdown = new WorkbenchDropdown($("chat-recipient"), {
  label: "发送给",
  onChange: (value) => {
    state.replyTargets.set(state.runId, { value, explicit: true });
    state.chatErrors.delete(state.runId);
    render();
    if (state.run) loadAgentTrace(state.runId, renderReplyTarget(state.run).recipient);
  },
});
const modelDropdown = new WorkbenchDropdown($("research-model"), {
  label: "模型",
  onChange: (value) => {
    const draft = modelDraft();
    draft.model = value;
    $("model-name").value = value;
    const supported = state.models.find((model) => model.id === value)?.efforts || [];
    if (!supported.includes(draft.reasoning_effort)) draft.reasoning_effort = "medium";
    renderModelSettings();
  },
});
const effortDropdown = new WorkbenchDropdown($("reasoning-effort"), {
  label: "推理强度",
  onChange: (value) => { modelDraft().reasoning_effort = value; renderModelSettings(); },
});
const backendOptions = [{value: "standard", text: "standard"}, {value: "codex", text: "codex"}, {value: "runtime", text: "Runtime 配置"}];
const backendDropdown = new WorkbenchDropdown($("agent-backend"), {
  label: "Agent 后端", onChange: (value) => { modelDraft().backend = value; if (value === "standard") modelDraft().auth_mode = "api_key"; renderModelSettings(); },
});

const authOptions = [{value: "api_key", text: "API key"}, {value: "codex_login", text: "本地 Codex 登录"}];
const authDropdown = new WorkbenchDropdown($("auth-mode"), {
  label: "认证方式", onChange: (value) => { modelDraft().auth_mode = value; modelDraft().api_key = ""; renderModelSettings(); },
});

function isSingleRun(run) {
  return run.collaboration?.mode === "pass1" ||
    (run.collaboration?.max_agents ?? run.brief?.limits?.max_agents) === 1;
}
function primaryAgent(run) {
  return (run.agents || []).find((agent) => !["rsi", "interaction"].includes(agent.agent_id))?.agent_id || "rsi";
}
function collaborationSelection(value) {
  return {mode: value.mode, modules: [...(value.modules || [])].sort(), max_agents: value.max_agents ?? 1};
}
function createCollaboration(value) {
  return {mode: "passk", modules: [], max_agents: value?.max_agents ?? 1};
}
function renderCreateCollaboration() {
  const development = !!state.workspaceReady;
  $("create-description").textContent = "描述要完成的事情、需要遵守的要求，以及怎样算完成。";
  $("create-message-label").textContent = "你想完成什么";
  $("create-message-hint").textContent = development ? "（必填）" : "（可稍后聊）";
  $("create-message").required = development;
  $("create-start-hint").textContent = "先确认任务和完成标准，再开始执行；你可以随时补充。";
  if (!state.creating) $("create-submit").textContent = "创建任务";
}
function renderCollaboration() {
  const saved = state.run?.collaboration;
  $("collaboration-settings").hidden = !saved?.mode;
  if (!saved?.mode) return;
  $("collaboration-summary").textContent = saved.mode === "passk"
    ? "pass@k" + (saved.modules?.length ? " · 原有协作配置" : "")
    : "历史任务（仅供查看）";
  $("collaboration-description").textContent = saved.modules?.length
    ? "此任务保留创建时的协作配置。"
    : "研究员独立完成任务，支持人工指导。";
  $("collaboration-status").textContent = "";
  $("reuse-collaboration").hidden = saved.supported === false;
}
$("reuse-collaboration").addEventListener("click", () => openCreate("", state.run.collaboration));
const effortLabels = {
  none: "无推理 · none", low: "低 · low", medium: "中 · medium",
  high: "高 · high", xhigh: "很高 · xhigh", max: "最高 · max",
};

function modelDraft() {
  if (!state.modelDrafts.has(state.runId)) {
    const saved = state.run?.model_settings || {};
    state.modelDrafts.set(state.runId, {
      base_url: saved.base_url || "", model: saved.model || "",
      reasoning_effort: saved.reasoning_effort || "", backend: saved.backend || "standard", auth_mode: saved.auth_mode || "api_key", api_key: "",
    });
  }
  return state.modelDrafts.get(state.runId);
}

function modelSettingsChanged() {
  const saved = state.run?.model_settings;
  const draft = state.modelDrafts.get(state.runId);
  return !!saved?.editable && !!draft &&
    (!!draft.api_key || ["base_url", "model", "reasoning_effort", "backend", "auth_mode"].some((key) => draft[key] !== (saved[key] || (key === "backend" ? "standard" : key === "auth_mode" ? "api_key" : ""))));
}

function renderModelSettings() {
  const saved = state.run?.model_settings;
  $("model-settings").hidden = !saved?.model;
  if (!saved?.model) return;
  if (state.modelFormRun !== state.runId) {
    state.modelFormRun = state.runId;
    $("model-settings").open = saved.editable;
  }
  if (!saved.editable) state.modelDrafts.delete(state.runId);
  const draft = saved.editable ? (state.modelDrafts.get(state.runId) || saved) : saved;
  const disabled = !saved.editable || !!state.modelSavingRun;
  $("model-settings-summary").textContent = `${saved.backend || "standard"}${saved.auth_mode === "codex_login" ? " · 本地登录" : ""} · ${saved.model} · ${saved.reasoning_effort}${saved.editable ? "" : " · 已锁定"}`;
  if ($("model-base-url").value !== draft.base_url) $("model-base-url").value = draft.base_url;
  $("model-base-url").disabled = disabled;
  $("model-name").value = draft.model;
  $("model-name").disabled = disabled;
  $("model-api-key").value = draft.api_key || "";
  $("model-api-key").disabled = disabled;
  $("model-api-key").placeholder = saved.has_credentials ? "已配置 · 留空沿用当前密钥" : "请输入 API Key";
  const localLogin = draft.auth_mode === "codex_login";
  authDropdown.update(authOptions.map((item) => ({...item, disabled: item.value === "codex_login" && (draft.backend || "standard") === "standard"})), draft.auth_mode || "api_key", disabled);
  for (const id of ["model-base-url", "model-api-key"]) {
    $(id).closest("label").hidden = localLogin;
    $(id).disabled = disabled || localLogin;
  }
  const models = state.models.map((model) => ({ value: model.id, text: model.id }));
  if (!models.some((model) => model.value === draft.model))
    models.push({ value: draft.model, text: draft.model, disabled: true });
  modelDropdown.update(models, draft.model, disabled);
  backendDropdown.update(backendOptions, draft.backend || "standard", disabled);
  const efforts = [...(state.models.find((model) => model.id === draft.model)?.efforts || Object.keys(effortLabels))];
  if (!efforts.includes(draft.reasoning_effort)) efforts.push(draft.reasoning_effort);
  effortDropdown.update(efforts.map((value) => ({ value, text: effortLabels[value] || value })), draft.reasoning_effort, disabled);
  $("save-model-settings").hidden = !saved.editable;
  $("save-model-settings").disabled = disabled || !modelSettingsChanged();
  $("save-model-settings").textContent = state.modelSavingRun === state.runId ? "保存中…" : "保存设置";
  $("model-settings-status").textContent = !saved.editable
    ? "研究已开始，设置已锁定。"
    : modelSettingsChanged() ? "有未保存的修改，请保存后继续对话或开始研究。"
    : "用于本研究的 Interaction、RSI 和 Research Agents；开始研究后锁定。";
}

for (const [id, field] of [["model-name", "model"], ["model-api-key", "api_key"]]) {
  $(id).addEventListener("input", () => { modelDraft()[field] = $(id).value; renderModelSettings(); });
}
$("model-base-url").addEventListener("input", () => {
  modelDraft().base_url = $("model-base-url").value;
  renderModelSettings();
});
$("model-settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.run?.model_settings?.editable || state.modelSavingRun || !modelSettingsChanged()) return;
  const runId = state.runId;
  const values = { ...modelDraft() };
  state.modelSavingRun = runId;
  renderModelSettings();
  notice();
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/model-settings`, values);
    state.modelDrafts.delete(runId);
    if (state.runId === runId) { await refresh(true); notice("模型设置已保存。"); }
  } catch (error) {
    if (state.runId === runId) { notice(error.message); await refresh(true); }
  } finally {
    state.modelSavingRun = null;
    renderModelSettings();
  }
});
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
  completed: "已完成",
  cancelled: "已取消",
  created: "已创建",
  starting: "启动中",
  timed_out: "已超时",
  lost: "执行失联",
};
const eventNames = {
  run_created: "创建研究",
  run_started: "研究开始",
  goal_updated: "研究进度更新",
  run_paused: "研究暂停",
  run_resumed: "研究继续",
  run_stopped: "研究停止",
  run_reopened: "恢复研究",
  agent_created: "建立研究方向",
  assignment_created: "下发研究任务",
  report_submitted: "提交研究报告",
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
  model_retry: "API 暂不可用，自动重试",
  model_recovered: "API 已恢复，继续任务",
  brief_updated: "更新研究需求",
  environment_updated: "更新执行环境",
  research_ready: "研究准备完成",
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
  const date = new Date(timestamp(value));
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
function timestamp(value) {
  return typeof value === "number" && value < 1e12 ? value * 1000 : value;
}
function duration(start, end) {
  if (!start) return "尚未开始";
  const seconds = Math.max(0, Math.floor(((end ? new Date(timestamp(end)).getTime() : Date.now())
    - new Date(timestamp(start)).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}
function elapsed(start, end, className = "") {
  const text = element("span", className, duration(start, end));
  if (start) {
    text.dataset.elapsedStart = String(start);
    if (end) text.dataset.elapsedEnd = String(end);
  }
  return text;
}
function nodeTimes(run, node) {
  const budget = run.budget?.researchers?.[node.agent_id];
  if (node.goal?.status === "completed") return [budget?.started_at || node.created_at || node.assignment?.created_at, node.goal.updated_at || node.updated_at || run.finished_at];
  if (budget?.started_at) return [budget.started_at, budget.finished_at || run.finished_at];
  const start = node.created_at || node.assignment?.created_at;
  const end = (!activeNode(node) ? node.updated_at || node.report?.created_at : null) || run.finished_at;
  return [start, end];
}
function nodeReports(node) {
  return node.reports?.length ? node.reports : node.report ? [node.report] : [];
}
function nodeScore(run, node) {
  return `${score(node.verification.score)}${run.brief?.verification?.metric === "speedup" ? "×" : ""}`;
}
function tickElapsed() {
  for (const item of document.querySelectorAll("[data-elapsed-start]")) {
    const parse = (value) => value && /^\d+(\.\d+)?$/.test(value) ? Number(value) : value;
    item.textContent = duration(parse(item.dataset.elapsedStart), parse(item.dataset.elapsedEnd));
  }
  for (const item of document.querySelectorAll("[data-stale-after]")) {
    const at = Number(item.dataset.staleAfter);
    item.hidden = !Number.isFinite(at) || Date.now() <= at;
  }
}
const activeJob = (job) => ["pending", "queued", "starting", "running"].includes(job.status);
const activeNode = (node) => node.goal?.status !== "completed" && ["pending", "running"].includes(node.state);
const phaseName = (phase) => ({search: "迭代验证", final: "最终验证", diagnostic: "诊断检查"}[phase] || "验证");
function jobTitle(job) {
  return job.is_baseline ? "起点评测" : job.title || job.description
    || (job.verification ? phaseName(job.phase) : "研究执行");
}
function progressText(job) {
  const p = job.progress;
  return p?.total > 0 ? `${p.completed} / ${p.total} ${p.unit || "项"}已完成` : "";
}
function progressBar(job) {
  const p = job.progress;
  if (!(p?.total > 0)) return element("span");
  const bar = element("div", "job-progress-bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-label", `${jobTitle(job)}完成进度`);
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(p.total));
  bar.setAttribute("aria-valuenow", String(p.completed));
  const fill = element("span");
  fill.style.width = `${100 * p.completed / p.total}%`;
  bar.append(fill);
  return bar;
}
function progressFreshness(job) {
  const updated = job.progress?.updated_at;
  if (!updated) return activeJob(job) ? "等待执行器上报细项进度" : "";
  const age = Date.now() - new Date(timestamp(updated)).getTime();
  return `${age > 45000 && activeJob(job) ? "进度暂未更新 · " : "进度更新于 "}${time(updated)}`;
}
function agentAction(run, agent) {
  if (agent.agent_id === "rsi" && run.collaboration?.mode === "passk" && !run.collaboration.rsi_model_enabled) return "代码调度";
  if ((run.human?.paused_agents || []).includes(agent.agent_id)) return "已暂停，等待人工继续";
  const activity = run.agent_activity?.[agent.agent_id];
  if (activity?.type === "model_retry") {
    const at = time(activity.next_retry_at * 1000);
    return `API 暂不可用（${activity.error_code}），已失败 ${activity.attempt} 次；预计 ${at} 自动重试，当前进度已保存`;
  }
  if (activity?.type === "model_attempt" && activity.attempt > 1)
    return `正在第 ${activity.attempt} 次尝试连接 API，等待模型响应`;
  const active = Object.values(activity?.active_tools || {});
  if (active.length || activity?.type === "tool_started")
    return `${toolName(active.at(-1)?.name || activity.tool)}${active.length > 1 ? ` · ${active.length} 项进行中` : ""}`;
  if (activity?.api_available === false) return "模型服务暂不可用，等待自动重试";
  return activity?.type === "tool_completed" ? "操作已结束，正在整理结果" : "正在分析任务并组织回复";
}
const toolNames = {exec_command: "执行命令", write_stdin: "等待命令结果", apply_patch: "修改代码",
    read_file: "读取文件", write_file: "写入文件", update_goal: "更新研究进度",
    request_user_input: "向你确认需求", ask_user: "向你提问",
    read: "读取文件", grep: "检索代码", glob: "查找文件", bash: "执行命令",
    write: "写入文件", edit: "修改文件", spawn_researcher: "安排研究任务",
    get_state: "查看研究进展", list_records: "查找研究记录", read_record: "阅读研究记录",
    save_candidate: "保存候选版本", checkout_candidate: "切换候选版本",
    verify_candidate: "提交验证", run_job: "运行实验", wait_for_job: "等待实验结果",
    job_status: "查看作业状态", read_job_output_file: "阅读实验反馈",
    save_evidence: "记录研究发现", share_evidence: "共享研究发现",
    update_plan: "调整研究计划", assign: "安排下一轮任务", message_researcher: "补充研究要求",
    wait: "等待后续消息", submit_report: "提交本轮报告"};
function toolName(name) {
  const short = (name || "").split(/[.:/]/).at(-1);
  return toolNames[short] || (name ? `运行工具（${name}）` : "处理任务");
}

function selectWork(run, id) {
  state.selected.set(run.run_id, id);
  localStorage.setItem(`rsi.workbench.node.${run.run_id}`, id);
  switchTab("node");
  renderGraph(run);
  renderNode(run);
  if (window.matchMedia("(max-width: 720px)").matches)
    $("node-panel").closest(".inspector").scrollIntoView({block: "start"});
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
async function api(path, body, signal, retryAuth = true) {
  return (await requestResponse(path, body, signal, retryAuth)).json();
}
async function requestResponse(path, body, signal, retryAuth = true) {
  const options = {
    headers: {},
    cache: "no-store",
    signal,
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
    if (response.status === 403 && error.code === "csrf_expired" && body !== undefined && retryAuth) {
      state.token = (await api("/api/bootstrap", undefined, signal)).csrf_token;
      return requestResponse(path, body, signal, false);
    }
    throw new Error(error.error || `请求失败（${response.status}）`);
  }
  return response;
}

async function downloadArtifact(action, params = {}) {
  try {
    const response = await requestResponse(`/api/runs/${encodeURIComponent(state.runId)}/${action}`, params);
    const url = URL.createObjectURL(await response.blob());
    const link = element("a"); link.href = url; link.download = `${state.runId}-${params.job_id || action}.zip`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { notice(error.message); }
}
$("export-candidate").addEventListener("click", () => downloadArtifact("export"));

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
  const groups = new Map();
  for (const run of state.runs) {
    const key = run.group?.id ? `group:${run.group.id}` : "ungrouped";
    if (!groups.has(key)) groups.set(key, {
      key, name: run.group?.name || "未分组", createdAt: run.group?.created_at || "", runs: [],
    });
    groups.get(key).runs.push(run);
  }
  const ordered = [...groups.values()].sort((a, b) =>
    (b.createdAt || "\uffff").localeCompare(a.createdAt || "\uffff"));
  for (const id of ["run-list", "mobile-run-list"]) {
    replace(id, ...(ordered.length
      ? ordered.map(renderRunGroup)
      : [element("p", "muted", "还没有研究记录")]));
  }
  const current = state.runs.find((run) => run.run_id === state.runId);
  $("mobile-run-name").textContent = current?.objective || "选择研究";
}

function renderRunGroup(group) {
  const details = element("details", "run-group");
  details.dataset.group = group.key;
  const storageKey = `rsi.workbench.group.${group.key}`;
  if (!state.groupExpansion.has(group.key))
    state.groupExpansion.set(group.key, localStorage.getItem(storageKey) === "open");
  details.open = state.groupExpansion.get(group.key);
  const summary = element("summary");
  summary.append(element("span", "run-group-name", group.name));
  summary.append(element("span", "run-group-count", String(group.runs.length)));
  details.classList.toggle("has-selected", group.runs.some((run) => run.run_id === state.runId));
  details.addEventListener("toggle", () => {
    if (!details.isConnected || state.groupExpansion.get(group.key) === details.open) return;
    state.groupExpansion.set(group.key, details.open);
    localStorage.setItem(storageKey, details.open ? "open" : "closed");
    for (const peer of document.querySelectorAll(".run-group"))
      if (peer.dataset.group === group.key) peer.open = details.open;
  });
  const items = element("div", "run-group-items");
  for (const run of group.runs) {
    const button = element(
      "button",
      `run-item ${run.run_id === state.runId ? "selected" : ""}`,
    );
    button.title = run.objective || "未命名研究";
    if (run.run_id === state.runId) button.setAttribute("aria-current", "true");
    button.append(element("span", "run-indicator"));
    const body = element("span");
    body.append(element("span", "run-name", run.objective || "未命名研究"));
    body.append(
      element(
        "small",
        "",
        label(run.status),
      ),
    );
    button.append(body);
    button.addEventListener("click", () => {
      mobileRunPicker.open = false;
      selectRun(run.run_id);
    });
    items.append(button);
  }
  details.append(summary, items);
  return details;
}
async function selectRun(runId) {
  if (state.deleting) return;
  if (state.runId === runId && state.run) return;
  if (state.runId) state.drafts.set(state.runId, $("chat-input").value);
  state.runId = runId;
  $("chat-input").value = state.drafts.get(runId) || "";
  state.refreshController?.abort();
  state.run = state.runCache.get(runId) || null;
  state.events = state.eventCache.get(runId) || new Map();
  state.eventCache.set(runId, state.events);
  state.graphSignature = state.detailSignature = state.messageSignature = "";

  localStorage.setItem("rsi.workbench.run", runId);
  const url = new URL(window.location.href);
  url.searchParams.set("run", runId);
  window.history.replaceState(null, "", url);
  const saved = localStorage.getItem(`rsi.workbench.node.${runId}`);
  if (saved) state.selected.set(runId, saved);
  notice();
  renderRuns();
  $("workspace").hidden = !state.run;
  $("welcome").hidden = true;
  $("run-loading").hidden = !!state.run;
  if (state.run) render();
  await refresh(true);
}

async function refresh(force = false) {
  if (!state.runId || state.deleting || (state.polling && !force)) return;
  const runId = state.runId;
  const sequence = ++state.refreshSequence;
  state.refreshController?.abort();
  const controller = new AbortController();
  state.refreshController = controller;
  state.polling = true;
  try {
    const after = state.cursors.get(runId) || 0;
    const run = await api(
      `/api/runs/${encodeURIComponent(runId)}?after=${after}&event_limit=100`, undefined, controller.signal,
    );
    if (state.deleting || state.runId !== runId || sequence < state.latestAppliedSequence) return;
    state.latestAppliedSequence = sequence;
    if (Number.isInteger(run.event_cursor) && run.event_cursor < after) {
      state.events.clear();
      state.goalHistory.delete(runId);
    }
    for (const event of run.events || [])
      state.events.set(event.sequence, event);
    while (state.events.size > 100)
      state.events.delete(state.events.keys().next().value);
    state.cursors.set(runId, run.event_cursor ?? after);
    mergeGoalTurns(run);
    state.run = run;
    state.runCache.set(runId, run);
    // Keep a bounded set of full projections, retaining independent event cursors.
    if (state.runCache.size > 12) {
      const oldest = [...state.runCache.keys()].find((key) => key !== runId);
      state.runCache.delete(oldest);
      state.goalHistory.delete(oldest);
    }
    $("run-loading").hidden = true;
    if ($("run-loading").dataset.failed) { notice(); delete $("run-loading").dataset.failed; }
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
    const selectedNode = run.nodes?.find((n) => n.iteration_id === state.selected.get(runId));
    if (selectedNode && state.tab === "node") await loadAgentTrace(runId, selectedNode.agent_id);
    else if (state.tab === "chat" && state.traceAvailable) {
      const recipient = renderReplyTarget(run).recipient;
      const agents = new Set([recipient]);
      if (!state.agentTraces.get(`${runId}/interaction`)?.ready
          || run.agents?.some((agent) => agent.agent_id === "interaction" && agent.state === "running")) agents.add("interaction");
      await Promise.all([...agents].map((agentId) => loadAgentTrace(runId, agentId)));
    }
    connected(true);
  } catch (error) {
    if (error.name === "AbortError" || state.runId !== runId) return;
    connected(false);
    if (!state.run) {
      $("run-loading").hidden = true;
      $("run-loading").dataset.failed = "true";
      notice(`研究记录加载失败：${error.message}。正在自动重试，也可重新选择研究。`);
    } else if (force) notice(error.message);
  } finally {
    if (state.refreshController === controller) state.polling = false;
  }
}

function render() {
  const run = state.run;
  if (!run) return;
  $("workspace-target").hidden = !run.workspace_dir;
  $("workspace-target").textContent = run.workspace_dir
    ? [`源 Git 仓库：${run.workspace_dir}`,
      ...Object.entries(run.worktree_dirs || {}).map(([agent, path]) =>
        `${agent} 工作区：${path}`)].join("\n") : "";
  renderModelSettings();
  renderCollaboration();
  renderGoal(run);
  renderGoalProgress(run);
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  $("objective").textContent =
    run.objective || run.brief?.objective || "未命名研究";
  $("run-short").textContent = run.run_id;
  $("workspace").classList.toggle("discussing", run.status === "planned");
  const needsResume = run.worker_alive === false && (
    run.status === "running" || (run.status === "planned" && run.pending_agents?.length)
  );
  $("run-status").textContent = run.status === "planned" && run.intake?.status === "discussing"
    && (!run.preparation || run.preparation.status === "pending") ? "确认需求中"
    : run.preparation && run.status === "planned"
    ? ({pending: "等待准备", running: "准备中", cancelling: "正在取消准备", ready: "可以开始", failed: "准备失败"}[run.preparation.status])
    : run.finalization?.status === "running" ? "最终复测与归档中"
    : run.finalization?.status === "failed" ? "收尾失败 · 可恢复"
    : run.finalization?.status === "completed" ? (run.status === "succeeded" ? "已达标" : run.brief?.verification?.target == null ? "已完成" : "已完成 · 未达标")
    : run.status === "running" && run.goal?.status === "blocked" ? "目标阻塞"
    : needsResume ? "待恢复"
    : run.status === "running" && run.execution_setup === "pending"
    ? "研究中 · 接入准备" : label(run.status);
  $("run-status").className = `status ${run.status}`;
  $("start").hidden = run.status !== "planned" || (run.intake?.status && run.intake.status !== "ready") || (run.preparation && run.preparation.status !== "ready");
  $("start").disabled = state.busy || run.agents?.some(
    (agent) => agent.agent_id === "interaction" && agent.state === "running",
  );
  $("pause").hidden = run.status !== "running";
  $("cancel-turn").hidden = !isSingleRun(run) || run.status !== "running";
  $("cancel-turn").disabled = state.busy;
  $("resume").hidden = run.status !== "paused" && !(run.status === "stopped" && run.started_at) && !needsResume;
  $("resume").hidden ||= ["running", "completed"].includes(run.finalization?.status)
    || (run.collaboration?.mode === "passk" && run.status === "stopped" && !run.finalization?.status);
  $("resume").textContent = run.finalization?.status === "failed" ? "恢复收尾" : run.status === "stopped" ? "恢复研究" : needsResume
    ? (run.status === "planned" ? "继续对话" : "恢复执行") : "继续";
  $("stop").hidden = !["planned", "running", "paused"].includes(run.status)
    && run.finalization?.status !== "running";
  $("stop").textContent = run.status === "planned" ? (run.preparation ? "放弃任务" : "结束对话") : "停止";
  if (run.collaboration?.supported === false) {
    for (const id of ["start", "pause", "resume"]) $(id).hidden = true;
  }
  $("delete-run").disabled = state.busy || state.sending.has(run.run_id) || !!state.modelSavingRun;
  $("delete-run").title = deletionBlocked(run) ? "请先停止研究再删除" : "删除这项研究的本地记录";
  $("best-score").textContent = score(run.best?.score);
  $("final-score").textContent = score(run.final?.score);
  $("export-candidate").hidden = !(run.final?.candidate_id || run.best?.candidate_id);
  const researchers = ResearchGraph.researchers(run);
  const pass1 = isSingleRun(run);
  $("agent-count-label").textContent = pass1 ? "执行 Agent" : "研究员";
  $("node-count").textContent = integer(pass1 ? 1 : researchers.length);
  $("research-graph-card").hidden = $("research-plan-card").hidden = pass1;
  $("runtime-turn-count").parentElement.hidden = pass1 || !!run.preparation;
  $("runtime-turn-count").textContent = integer(run.runtime_turns ?? run.budget?.runtime_turns ?? 0);
  $("plan").textContent =
    run.plan || "研究开始后，RSI 会在这里说明当前方向和下一步。";
  $("plan").classList.toggle("muted", !run.plan);
  $("score-metric").hidden = $("final-metric").hidden = !run.brief?.verification;
  $("metrics").classList.toggle("without-score", !run.brief?.verification);
  $("run-elapsed").replaceChildren(elapsed(run.started_at, run.finished_at));
  $("update-goal").hidden = !canGuide(run);
  $("update-goal").disabled = state.busy;
  $("human-status").textContent = run.collaboration?.mode === "passk" ? `目标 v${(run.brief?.revision || 0) + 1}${run.human?.assisted ? " · 人工指导" : ""}` : "";
  renderPreparation(run);
  renderIntake(run);
  renderOverview(run);
  renderDirections(run);
  renderGraph(run);
  renderMessages(run);
  renderNode(run);
  renderBrief(run);
  renderJobs(run);
  renderEvents();
  const target = renderReplyTarget(run);
  renderAgentErrors(run);
  const chatUnavailable = run.collaboration?.supported === false;
  const researchEnded = ["stopped", "succeeded", "completed", "failed"].includes(run.status);
  const fixedTask = run.preparation && run.status === "planned" && !run.intake?.status;
  const responding = (run.agents || []).find((agent) => agent.agent_id === target.recipient);
  $("chat-input").disabled = chatUnavailable || fixedTask;
  $("send-message").disabled = chatUnavailable || fixedTask || state.sending.has(run.run_id) || target.stale;
  $("chat-input-label").textContent = `向 ${agentName(target.recipient)} 发送消息`;
  $("chat-input").placeholder = target.reply_to
    ? "回答这个问题，或说明处理进展…"
    : "补充想法、提供环境，或询问进展…";
  $("chat-status").textContent = chatUnavailable
    ? "当前历史配置无法继续对话"
    : fixedTask
    ? "此研发任务按固定条件执行；请使用预检、启动、暂停或停止操作。"
    : target.stale
    ? target.text
    : state.chatErrors.has(run.run_id)
    ? `发送失败：${state.chatErrors.get(run.run_id)}`
    : run.status === "paused" && target.recipient !== "interaction"
    ? "研究已暂停，消息将在继续后处理"
    : state.sending.has(run.run_id)
    ? "正在发送…"
    : researchEnded
    ? "研究执行已结束，仍可与 Interaction Agent 对话"
    : responding?.state === "running"
      ? `${agentName(target.recipient)} 正在处理…`
      : "Enter 发送 · Shift+Enter 换行";
  renderChatFeedback(run, target, responding);
  if (run.budget_stop_reason) notice(run.budget_stop_reason);
}

function renderGoal(run) {
  const visible = isSingleRun(run);
  $("goal-context").hidden = !visible;
  if (!visible) return;
  const goal = run.goal || {};
  const status = ["planned", "paused", "stopped", "failed"].includes(run.status) ? run.status
    : ["succeeded", "completed"].includes(run.status) ? "completed" : goal.status || "active";
  $("goal-status").textContent = {active: "持续执行", blocked: "等待解除阻塞", completed: "目标完成", usageLimited: "等待额度恢复", budgetLimited: "原生预算已用完"}[status] || label(status);
  $("goal-status").className = `status ${status}`;
  $("goal-instructions").textContent = run.brief?.instructions || run.objective || "";
  $("goal-summary").textContent = goal.summary || "等待 Agent 整理执行上下文…";
  $("goal-current-step").textContent = goal.current_step || "尚未记录当前步骤";
  const activity = run.agent_activity?.[primaryAgent(run)];
  $("goal-activity").textContent = run.status === "running" && activity?.type === "tool_started" && activity.tool ? `正在执行：${activity.tool}` : "";

  for (const [field, empty] of [["next_steps", status === "completed" ? "目标已完成，无后续步骤" : status === "blocked" ? "等待补充条件后继续" : "等待整理下一步"], ["acceptance", "等待明确验收条件"], ["evidence", "尚无验收记录"]]) {
    const items = field === "acceptance" && !goal.acceptance?.length
      ? (run.brief?.acceptance || []) : Array.isArray(goal[field]) ? goal[field] : [];
    const container = $("goal-" + field.replaceAll("_", "-"));
    const signature = JSON.stringify([items, status]);
    if (container.dataset.signature !== signature) {
      container.replaceChildren(...(items.length ? items.map((item) => element("li", "", item)) : [element("li", "muted", empty)]));
      container.dataset.signature = signature;
    }
  }
  $("goal-blocker").hidden = !goal.blocker;
  $("goal-blocker").textContent = goal.blocker ? `阻塞原因：${goal.blocker}` : "";
  const guidance = (run.messages || []).filter((message) => message.sender === "user").at(-1);
  $("goal-guidance").textContent = guidance ? `最近用户指令：${guidance.text}` : "";
  const remaining = run.budget?.remaining_wall_seconds;
  $("goal-budget").textContent = `已用 ${integer(run.budget?.total_tokens || 0)} tokens · ` + (remaining == null ? "未设置时限" : `剩余 ${Math.ceil(remaining)} 秒`);
  $("goal-updated").textContent = goal.updated_at ? `更新于 ${new Date(goal.updated_at).toLocaleTimeString()}` : "等待进度更新";

}

function mergeGoalTurns(run, older = false) {
  if (!Array.isArray(run.goal_turns)) return;
  let history = state.goalHistory.get(run.run_id);
  if (!history) {
    history = {turns: new Map(), next: null, ready: false, total: 0, loading: false, error: ""};
    state.goalHistory.set(run.run_id, history);
  }
  const numbers = run.goal_turns.map((turn) => turn.number);
  if (!older && numbers.length && history.turns.size && Math.min(...numbers) > Math.max(...history.turns.keys()) + 1) {
    history.turns.clear(); history.ready = false;
  }
  for (const turn of run.goal_turns) history.turns.set(turn.number, turn);
  if (!history.ready || older) history.next = run.goal_turns_next ?? null;
  history.total = Math.max(history.total, run.goal_turns_total ?? history.turns.size);
  history.ready = true;
}

function renderPreparation(run) {
  const prep = run.preparation;
  $("preparation").hidden = !prep;
  const hideChat = !!prep && run.status === "planned" && !run.intake?.status;
  $("chat-tab").hidden = hideChat;
  if (hideChat && state.tab === "chat") switchTab("brief");
  if (!prep) return;
  const phase = `${run.run_id}/${run.status === "planned"}/${prep.status === "failed"}`;
  if ($("preparation").dataset.phase !== phase) {
    $("preparation").open = prep.status === "failed";
    $("preparation").dataset.phase = phase;
    if (run.status !== "planned") $("model-settings").open = $("collaboration-settings").open = false;
  }
  $("preparation-status").textContent = {pending: "等待准备", running: "准备中", cancelling: "取消中", ready: "已准备好", failed: "准备失败"}[prep.status];
  $("preparation-workspace").textContent = `工作区由系统准备。${prep.setup.wall_seconds == null ? "未设置运行时限。" : `运行时限 ${prep.setup.wall_seconds / 3600} 小时。`}`;
  $("preparation-checks").replaceChildren(...(prep.checks || []).map((check) => element("li", "", `${{pending: "等待", running: "检查中", passed: "通过", failed: "失败"}[check.status]} · ${check.name}：${check.message}`)));
  $("preparation-error").textContent = prep.error || "";
  $("prepare-run").hidden = run.status !== "planned";
  $("prepare-run").disabled = state.busy || ["running", "cancelling"].includes(prep.status);
  $("cancel-preparation").hidden = run.status !== "planned" || !["running", "cancelling"].includes(prep.status);
  $("cancel-preparation").disabled = state.busy || prep.status === "cancelling";
  $("prepare-run").textContent = prep.status === "pending" ? "准备任务" : "重新准备";
}
$("prepare-run").addEventListener("click", () => control("prepare"));
$("cancel-preparation").addEventListener("click", () => control("cancel"));

function renderGoalProgress(run) {
  const visible = isSingleRun(run);
  $("goal-history").hidden = !visible;
  if (!visible) return;
  const history = state.goalHistory.get(run.run_id);
  const turns = [...(history?.turns.values() || [])].sort((a, b) => a.number - b.number);
  const entries = turns.flatMap((turn) => [
    ...(turn.output || []).map((text) => ({text, error: false})),
    ...(turn.error ? [{text: turn.error, error: true}] : []),
  ]);
  const container = $("goal-turn-list");
  const signature = JSON.stringify([run.run_id, entries]);
  if (container.dataset.signature !== signature) {
    container.dataset.signature = signature;
    container.replaceChildren(...(entries.length
      ? entries.map((entry) => {
          const message = element("li", entry.error ? "progress-message error" : "progress-message");
          appendProgressText(message, entry.text);
          return message;
        })
      : [element("li", "muted", "尚无执行进展")]));
  }
  $("goal-load-older").hidden = history?.next == null;
  $("goal-load-older").disabled = !!history?.loading;
  $("goal-load-older").textContent = history?.loading ? "正在读取…" : "查看更早进展";
  $("goal-history-error").hidden = !history?.error;
  $("goal-history-error").textContent = history?.error || "";
}

function appendProgressText(container, text) {
  for (const section of text.split(/(```[\s\S]*?```)/g)) {
    if (section.startsWith("```")) { appendFormattedText(container, section); continue; }
    for (const paragraph of section.split(/\n\s*\n/).filter((part) => part.trim())) {
      const lines = paragraph.trim().split("\n");
      let block = null, list = null;
      for (const line of lines) {
        const bullet = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
        const heading = line.match(/^#{1,6}\s+(.+)$/);
        if (bullet) {
          const tag = bullet[1] ? "UL" : "OL";
          if (!list || list.tagName !== tag) { list = element(tag.toLowerCase()); container.append(list); }
          const item = element("li"); appendFormattedText(item, bullet[2]); list.append(item);
          block = null;
        } else {
          list = null;
          if (heading) { block = element("h3"); container.append(block); appendFormattedText(block, heading[1]); block = null; }
          else {
            if (!block) { block = element("p"); container.append(block); }
            else block.append(document.createTextNode("\n"));
            appendFormattedText(block, line);
          }
        }
      }
    }
  }
}

$("goal-load-older").addEventListener("click", async () => {
  const runId = state.runId, history = state.goalHistory.get(runId);
  if (!history || history.loading || history.next == null) return;
  history.loading = true; history.error = ""; renderGoalProgress(state.run);
  try {
    const page = await api(`/api/runs/${encodeURIComponent(runId)}?goal_before=${history.next}&after=${state.cursors.get(runId) || 0}&event_limit=1`);
    if (state.goalHistory.get(runId) === history) mergeGoalTurns(page, true);
  } catch (error) { history.error = error.message; }
  finally { history.loading = false; if (state.runId === runId) renderGoalProgress(state.run); }
});

function agentName(id) {
  return {rsi: "RSI Agent", interaction: "Interaction Agent"}[id]
    || state.run?.agents?.find((agent) => agent.agent_id === id)?.direction || "Research Agent";
}
function questionPreview(question, length = 48) {
  const text = (question.question || question.text || "请查看对话").replace(/\s+/g, " ");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
function renderReplyTarget(run) {
  const active = ["planned", "running", "paused"].includes(run.status);
  const questions = (run.waiting_for_user || []).filter((q) =>
    q.message_id && (active || q.agent_id === "interaction"));
  const choices = [
    ...questions.map((q) => ({
      value: q.message_id,
      text: `回复 ${agentName(q.agent_id)} · ${questionPreview(q, 24)}`,
      recipient: q.agent_id,
      reply_to: q.message_id,
    })),
    ...(run.started_at ? [] : [{ value: "interaction", recipient: "interaction", text: "Interaction Agent · 新消息" }]),
    ...(active && run.started_at && run.collaboration?.rsi_model_enabled
      ? [{ value: "rsi", recipient: "rsi", text: "RSI Agent · 新消息" }] : []),
  ];
  if (active && run.collaboration?.mode === "passk" && run.started_at)
    choices.push(...(run.agents || []).filter((a) => a.agent_id !== "rsi" && a.agent_id !== "interaction" && a.state !== "stopped").map((a) => ({value: a.agent_id, recipient: a.agent_id, text: `${agentName(a.agent_id)} · 定向指导`})));
  if (run.started_at) choices.push({value: "interaction", recipient: "interaction", text: "Interaction Agent · 对话"});
  if (!choices.length) choices.push({value: "interaction", recipient: "interaction", text: "任务已结束"});
  let selected = state.replyTargets.get(run.run_id);
  const hasDraft = !!$("chat-input").value.trim();
  if (!selected || (!hasDraft && !choices.some((c) => c.value === selected.value)))
    selected = { value: choices[0].value, explicit: false };
  if (!selected.explicit && !hasDraft) selected.value = choices[0].value;
  state.replyTargets.set(run.run_id, selected);
  let target = choices.find((c) => c.value === selected.value);
  if (!target) {
    // Never silently redirect a draft if another browser already answered its question.
    target = { value: selected.value, recipient: "interaction", stale: true,
      text: active ? "原接收者已不可用，请重新选择" : "研究已结束，请改选 Interaction Agent",
      disabled: true };
    choices.unshift(target);
  }
  recipientDropdown.update(choices, selected.value,
    state.sending.has(run.run_id) || run.collaboration?.supported === false);
  const pending = $("pending-questions");
  pending.hidden = !questions.length;
  const questionSignature = JSON.stringify([run.run_id, questions, selected.value]);
  if (pending.dataset.signature !== questionSignature) {
    pending.replaceChildren(element("strong", "", "等待你的回复"), ...questions.map((q) => {
      const button = element("button", "pending-question",
        `${agentName(q.agent_id)} · ${questionPreview(q)}`);
      button.type = "button";
      button.title = q.question || q.text;
      button.setAttribute("aria-pressed", String(selected.value === q.message_id));
      button.addEventListener("click", () => {
        state.replyTargets.set(run.run_id, { value: q.message_id, explicit: true });
        render();
        $("chat-input").focus();
      });
      return button;
    }));
    pending.dataset.signature = questionSignature;
  }
  return target;
}

function renderAgentErrors(run) {
  const failures = (run.agents || []).filter((agent) =>
    ["rsi", "interaction"].includes(agent.agent_id) && agent.state === "failed");
  const container = $("agent-errors");
  container.hidden = !failures.length;
  const signature = JSON.stringify([run.run_id, failures, run.status]);
  if (container.dataset.signature === signature) return;
  container.replaceChildren(...failures.map((agent) => {
    const block = element("div", "agent-error");
    block.append(element("strong", "", `${agentName(agent.agent_id)} 暂时无法继续`),
      element("p", "", agent.error || "调用失败，请检查模型服务与网络。"));
    const retry = element("button", "secondary", "处理后重试");
    retry.disabled = run.collaboration?.supported === false || !["planned", "running"].includes(run.status);
    retry.addEventListener("click", () => control("retry", { agent_id: agent.agent_id }));
    block.append(retry);
    return block;
  }));
  container.dataset.signature = signature;
}

function renderIntake(run) {
  const preparing = run.status === "planned" && (!!run.intake?.status || !run.preparation);
  $("intake-overview").hidden = !preparing;
  $("node-tab").hidden = preparing && !run.nodes?.length;
  $("intake-status").textContent = run.intake?.status === "ready" ? "待你确认" : "整理中";
  $("intake-summary").textContent = run.intake?.status === "ready"
    ? run.intake.summary
    : run.brief?.instructions || "先和 Interaction 讨论目标，它会在这里整理研究要求。";
  $("intake-environment").textContent = run.intake?.environment || "";
  const failed = run.agents?.find((agent) => agent.agent_id === "interaction" && agent.state === "failed");
  const items = [];
  if (failed) {
    items.push(element("p", "error", failed.error || "Interaction 暂时未能完成回复。"));
    const retry = element("button", "secondary", "重试回复");
    retry.addEventListener("click", () => control("retry", {agent_id: "interaction"}));
    items.push(retry);
  }
  replace("intake-retry", ...items);
}

function agentStatus(run, agent) {
  if (agent.agent_id === "rsi" && run.status === "running" && !run.collaboration?.rsi_model_enabled) return "自动调度";
  const goal = run.goals?.[agent.agent_id];
  if (goal?.status === "completed") return "已完成";
  if (goal?.status === "blocked") return "等待补充条件";
  if (agent.state !== "waiting") return label(agent.state);
  if (agent.wait_reason === "user") return "等待你回复";
  if (agent.wait_reason === "rsi_reply") return "已报告 · 等待 RSI 指令";
  if ((run.jobs || []).some((job) => activeJob(job) && (job.agent_id === agent.agent_id
    || `job:${job.job_id}` === agent.wait_reason))) return "等待作业结果";
  return "等待后续消息";
}
function renderOverview(run) {
  $("live-overview").hidden = run.status === "planned";
  const jobs = (run.jobs || []).filter(activeJob);
  const busy = (run.agents || []).filter((agent) => agent.state === "running");
  const waiting = (run.waiting_for_user || []).length;
  const heading = element("div", "card-heading");
  heading.append(element("h2", "", "当前进展"), element("span", "live-summary",
    [jobs.length ? `${jobs.length} 个待完成作业` : "", busy.length ? `${busy.length} 个 Agent 处理中` : ""].filter(Boolean).join(" · ") || "暂无执行活动"));
  const blocks = [heading];
  if (run.worker_alive === false && run.status === "running")
    blocks.push(element("p", "live-warning", "调度已断开，请恢复执行以继续接收结果。"));
  if (run.status === "paused") blocks.push(element("p", "live-warning", "研究已暂停。继续后恢复任务调度。"));
  if (waiting) blocks.push(element("p", "live-warning", `${waiting} 个问题等待你回复，请查看需求与对话。`));
  if (run.execution_blocker?.job_id && !jobs.length)
    blocks.push(element("p", "live-warning", `执行环境需要处理：${run.execution_blocker.error || "验证未能运行"}。已有研究记录保留。`));
  for (const job of jobs) {
    const card = element("button", "live-task");
    card.dataset.work = `job:${job.job_id}`;
    const title = element("div", "live-title");
    title.append(element("h3", "", jobTitle(job)), badge(job.status));
    card.append(title,
      element("p", "live-stage", job.progress?.stage || ({queued: "等待执行资源", pending: "等待调度", starting: "启动执行器"}[job.status] || "执行器正在运行")),
      element("p", "live-description", job.progress?.message || job.progress_message || "等待执行器上报细项进度"),
      progressBar(job));
    const meta = element("div", "live-meta");
    meta.append(element("span", "", progressText(job)),
      element("span", "", `${job.agent_name || agentName(job.agent_id)} · ${job.started_at ? `已运行 ${duration(job.started_at, job.finished_at)}` : "等待启动"}`),
      element("span", "", progressFreshness(job)), element("span", "live-link", "查看作业 →"));
    card.append(meta);
    card.addEventListener("click", () => selectWork(run, `job:${job.job_id}`));
    blocks.push(card);
  }
  for (const agent of busy) {
    const node = run.nodes?.find((n) => n.iteration_id === agent.current_iteration_id);
    const line = element(node ? "button" : "div", "live-agent");
    line.append(element("strong", "", `${agentName(agent.agent_id)} · ${agentAction(run, agent)}`));
    if (node) {
      if (!run.preparation)
        line.append(element("p", "", (node.title || node.assignment?.instruction || "执行本轮研究任务").slice(0, 160)));
      line.dataset.work = node.iteration_id;
      line.addEventListener("click", () => selectWork(run, node.iteration_id));
    }
    if (run.preparation) {
      const latest = [...state.events.values()].filter((event) => event.kind === "assistant_output" && event.agent_id === agent.agent_id).at(-1);
      if (latest?.data?.text) line.append(element("p", "live-description", latest.data.text.slice(0, 240)));
    }
    blocks.push(line);
  }
  if (!jobs.length && !busy.length) {
    const rsi = run.agents?.find((a) => a.agent_id === "rsi");
    const latest = [...state.events.values()].at(-1);
    blocks.push(element("p", "live-idle", waiting ? "等待你的补充，再继续推进研究。"
      : run.status !== "running" ? label(run.status)
      : rsi?.error || (rsi?.wait_reason && !["message", "user", "rsi_reply"].includes(rsi.wait_reason)
        ? `当前没有作业或模型调用。RSI 的等待说明：${rsi.wait_reason}`
        : "当前没有作业或模型调用；Agent 正在等待后续消息。")));
    if (latest) blocks.push(element("p", "live-last muted", `最近动态：${eventNames[latest.kind] || latest.kind} · ${time(latest.created_at)}`));
  }
  const rsi = run.agents?.find((a) => a.agent_id === "rsi");
  if (jobs.length && rsi?.state === "waiting")
    blocks.push(element("p", "live-next", "下一步 · 作业完成后将结果交回 Agent，RSI 据此安排后续研究。"));
  const focus = document.activeElement?.dataset.work;
  const scroll = $("live-overview").scrollTop;
  replace("live-overview", ...blocks);
  $("live-overview").scrollTop = scroll;
  if (focus) [...$("live-overview").querySelectorAll("[data-work]")]
    .find((el) => el.dataset.work === focus)?.focus({preventScroll: true});
}
function renderDirections(run) {
  replace(
    "directions",
    ...(run.agents || [])
      .filter((agent) => agent.agent_id)
      .map((agent) => {
        const item = element(
          "span",
          `direction ${agent.state}`,
          `${agentName(agent.agent_id)} · ${agentStatus(run, agent)}`,
        );
        item.title = agent.error || agent.wait_reason || "";
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

function graphAgentName(run, id) {
  if (id === "rsi") return "RSI Agent";
  const index = ResearchGraph.researchers(run).indexOf(id);
  return index >= 0 ? `Researcher ${index + 1}` : agentName(id);
}

function renderGraph(run) {
  const model = ResearchGraph.model(run, {allJobs: state.graphAllJobs, collaborationOnly: state.graphCollaborationOnly});
  const layout = ResearchGraph.layout(model);
  const researchers = ResearchGraph.researchers(run);
  const sharing = (run.communications || []).filter((m) => m.sender === "rsi" && ResearchGraph.sharedFrom(m).length);
  $("graph-count").textContent = `${researchers.length} 名研究员 · ${sharing.length} 次有来源的共享 · ${run.jobs?.length || 0} 个作业`;
  $("graph-jobs").textContent = state.graphAllJobs ? "收起普通作业" : "显示全部作业";
  $("graph-jobs").setAttribute("aria-pressed", String(state.graphAllJobs));
  $("graph-sharing").setAttribute("aria-pressed", String(state.graphCollaborationOnly));
  const selected = state.selected.get(run.run_id);
  const signature = JSON.stringify([run.run_id, run.nodes, run.jobs, run.communications, run.edges,
    run.agents, run.agent_activity, run.budget?.researchers, selected, state.graphAllJobs, state.graphCollaborationOnly]);
  if (signature === state.graphSignature) return;
  state.graphSignature = signature;
  const viewport = $("graph-viewport"), canvas = $("graph-canvas");
  let scroll = {left: viewport.scrollLeft, top: viewport.scrollTop};
  if (state.graphRunId !== run.run_id) {
    try { scroll = JSON.parse(localStorage.getItem(`rsi.workbench.viewport.v2.${run.run_id}`)) || {left: 0, top: 0}; }
    catch { scroll = {left: 0, top: 0}; }
    state.graphRunId = run.run_id;
    const saved = Number(localStorage.getItem(`rsi.workbench.zoom.v2.${run.run_id}`));
    state.graphZoom = saved > 0 ? Math.max(.2, Math.min(2, saved)) : Math.min(1, Math.max(.8, (viewport.clientWidth || layout.width) / layout.width));
  }
  const focused = document.activeElement?.dataset.node;
  canvas.replaceChildren();
  $("graph-column-headings").replaceChildren();
  $("graph-column-headings").hidden = !model.lanes.length;
  canvas.hidden = !model.lanes.length;
  $("graph-empty").hidden = !!model.lanes.length;
  if (!model.lanes.length) {
    $("graph-surface").style.width = $("graph-surface").style.height = "0px";
    $("graph-detail").hidden = true;
    $("graph-active").hidden = true;
    return;
  }
  const palette = ["#788296", "#547b6b", "#527f9b", "#9c7843", "#87709d", "#a96e74"];
  const shortName = (id) => id === "rsi" ? "RSI" : `R${researchers.indexOf(id) + 1}`;
  const laneX = (id) => layout.left + model.lanes.indexOf(id) * layout.laneWidth;
  model.lanes.forEach((id, index) => {
    const owner = run.agents?.find((a) => a.agent_id === id);
    const nodes = (run.nodes || []).filter((n) => n.agent_id === id);
    const node = nodes.at(-1);
    const lane = element("div", "agent-lane");
    lane.dataset.agent = id;
    Object.assign(lane.style, {left: `${laneX(id)}px`, width: `${layout.laneWidth - 10}px`, height: `${layout.height - 12}px`});
    const header = element("div", "agent-lane-heading");
    header.dataset.x = laneX(id);
    header.dataset.width = layout.laneWidth - 10;
    header.style.setProperty("--agent-color", palette[index % palette.length]);
    header.append(element("span", "agent-monogram", shortName(id)), element("strong", "", graphAgentName(run, id)));
    $("graph-column-headings").append(header);
    const rail = element("div", "agent-rail");
    rail.style.top = `${layout.top - 16}px`;
    lane.append(rail);
    canvas.append(lane);
    const ongoing = node && activeNode(node);
    const button = element(node ? "button" : "div", `agent-summary ${node ? "graph-node" : "leader-summary"} ${ongoing ? "active-node" : ""} ${selected === node?.iteration_id ? "selected" : ""}`);
    Object.assign(button.style, {left: `${laneX(id) + 20}px`, top: "60px"});
    const top = element("div", "node-top");
    top.append(element("span", "", "当前状态"), owner ? element("span", "status", agentStatus(run, owner)) : badge(node?.state));
    button.append(top);
    if (node) {
      button.dataset.node = node.iteration_id;
      button.append(element("h3", "", run.collaboration?.mode === "passk" ? "独立探索" : owner?.direction || node.title || `第 ${nodes.length} 轮探索`));
      if (node.verification) button.append(element("p", "node-score", `最佳 ${nodeScore(run, node)}`));
      else if (run.brief?.verification) button.append(element("p", "node-score muted", "暂无有效成绩"));
      if (ongoing) button.append(element("p", "node-progress", owner ? agentAction(run, owner) : "探索中"));
      button.append(element("p", "node-reports", `${nodes.reduce((sum, n) => sum + nodeReports(n).length, 0)} 份报告 · ${(run.jobs || []).filter((job) => job.agent_id === id).length} 个作业`));
      const [started, finished] = nodeTimes(run, node), budget = run.budget?.researchers?.[id];
      const timing = element("div", "node-timing");
      if (budget) {
        timing.textContent = `有效 ${(budget.effective_seconds / 60).toFixed(1)} / ${(budget.limit_seconds / 60).toFixed(0)} 分钟`;
        timing.title = `剩余 ${(budget.remaining_effective_seconds / 60).toFixed(1)} 分钟；实际历时 ${duration(started, finished)}`;
      } else timing.append(document.createTextNode("历时 "), elapsed(started, finished));
      button.append(timing);
      button.setAttribute("aria-pressed", String(selected === node.iteration_id));
      button.addEventListener("click", () => selectWork(run, node.iteration_id));
    } else {
      const outbound = (run.communications || []).filter((m) => m.sender === id);
      button.append(element("p", "leader-number", `${outbound.length} 次指导`), element("p", "node-reports", `${sharing.length} 次引用同伴经验`));
    }
    canvas.append(button);
  });
  const axis = element("div", "graph-time-label", "时间 ↓");
  Object.assign(axis.style, {left: "14px", top: `${layout.top - 34}px`}); canvas.append(axis);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("sharing-routes");
  svg.setAttribute("width", layout.width); svg.setAttribute("height", layout.height);
  const seenRows = new Set();
  const selectMessage = (message) => {
    state.selected.set(run.run_id, `message:${message.message_id}`);
    renderGraph(run);
  };
  for (const entry of layout.entries) {
    if (!seenRows.has(entry.row)) {
      seenRows.add(entry.row);
      const tick = element("div", "graph-time-tick", time(entry.at));
      tick.style.top = `${entry.y + 13}px`; canvas.append(tick);
    }
    const id = entry.message ? `message:${entry.message.message_id}` : entry.job ? entry.id : entry.node?.iteration_id;
    const active = entry.job && activeJob(entry.job);
    const button = element("button", `timeline-event event-${entry.kind} ${entry.job && (state.graphAllJobs || active) ? "graph-node execution-node" : ""} ${active ? "active-node" : ""} ${selected === id ? "selected" : ""}`);
    button.dataset.node = id;
    button.dataset.event = entry.id;
    Object.assign(button.style, {left: `${entry.x}px`, top: `${entry.y}px`});
    const kind = {start: "开始探索", report: "汇报 RSI", advice: "RSI 建议", share: "经 RSI 共享", job: entry.improves ? "个人新最佳" : entry.job ? jobTitle(entry.job) : "执行作业"}[entry.kind];
    const heading = element("div", "event-heading");
    heading.append(element("span", "event-kind", kind), element("span", "event-time", time(entry.at)));
    button.append(heading);
    let title = entry.title;
    if (entry.job?.result?.score != null) title = `${score(entry.job.result.score)}${run.brief?.verification?.metric === "speedup" ? "×" : ""} · ${entry.job.result.passed ? "验证通过" : "未通过正确性"}`;
    if (entry.kind === "share") title = `${entry.sources.map(shortName).join("、")} → ${shortName(entry.agent)} · ${title}`;
    button.append(element("p", "event-preview", title));
    if (entry.message) {
      const delivered = entry.message.delivery_status === "delivered";
      button.append(element("span", `event-delivery ${delivered ? "" : "pending"}`, delivered ? "已送达" : run.finished_at ? "结束时未送达" : "等待送达"));
      button.addEventListener("click", () => selectMessage(entry.message));
    } else button.addEventListener("click", () => selectWork(run, id));
    button.setAttribute("aria-pressed", String(selected === id));
    button.title = `${kind} · ${time(entry.at)}\n${title}`;
    canvas.append(button);
    entry.sources.forEach((source, index) => {
      const sx = laneX(source) + layout.laneWidth / 2;
      const tx = entry.x + (sx > entry.x ? 192 : 0);
      const y = entry.y + 46 + (index - (entry.sources.length - 1) / 2) * Math.min(7, 24 / entry.sources.length);
      const d = `M ${sx} ${y} C ${sx + (tx - sx) * .4} ${y - 18}, ${sx + (tx - sx) * .6} ${y + 18}, ${tx} ${y}`;
      const path = document.createElementNS(svg.namespaceURI, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", `sharing-route ${entry.kind === "share" ? "peer-sharing" : "leader-advice"} ${selected === id ? "selected" : ""}`);
      path.dataset.message = entry.message?.message_id || "";
      const tip = document.createElementNS(svg.namespaceURI, "title");
      tip.textContent = `${graphAgentName(run, source)} → ${graphAgentName(run, entry.agent)}${entry.kind === "share" ? " · 经 RSI 共享" : ""}`;
      path.append(tip); svg.append(path);
      const arrow = document.createElementNS(svg.namespaceURI, "path");
      const sign = sx < tx ? -1 : 1;
      arrow.setAttribute("d", `M ${tx + sign * 6} ${y - 4} L ${tx} ${y} L ${tx + sign * 6} ${y + 4}`);
      arrow.setAttribute("class", `sharing-arrow${entry.kind === "share" ? "" : " advice-arrow"}`); svg.append(arrow);
      const origin = element("button", "share-origin", entry.kind === "share" ? `${shortName(source)} 的经验` : "RSI 建议");
      Object.assign(origin.style, {left: `${sx - 48}px`, top: `${entry.y + 13}px`});
      origin.title = tip.textContent;
      if (entry.message) origin.addEventListener("click", () => selectMessage(entry.message));
      else origin.addEventListener("click", () => selectWork(run, entry.node.iteration_id));
      canvas.append(origin);
    });
  }
  canvas.prepend(svg);
  if (!layout.entries.length) {
    const empty = element("p", "graph-no-events", state.graphCollaborationOnly ? "暂无协作记录" : "暂无研究进展");
    empty.style.top = `${layout.top}px`; canvas.append(empty);
  }
  canvas.style.width = `${layout.width}px`; canvas.style.height = `${layout.height}px`;
  $("graph-active").hidden = !canvas.querySelector(".active-node");
  renderGraphDetail(run, selected);
  applyGraphZoom();
  viewport.scrollTo(scroll);
  if (focused) [...canvas.querySelectorAll("[data-node]")].find((button) => button.dataset.node === focused)?.focus({preventScroll: true});
}

function renderGraphDetail(run, selected) {
  const message = (run.communications || []).find((m) => `message:${m.message_id}` === selected);
  const panel = $("graph-detail"); panel.hidden = !message;
  if (!message) return;
  const header = element("div", "sharing-detail-heading");
  const sources = ResearchGraph.sharedFrom(message);
  const title = message.sender === "rsi" && sources.length
    ? `${sources.map((id) => graphAgentName(run, id)).join("、")} → ${graphAgentName(run, message.recipient)} · 经 RSI 共享`
    : `${graphAgentName(run, message.sender)} → ${graphAgentName(run, message.recipient)}`;
  header.append(element("h3", "", title), element("span", "muted", `${time(message.created_at)} · ${message.delivery_status === "delivered" ? "已送达" : run.finished_at ? "结束时未送达" : "等待送达"}`));
  const close = element("button", "quiet", "收起详情");
  close.addEventListener("click", () => { state.selected.delete(run.run_id); renderGraph(run); }); header.append(close);
  const body = element("div", "sharing-detail-body");
  const text = element("section", "sharing-message");
  text.append(element("h4", "", message.sender === "rsi" ? "RSI 的原始建议" : "原始汇报"), element("p", "preserve", message.text)); body.append(text);
  const evidence = element("section", "sharing-evidence");
  evidence.append(element("h4", "", "引用来源"));
  if (!message.references?.length) evidence.append(element("p", "muted", "无"));
  for (const ref of message.references || []) {
    const source = message.sources?.find((s) => s.uri === ref.uri);
    const item = element("details", "sharing-source");
    const category = {reports: "进展汇报", materials: "实验记录", candidates: "候选版本", verifications: "验证结果", executions: "执行记录", evidence: "研究发现", assignments: "任务说明"}[ref.uri.split("/").at(-2)] || "原始记录";
    item.append(element("summary", "", source ? `${graphAgentName(run, source.agent_id)} · ${category}${source.created_at ? ` · ${time(source.created_at)}` : ""}` : category));
    const reportId = ref.uri.split("/").at(-1)?.replace(/\.json$/, "");
    const reportNode = source && run.nodes?.find((n) => nodeReports(n).some((r) => r.report_id === reportId));
    const report = reportNode && nodeReports(reportNode).find((r) => r.report_id === reportId);
    const original = report?.message || source?.summary;
    if (original) item.append(element("p", "preserve", original));
    item.append(element("p", "record-ref", ref.uri));
    evidence.append(item);
  }
  body.append(evidence);
  panel.replaceChildren(header, body);
}

function section(title, value) {
  const block = element("section", "detail-section");
  block.append(element("h4", "", title), element("p", "", value || "暂无记录"));
  return block;
}
function assignmentDetails(run, node) {
  const instruction = node.assignment?.instruction || "暂无记录";
  const criteria = node.assignment?.completion_criteria || "";
  const key = `${run.run_id}/${node.iteration_id}`;
  const details = element("details", "assignment-details");
  details.open =
    state.assignmentExpansion.get(key) ??
    instruction.length + criteria.length <= 240;
  const summary = element("summary", "", criteria ? "任务说明与完成条件" : "任务说明");
  summary.append(
    element("span", "assignment-expand", "展开全文"),
    element("span", "assignment-collapse", "收起"),
  );
  details.append(summary, section("任务说明", instruction));
  if (criteria) details.append(section("完成条件", criteria));
  details.addEventListener("toggle", () => {
    if (details.isConnected) state.assignmentExpansion.set(key, details.open);
  });
  return details;
}
function reportHistory(run, node) {
  const reports = nodeReports(node);
  const block = element("section", "detail-section report-history");
  block.append(element("h4", "", `研究报告 · ${reports.length} 份`));
  if (!reports.length) block.append(element("p", "muted", "尚未提交报告。"));
  for (const [index, report] of [...reports].reverse().entries()) {
    const item = element("details", "report-entry");
    const key = `${run.run_id}/${node.iteration_id}/${report.report_id || report.created_at}`;
    item.open = state.reportExpansion.get(key) ?? index === 0;
    const delivery = report.delivery_status === "delivered" ? "已送达 RSI"
      : report.delivery_status === "pending" ? (run.finished_at ? "结束时尚未送达 RSI" : "等待送达 RSI")
      : report.delivery_status === "archived" ? "已归档" : "";
    const date = report.created_at ? new Date(timestamp(report.created_at)).toLocaleString("zh-CN") : "";
    item.append(element("summary", "", [`报告 ${reports.length - index}`, date, delivery].filter(Boolean).join(" · ")));
    if (report.message) {
      const body = element("div", "message-content");
      appendFormattedText(body, report.message);
      item.append(body);
    } else {
      for (const [name, title] of Object.entries({task_status: "任务执行情况", work: "具体尝试",
        problems: "遇到的问题", results: "实际结果", interpretation: "结论与不确定性", next_steps: "建议的下一步"}))
        if (report[name]) item.append(section(title, report[name]));
      if (report.shared_findings?.length)
        item.append(section("可共享的发现", report.shared_findings.join("\n")));
    }
    for (const ref of report.references || [])
      item.append(element("div", "record-ref", ref.uri));
    item.addEventListener("toggle", () => {
      if (item.isConnected) state.reportExpansion.set(key, item.open);
    });
    block.append(item);
  }
  return block;
}
function renderNode(run) {
  if (!state.selected.has(run.run_id) && run.nodes?.length) state.selected.set(run.run_id, run.nodes[0].iteration_id);
  const job = run.jobs?.find((item) => `job:${item.job_id}` === state.selected.get(run.run_id));
  if (job) {
    renderJobDetail(run, job);
    return;
  }
  const node = run.nodes?.find(
    (item) => item.iteration_id === state.selected.get(run.run_id),
  );
  const budget = run.budget?.researchers?.[node?.agent_id];
  const latest = run.preparation && [...state.events.values()].filter((event) => event.kind === "assistant_output" && event.agent_id === node?.agent_id).at(-1);
  const traceKey = `${run.run_id}/${node?.agent_id}`;
  if (node && state.tab === "node" && !state.agentTraces.has(traceKey)) loadAgentTrace(run.run_id, node.agent_id);
  const trace = state.agentTraces.get(traceKey);
  const signature = JSON.stringify([run.run_id, run.status, run.finished_at, node, budget, latest, run.human, state.busy,
    run.worker_alive, run.agent_activity?.[node?.agent_id], run.goals?.[node?.agent_id],
    run.agents?.find((a) => a.agent_id === node?.agent_id), run.jobs, [...(trace?.entries.values() || [])], trace?.loading, trace?.error, trace?.next]);
  if (signature === state.detailSignature) return;
  state.detailSignature = signature;
  if (!node) {
    const blank = element("div", "empty-detail");
    blank.append(
      element("span", "empty-symbol", "↖"),
      element("h3", "", "选择研究员或时间线事件"),
      element("p", "", "查看任务、执行进度、研究报告与结果。"),
    );
    replace("node-panel", blank);
    return;
  }
  const position =
    state.detailNodeId === node.iteration_id ? $("node-panel").scrollTop : 0;
  state.detailNodeId = node.iteration_id;
  const heading = element("div", "detail-header");
  heading.append(
    badge(node.goal?.status === "completed" ? "completed" : (run.human?.paused_agents || []).includes(node.agent_id) ? "paused" : node.state),
    element("h3", "", `${run.preparation ? "研发任务" : "本轮任务"} · ${agentName(node.agent_id)}`),
    element("p", "muted", node.iteration_id),
  );
  const [started, finished] = nodeTimes(run, node);
  const timing = element("p", "detail-timing");
  if (budget) timing.append(element("span", "", `有效时间 ${(budget.effective_seconds / 60).toFixed(1)} / ${(budget.limit_seconds / 60).toFixed(0)} 分钟，剩余 ${(budget.remaining_effective_seconds / 60).toFixed(1)} 分钟；`));
  timing.append(document.createTextNode(budget ? "实际历时 " : "本轮历时 "), elapsed(started, finished));
  if (budget) timing.append(element("small", "muted", `GPU 排队 ${(budget.gpu_queue_seconds / 60).toFixed(1)} 分钟 · API 故障等待 ${(budget.api_wait_seconds / 60).toFixed(1)} 分钟`));
  if (started) timing.append(element("small", "muted", `开始 ${new Date(timestamp(started)).toLocaleString("zh-CN")}${finished ? ` · 结束 ${new Date(timestamp(finished)).toLocaleString("zh-CN")}` : " · 进行中"}`));
  const picker = element("div", "agent-trace-switcher");
  const pickerLabel = element("label", "", "查看研究员"); pickerLabel.htmlFor = "trace-researcher";
  const select = element("select"); select.id = "trace-researcher";
  const latestNodes = new Map((run.nodes || []).map((item) => [item.agent_id, item]));
  for (const item of latestNodes.values()) {
    const option = element("option", "", agentName(item.agent_id)); option.value = item.iteration_id; select.append(option);
  }
  select.value = node.iteration_id;
  select.addEventListener("change", () => selectWork(state.run, select.value));
  picker.append(pickerLabel, select);
  const blocks = [picker, heading, timing, agentDiagnostics(run, node.agent_id)];
  if (node.goal) {
    blocks.push(section("本路目标状态", `${{active: "执行中", blocked: "等待补充条件", completed: "已完成"}[node.goal.status]} · ${node.goal.summary}`));
    if (node.goal.blocker) blocks.push(section("等待补充", node.goal.blocker));
    if (node.goal.evidence?.length) blocks.push(section("本路验收证据", node.goal.evidence.join("\n")));
  }
  if (canGuide(run)) {
    const actions = element("section", "detail-section agent-trace-controls");
    const guide = element("button", "primary", "指导这个 Agent");
    guide.addEventListener("click", () => {
      state.replyTargets.set(run.run_id, {value: node.agent_id, explicit: true});
      switchTab("chat"); render(); $("chat-input").focus();
    });
    const paused = (run.human?.paused_agents || []).includes(node.agent_id);
    const hold = element("button", "secondary", paused ? "继续这个 Agent" : "暂停这个 Agent");
    hold.disabled = state.busy;
    hold.addEventListener("click", () => control("agent-control", {agent_id: node.agent_id, paused: !paused}));
    actions.append(guide, hold, element("p", "muted", "指导仅发给此研究员。暂停后可发送指导，再继续执行；已有作业请在作业详情中单独取消。"));
    blocks.push(actions);
  }
  blocks.push(renderAgentTrace(run, node.agent_id, trace));
  blocks.push(assignmentDetails(run, node));
  if (latest?.data?.text && !trace?.entries.size) {
    const progress = element("section", "detail-section progress-message");
    progress.append(element("h4", "", "最新执行说明"));
    appendProgressText(progress, latest.data.text);
    blocks.push(progress);
  }
  if (node.verification)
    blocks.push(section("本次探索最佳有效成绩", `${nodeScore(run, node)} · 探索阶段正式验证`));
  blocks.push(reportHistory(run, node));
  const jobs = (run.jobs || []).filter((job) => job.iteration_id === node.iteration_id);
  if (jobs.length) {
    const links = element("div", "node-job-links");
    for (const job of jobs) {
      const link = element("button", "quiet", `${jobTitle(job)} · ${label(job.status)}`);
      link.addEventListener("click", () => selectWork(run, `job:${job.job_id}`));
      links.append(link);
    }
    blocks.push(links);
  }
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

function renderJobDetail(run, job) {
  const id = `job:${job.job_id}`;
  const signature = JSON.stringify([run.run_id, job, Math.floor(Date.now() / 10000)]);
  if (signature === state.detailSignature) return;
  state.detailSignature = signature;
  const scroll = state.detailNodeId === id ? $("node-panel").scrollTop : 0;
  state.detailNodeId = id;
  const heading = element("div", "detail-header");
  heading.append(badge(job.status), element("h3", "", jobTitle(job)), element("p", "muted", job.job_id));
  const blocks = [heading, section(activeJob(job) ? "当前执行" : "最后上报的执行情况", job.error || job.progress?.message || job.progress_message
    || (activeJob(job) ? "执行器尚未上报细项进度。" : label(job.status)))];
  if (canGuide(run) && activeJob(job)) {
    const cancel = element("button", "secondary", "取消此作业");
    cancel.addEventListener("click", () => control("cancel-job", {job_id: job.job_id}));
    blocks.push(cancel);
  }
  blocks.push(section("所属目标", `v${(job.goal_revision || 0) + 1}${job.goal_revision != null && job.goal_revision !== (run.brief?.revision || 0) ? " · 历史目标结果" : ""}`));
  if (job.report_available) {
    const report = element("button", "secondary", "下载公开验收报告");
    report.addEventListener("click", () => downloadArtifact("export-report", {job_id: job.job_id}));
    blocks.push(report);
  }
  const progress = element("section", "detail-section");
  progress.append(element("h4", "", job.progress?.stage || "执行进度"),
    element("p", "", progressText(job)), progressBar(job),
    element("p", "muted", progressFreshness(job)));
  blocks.push(progress);
  if (job.progress?.items?.length) {
    const items = element("section", "detail-section task-items");
    items.append(element("h4", "", "分项进度"));
    for (const item of job.progress.items) {
      const row = element("div", "task-item");
      const title = element("div", "task-item-title");
      title.append(element("strong", "", item.label || item.id || "子任务"), badge(item.status));
      row.append(title, element("p", "muted", item.message));
      items.append(row);
    }
    blocks.push(items);
  }
  blocks.push(section("执行信息", `${job.agent_name || agentName(job.agent_id)}\n${job.started_at ? `开始于 ${time(job.started_at)} · 已运行 ${duration(job.started_at, job.finished_at)}` : "等待调度启动"}${job.gpu_ids?.length ? `\nGPU ${job.gpu_ids.join(", ")}` : ""}`));
  if (job.verification) blocks.push(section("验证用途", job.is_baseline
    ? "评测起点版本，作为后续优化的比较依据。"
    : job.phase === "final" ? "确认选定版本的最终表现。" : "评估本轮候选，供 RSI 决定下一步研究。"));
  if (typeof job.result?.score === "number") blocks.push(section("正式验证结果", score(job.result.score)));
  if (job.candidate_id) blocks.push(section("评测版本", job.candidate_id));
  if (job.iteration_id) {
    const parent = element("button", "quiet", "查看所属研究任务 →");
    parent.addEventListener("click", () => selectWork(run, job.iteration_id));
    blocks.push(parent);
  }
  replace("node-panel", ...blocks);
  $("node-panel").scrollTop = scroll;
}

function renderChatFeedback(run, target, agent) {
  const box = $("chat-feedback");
  const sending = state.sending.has(run.run_id);
  const pending = (run.pending_agents || []).includes(target.recipient);
  const working = agent?.state === "running";
  const open = ["planned", "running"].includes(run.status) || target.recipient === "interaction";
  const unserved = run.worker_alive === false && (pending || working);
  const pass1 = isSingleRun(run) && target.recipient === primaryAgent(run);
  if (pass1 && run.status === "paused" && agent?.wait_reason === "user_cancelled") {
    box.hidden = false;
    box.replaceChildren(element("p", "", "当前执行已取消，上下文和已有结果已保留。发送新指令或点击继续，即可恢复执行。"));
    return;
  }
  box.hidden = !open || !(sending || pending || working);
  if (box.hidden) { box.replaceChildren(); return; }
  const status = sending ? "正在发送消息…" : unserved ? "消息已保存，等待恢复服务"
    : working && pass1 ? "研究员正在执行目标" : working ? `等待 ${agentName(target.recipient)} 回复` : `消息已收到，等待 ${agentName(target.recipient)} 处理`;
  const row = element("div", "chat-feedback-title");
  if (!unserved) { const spinner = element("span", "spinner"); spinner.setAttribute("aria-hidden", "true"); row.append(spinner); }
  row.append(document.createTextNode(status));
  const detail = unserved ? "点击继续对话或恢复执行。" : working ? agentAction(run, agent) : "";
  box.replaceChildren(row, element("small", "muted", detail));
  if (pass1 && working && !unserved) {
    const activity = run.agent_activity?.[primaryAgent(run)];
    if (activity?.updated_at) box.append(element("small", "muted", `最近活动：${time(activity.updated_at)}`));
    const latest = [...state.events.values()].filter((event) => event.kind === "assistant_output" && event.agent_id === primaryAgent(run)).at(-1);
    const output = latest?.data?.text || run.goal_turns?.[0]?.output?.at(-1);
    const trace = state.agentTraces.get(`${run.run_id}/${primaryAgent(run)}`);
    const inConversation = [...(trace?.entries.values() || [])].some((entry) => entry.kind === "assistant_output" && entry.text === output);
    if (output && !inConversation) {
      const content = element("div", "message-content preserve");
      appendFormattedText(content, output);
      box.append(content);
    }
    const turn = run.goal_turns?.[0];
    const queued = turn?.status === "running" && (run.messages || []).some((message) =>
      message.sender === "user" && message.recipient === primaryAgent(run) && message.created_at >= turn.started_at
      && !(turn.inputs || []).some((input) => input.message_id === message.message_id));
    if (queued) box.append(element("small", "muted", "补充消息仍在排队，将在下一次 RSI 工具返回或下一轮开始时送达。"));
  }
}

function renderMessages(run) {
  const agentId = renderReplyTarget(run).recipient;
  const messages = [...(run.messages || [])];
  const history = $("chat-history");
  history.replaceChildren();
  for (const id of new Set(["interaction", agentId])) {
    const trace = state.agentTraces.get(`${run.run_id}/${id}`);
    for (const entry of trace?.entries.values() || []) {
      if (entry.kind !== "assistant_output" || !entry.text?.trim()) continue;
      // The runtime may also publish the final output as a formal reply.
      if (messages.some((message) => message.sender === id && message.text?.trim() === entry.text.trim())) continue;
      messages.push({message_id: `output:${id}:${entry.sequence}`, sender: id,
        text: entry.text, created_at: entry.created_at, progress: true});
    }
    if (trace?.next != null) {
      const more = element("button", "quiet", `加载 ${agentName(id)} 更早的过程说明`);
      more.disabled = trace.loading;
      more.addEventListener("click", () => loadAgentTrace(run.run_id, id, true));
      history.append(more);
    }
    if (trace?.error) {
      history.append(element("span", "error", `${agentName(id)} 过程说明加载失败：${trace.error}`));
      const retry = element("button", "quiet", "重试");
      retry.addEventListener("click", () => loadAgentTrace(run.run_id, id));
      history.append(retry);
    }
  }
  history.hidden = !history.childElementCount;
  messages.sort((a, b) => new Date(timestamp(a.created_at)) - new Date(timestamp(b.created_at)));
  const pending = state.outgoing.get(run.run_id);
  if (pending && !messages.some((message) => message.message_id === pending.message_id)) messages.push(pending);
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
          ? `你 → ${agentName(message.recipient)}`
          : message.sender === "rsi"
            ? "RSI Agent"
            : message.sender === "runtime"
            ? "系统状态"
            : agentName(message.sender),
      ),
      element("time", "", time(message.created_at)),
    );
    const content = element("div", "message-content");
    if (message.sender === "user") content.textContent = message.text;
    else appendFormattedText(content, message.text || "");
    if (message.progress) heading.append(element("span", "muted", "过程说明"));
    block.append(heading, content);
    if (message.delivery_status && message.sender === "user")
      block.append(element("small", "delivery-status", {pending: "已保存 · 待送达", delivered: "已送达 · 等待回复", replied: "Agent 已回复"}[message.delivery_status]));
    if (message.delivery) {
      block.append(element("small", "delivery-status", message.delivery === "failed" ? "发送未确认，请重试" : "正在发送…"));
      if (message.delivery === "failed") {
        const retry = element("button", "quiet", "重试发送");
        retry.addEventListener("click", () => {
          $("chat-input").value = message.text;
          state.replyTargets.set(run.run_id, {value: message.reply_to || message.recipient, explicit: true});
          render();
          $("chat-form").requestSubmit();
        });
        block.append(retry);
      }
    }
    if (message.sender === "user" && message.reply_to) {
      const question = messages.find((item) => item.message_id === message.reply_to);
      if (question) block.insertBefore(
        element("p", "message-reply", `回复：${questionPreview(question, 60)}`), content);
    }
    return block;
  });
  container.replaceChildren(...blocks);
  container.scrollTop = follow ? container.scrollHeight : previous;
}
function appendFormattedText(container, text) {
  // A small DOM-only renderer for the common emphasis, code and links in replies.
  // User/model text is never interpreted as HTML.
  const pattern = /```[^\n]*\n([\s\S]*?)```|\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(offset, match.index)));
    if (match[1] !== undefined) container.append(element("pre", "message-code", match[1]));
    else if (match[2]) container.append(element("strong", "", match[2]));
    else if (match[3]) container.append(element("code", "", match[3]));
    else {
      const link = element("a", "", match[4]);
      link.href = match[5];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      container.append(link);
    }
    offset = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(offset)));
}
function budgetLimit(value) { return value === null ? "不限" : integer(value); }
function renderBrief(run) {
  const brief = run.brief || {},
    limits = brief.limits || {},
    verification = brief.verification;
  const blocks = [
    section("研究目标", brief.objective || run.objective),
    section("给 RSI 的要求与建议", brief.instructions),
    ...(brief.acceptance?.length ? [section("验收标准", brief.acceptance.join("\n"))] : []),
  ];
  const grid = element("dl", "brief-grid");
  const rows = [
    ["同时存活研究员上限", limits.max_agents],
    ["节点上限", budgetLimit(limits.max_nodes)],
    ["Token 上限", budgetLimit(limits.max_total_tokens)],
    ["验证次数上限", budgetLimit(limits.max_verify_calls)],
    ["研究时长上限", limits.max_wall_seconds === null ? "不限" : `${budgetLimit(limits.max_wall_seconds)} 秒`],
    ...(limits.max_effective_seconds != null ? [["每名研究员有效时间上限", `${limits.max_effective_seconds / 60} 分钟`]] : []),
    ["作业并发上限", budgetLimit(limits.max_jobs)],
    ["本机 GPU", limits.gpu_ids?.join(", ") || (run.preparation ? "远端资源见执行配置和作业进度" : "尚未配置")],
    ["允许修改", limits.allowed_paths?.join(", ") || "待确认"],
    ["验证指标", verification?.metric || "尚未设置"],
    ["目标", score(verification?.target)],
  ];
  for (const [key, value] of rows)
    grid.append(element("dt", "", key), element("dd", "", value ?? "—"));
  for (const [agentId, budget] of Object.entries(run.budget?.researchers || {})) {
    grid.append(element("dt", "", `${agentId} · 有效时间`), element("dd", "",
      `${(budget.effective_seconds / 60).toFixed(1)} / ${(budget.limit_seconds / 60).toFixed(0)} 分钟；GPU 排队 ${(budget.gpu_queue_seconds / 60).toFixed(1)} 分钟，API 故障等待 ${(budget.api_wait_seconds / 60).toFixed(1)} 分钟`));
  }
  blocks.push(grid);
  if (verification && run.human?.verification_current === false)
    blocks.push(section("新目标验收覆盖待确认", "已有验证器的成绩仅供参考，不会自动认定新目标达标；确认覆盖范围后可再次调整目标并勾选验收确认。"));
  if (verification?.description)
    blocks.push(section("验证条件", verification.description));
  for (const change of run.human?.goal_changes || []) {
    const history = element("details", "detail-section");
    history.append(element("summary", "", `目标 v${change.previous.revision + 1} → v${change.brief.revision + 1} · ${change.applied ? "已应用" : "待恢复"}`),
      section("修改前", change.previous.objective + "\n" + change.previous.instructions),
      section("修改后", change.brief.objective + "\n" + change.brief.instructions));
    blocks.push(history);
  }
  replace("brief-panel", ...blocks);
}
function renderJobs(run) {
  const jobs = [...(run.jobs || [])].sort((a, b) => Number(activeJob(b)) - Number(activeJob(a)));
  $("job-count").textContent = `${jobs.filter(activeJob).length} 进行中 / ${jobs.length}`;
  const focused = document.activeElement?.dataset.job;
  const scroll = $("jobs").scrollTop;
  replace(
    "jobs",
    ...(jobs.length
      ? jobs.map((job) => {
          const row = element("button", "job-row");
          row.dataset.job = job.job_id;
          row.addEventListener("click", () => selectWork(run, `job:${job.job_id}`));
          const body = element("div", "job-body");
          body.append(
            element("h3", "", jobTitle(job)),
            element(
              "p",
              "",
              job.publication_error ? `产物归档或验证失败：${job.publication_error}` :
                job.error || job.progress?.message || job.progress_message || job.progress?.stage || label(job.status),
            ),
            element("p", "job-meta", `${job.agent_name || agentName(job.agent_id)} · ${job.started_at ? `已运行 ${duration(job.started_at, job.finished_at)}` : "等待启动"}${progressText(job) ? ` · ${progressText(job)}` : ""}`),
            progressBar(job),
          );
          row.append(body, badge(job.status));
          return row;
        })
      : [
          element(
            "p",
            "muted",
            "暂无作业。",
          ),
        ]),
  );
  $("jobs").scrollTop = scroll;
  if (focused) [...$("jobs").querySelectorAll("button")]
    .find((button) => button.dataset.job === focused)?.focus({preventScroll: true});
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
            (event.kind === "model_retry" ? `${event.data?.error_code} · 已失败 ${event.data?.attempt} 次` : "") ||
            (event.kind === "report_submitted" ? agentName(event.agent_id) : "") ||
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
  if (tab === "node" && state.run) {
    renderNode(state.run);
    const node = state.run.nodes?.find((n) => n.iteration_id === state.selected.get(state.runId));
    if (node) loadAgentTrace(state.runId, node.agent_id);
  } else if (tab === "chat" && state.run && state.traceAvailable) {
    loadAgentTrace(state.runId, renderReplyTarget(state.run).recipient);
  }
}

async function control(action, params = {}) {
  if (!state.runId || state.busy) return;
  if (action === "start" && (modelSettingsChanged() || state.modelSavingRun)) {
    notice("请先保存模型设置。");
    return;
  }
  state.busy = true;
  $("cancel-turn").disabled = true;
  notice();
  for (const button of $("controls").querySelectorAll("button"))
    button.disabled = true;
  try {
    const result = await api(
      `/api/runs/${encodeURIComponent(state.runId)}/${action}`,
      params,
    );
    if (result.notice) notice(result.notice);
    await refresh(true);
  } catch (error) {
    notice(error.message);
  } finally {
    state.busy = false;
    for (const button of $("controls").querySelectorAll("button"))
      button.disabled = false;
    if (state.run) render();
  }
}

function deletionBlocked(run) {
  return ["running", "paused"].includes(run.status) || run.finalization?.status === "running";
}
$("delete-run").addEventListener("click", () => {
  if (!state.run || state.busy) return;
  if (deletionBlocked(state.run)) {
    notice("请先停止研究，再删除记录。");
    return;
  }
  state.deleteTarget = state.runId;
  $("delete-name").textContent = state.run.objective || "未命名研究";
  $("delete-error").hidden = true;
  $("delete-dialog").showModal();
});
$("delete-cancel").addEventListener("click", () => $("delete-dialog").close());
$("delete-dialog").addEventListener("cancel", (event) => {
  if (state.deleting) event.preventDefault();
});
$("delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const runId = state.deleteTarget;
  if (!runId || state.busy) return;
  state.busy = state.deleting = true;
  state.refreshController?.abort();
  $("delete-submit").disabled = $("delete-cancel").disabled = true;
  $("delete-submit").textContent = "正在删除…";
  $("delete-error").hidden = true;
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/delete`, {});
    for (const cache of [state.runCache, state.eventCache, state.cursors, state.selected,
      state.drafts, state.replyTargets, state.outgoing, state.chatErrors, state.modelDrafts]) cache.delete(runId);
    localStorage.removeItem(`rsi.workbench.node.${runId}`);
    localStorage.removeItem(`rsi.workbench.zoom.${runId}`);
    localStorage.removeItem("rsi.workbench.run");
    const url = new URL(window.location.href);
    url.searchParams.delete("run");
    window.history.replaceState(null, "", url);
    state.runs = state.runs.filter((run) => run.run_id !== runId);
    state.runId = state.run = null;
    state.events = new Map();
    state.deleting = false;
    $("delete-dialog").close();
    renderRuns();
    $("workspace").hidden = true;
    $("run-loading").hidden = true;
    $("welcome").hidden = state.runs.length > 0;
    $("run-short").textContent = "准备开始";
    $("chat-input").value = "";
    if (state.runs.length) await selectRun(state.runs[0].run_id);
    notice("研究已删除。");
  } catch (error) {
    $("delete-error").textContent = error.message;
    $("delete-error").hidden = false;
  } finally {
    state.busy = state.deleting = false;
    $("delete-submit").disabled = $("delete-cancel").disabled = false;
    $("delete-submit").textContent = "确认删除";
    if (state.run) render();
  }
});

async function createConversation(message = "", modelSettings, collaboration, workspace) {
  if (state.creating) return;
  state.creating = true;
  $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = true;
  notice();
  try {
    $("create-submit").disabled = true;
    $("create-submit").textContent = "正在创建…";
    const result = await api("/api/runs", { message, message_id: crypto.randomUUID(), ...(modelSettings ? {model_settings: modelSettings} : {}), ...(collaboration ? {collaboration} : {}), ...(workspace || {}) });
    $("create-dialog").close();
    $("create-api-key").value = "";
    if (!result.run_id) throw new Error("未能读取研究记录，请刷新列表。");
    await loadRuns();
    state.runId = null;
    await selectRun(result.run_id);
    switchTab("chat");
    $("welcome-input").value = "";
    $("chat-input").focus();
  } catch (error) {
    $("create-error").textContent = error.message;
    $("create-error").hidden = false;
    notice(error.message);
  } finally {
    $("create-submit").disabled = false;
    $("create-submit").textContent = "创建任务";
    state.creating = false;
    $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = false;
  }
}
const createPreset = new WorkbenchDropdown($("create-model-preset"), {
  label: "常用模型", onChange: (value) => { $("create-model").value = value; updateCreateModels(); },
});
const createBackend = new WorkbenchDropdown($("create-backend"), {
  label: "Agent 后端", onChange: (value) => { state.createBackend = value; if (value === "standard") state.createAuthMode = "api_key"; createBackend.update(backendOptions, value); updateCreateAuth(); },
});
const createAuth = new WorkbenchDropdown($("create-auth-mode"), {
  label: "认证方式", onChange: (value) => { state.createAuthMode = value; $("create-api-key").value = ""; updateCreateAuth(); },
});
function updateCreateAuth() {
  const localLogin = state.createAuthMode === "codex_login";
  createAuth.update(authOptions.map((item) => ({...item, disabled: item.value === "codex_login" && state.createBackend === "standard"})), state.createAuthMode);
  for (const id of ["create-base-url", "create-api-key"]) {
    $(id).closest("label").hidden = localLogin;
    $(id).disabled = localLogin;
  }
}
const createEffort = new WorkbenchDropdown($("create-effort"), {
  label: "推理强度", onChange: (value) => { state.createEffort = value; updateCreateModels(); },
});
function updateCreateModels() {
  const model = $("create-model").value;
  const efforts = state.models.find((item) => item.id === model)?.efforts || Object.keys(effortLabels);
  if (!efforts.includes(state.createEffort)) state.createEffort = "medium";
  createPreset.update(state.models.map((item) => ({value: item.id, text: item.id})), model);
  createEffort.update(efforts.map((value) => ({value, text: effortLabels[value]})), state.createEffort);
}
function workspaceRequest() {
  return state.workspaceReady ? {use_workspace: true,
    wall_seconds: $("create-hours").value.trim() === "" ? null : Math.round(Number($("create-hours").value) * 3600)} : {};
}
function openCreate(message = "", collaboration = null) {
  if (state.creating) return;
  const defaults = state.defaultModelSettings;
  $("create-base-url").value = defaults.base_url || "https://4ipic.com";
  $("create-model").value = defaults.model || "gpt-6-astra";
  $("create-api-key").value = "";
  $("create-api-key").placeholder = defaults.has_credentials ? "留空使用默认密钥（已配置）" : "请输入 API Key";
  $("create-message").value = message;
  $("create-error").hidden = true;
  $("create-hours").value = "";
  $("create-time-limit").hidden = !state.workspaceReady;
  $("create-options").open = !defaults.has_credentials && defaults.auth_mode !== "codex_login";
  state.createEffort = defaults.reasoning_effort || "medium";
  state.createBackend = defaults.backend || "standard";
  createBackend.update(backendOptions, state.createBackend);
  state.createAuthMode = defaults.auth_mode || "api_key";
  updateCreateAuth();
  state.createCollaboration = createCollaboration(collaboration);
  renderCreateCollaboration();
  updateCreateModels();
  $("create-dialog").showModal();
}
$("create-model").addEventListener("input", updateCreateModels);
$("create-cancel").addEventListener("click", () => { if (!state.creating) $("create-dialog").close(); });
$("create-dialog").addEventListener("cancel", (event) => { if (state.creating) event.preventDefault(); });
$("create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  createConversation($("create-message").value.trim(), {
    base_url: state.createAuthMode === "codex_login" ? "https://api.openai.com" : $("create-base-url").value.trim(), api_key: state.createAuthMode === "codex_login" ? "" : $("create-api-key").value.trim(),
    auth_mode: state.createAuthMode,
    model: $("create-model").value.trim(), reasoning_effort: state.createEffort, backend: state.createBackend,
  }, collaborationSelection(state.createCollaboration), workspaceRequest());
});
$("new-run").addEventListener("click", () => openCreate());
$("mobile-new").addEventListener("click", () => openCreate());
$("welcome-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const message = $("welcome-input").value.trim();
  if (message) openCreate(message);
});
$("welcome-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("welcome-form").requestSubmit();
  }
});
for (const name of ["chat", "node", "brief"])
  $(`${name}-tab`).addEventListener("click", () => switchTab(name));
for (const button of $("controls").querySelectorAll("button[data-action]"))
  button.addEventListener("click", () => control(button.dataset.action));
$("graph-active").addEventListener("click", () => {
  const active = [...$("graph-canvas").querySelectorAll(".active-node")];
  const current = active.findIndex((node) => node.dataset.node === state.selected.get(state.runId));
  const next = active[(current + 1) % active.length];
  if (!next) return;
  const position = {left: Math.max(0, next.offsetLeft * state.graphZoom - 24), top: Math.max(0, next.offsetTop * state.graphZoom - 46)};
  selectWork(state.run, next.dataset.node);
  $("graph-viewport").scrollTo(position);
  [...$("graph-canvas").querySelectorAll("button")].find((node) => node.dataset.node === next.dataset.node)?.focus({preventScroll: true});
});
function applyGraphZoom() {
  const canvas = $("graph-canvas"), surface = $("graph-surface");
  canvas.style.transform = `scale(${state.graphZoom})`;
  $("graph-column-headings").style.width = `${parseFloat(canvas.style.width || 0) * state.graphZoom}px`;
  for (const heading of $("graph-column-headings").children) {
    heading.style.left = `${Number(heading.dataset.x) * state.graphZoom}px`;
    heading.style.width = `${Number(heading.dataset.width) * state.graphZoom}px`;
  }
  surface.style.width = `${parseFloat(canvas.style.width || 0) * state.graphZoom}px`;
  surface.style.height = `${parseFloat(canvas.style.height || 0) * state.graphZoom}px`;
  $("graph-reset").textContent = `${Math.round(state.graphZoom * 100)}%`;
  $("graph-zoom-out").disabled = state.graphZoom <= .2;
  $("graph-zoom-in").disabled = state.graphZoom >= 2;
}
function zoomGraph(value, point) {
  const viewport = $("graph-viewport");
  const anchor = point || {x: viewport.clientWidth / 2, y: viewport.clientHeight / 2};
  const x = (viewport.scrollLeft + anchor.x) / state.graphZoom;
  const y = (viewport.scrollTop + anchor.y) / state.graphZoom;
  state.graphZoom = Math.max(.2, Math.min(2, value));
  applyGraphZoom();
  viewport.scrollTo({left: x * state.graphZoom - anchor.x, top: y * state.graphZoom - anchor.y});
  if (state.runId) localStorage.setItem(`rsi.workbench.zoom.v2.${state.runId}`, String(state.graphZoom));
}
$("graph-reset").addEventListener("click", () => zoomGraph(1));
$("graph-zoom-in").addEventListener("click", () => zoomGraph(state.graphZoom * 1.2));
$("graph-zoom-out").addEventListener("click", () => zoomGraph(state.graphZoom / 1.2));
$("graph-fit").addEventListener("click", () => {
  const canvas = $("graph-canvas"), viewport = $("graph-viewport");
  zoomGraph(Math.min(1, viewport.clientWidth / (parseFloat(canvas.style.width) || 1)));
  viewport.scrollTo({left: 0, top: 0});
});
$("graph-sharing").addEventListener("click", () => { state.graphCollaborationOnly = !state.graphCollaborationOnly; if (state.run) renderGraph(state.run); });
$("graph-jobs").addEventListener("click", () => { state.graphAllJobs = !state.graphAllJobs; if (state.run) renderGraph(state.run); });
$("graph-expand").addEventListener("click", () => {
  const expanded = $("graph-viewport").classList.toggle("expanded");
  $("graph-expand").setAttribute("aria-pressed", String(expanded));
  $("graph-expand").textContent = expanded ? "收起画布 ⛶" : "展开画布 ⛶";
});
$("graph-viewport").addEventListener("wheel", (event) => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const rect = event.currentTarget.getBoundingClientRect();
  zoomGraph(state.graphZoom * Math.exp(-event.deltaY * .005), {x: event.clientX - rect.left, y: event.clientY - rect.top});
}, {passive: false});
$("graph-viewport").addEventListener("keydown", (event) => {
  if (event.target.closest("button")) return;
  if (["+", "=", "-", "0"].includes(event.key)) {
    event.preventDefault();
    zoomGraph(event.key === "0" ? 1 : state.graphZoom * (event.key === "-" ? 1 / 1.2 : 1.2));
  }
});
let graphDrag = null;
$("graph-viewport").addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || event.target.closest("button") || event.pointerType === "touch") return;
  const viewport = event.currentTarget;
  graphDrag = {x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop};
  viewport.setPointerCapture(event.pointerId);
});
$("graph-viewport").addEventListener("pointermove", (event) => {
  if (graphDrag) event.currentTarget.scrollTo({left: graphDrag.left + graphDrag.x - event.clientX, top: graphDrag.top + graphDrag.y - event.clientY});
});
for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
  $("graph-viewport").addEventListener(name, () => { graphDrag = null; });
$("graph-viewport").addEventListener(
  "scroll",
  () => {
    if (state.runId)
      localStorage.setItem(
        `rsi.workbench.viewport.v2.${state.runId}`,
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
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text || !state.run || state.sending.has(state.runId)) return;
  if (modelSettingsChanged() || state.modelSavingRun) {
    notice("请先保存模型设置。");
    return;
  }
  const target = renderReplyTarget(state.run);
  if (target.stale) return;
  const runId = state.runId;
  state.chatErrors.delete(runId);
  const previous = state.outgoing.get(runId);
  const same = previous?.text === text && previous.recipient === target.recipient && previous.reply_to === target.reply_to;
  const message = {sender: "user", recipient: target.recipient, reply_to: target.reply_to,
    text, message_id: same ? previous.message_id : crypto.randomUUID(),
    created_at: same ? previous.created_at : new Date().toISOString(), delivery: "sending"};
  state.outgoing.set(runId, message);
  state.sending.add(runId);
  render();
  $("messages").scrollTop = $("messages").scrollHeight;
  try {
    const receipt = await api(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      text,
      message_id: message.message_id,
      ...(target.reply_to ? { reply_to: target.reply_to } : { recipient: target.recipient }),
    });
    message.message_id = receipt.message_id || message.message_id;
    state.drafts.set(runId, "");
    if (target.reply_to) state.replyTargets.delete(runId);
    if (state.runId === runId && $("chat-input").value.trim() === text)
      $("chat-input").value = "";
    message.delivery = null;
    state.outgoing.delete(runId);
    if (state.runId === runId) {
      await refresh(true);
      $("messages").scrollTop = $("messages").scrollHeight;
    }
  } catch (error) {
    message.delivery = "failed";
    state.chatErrors.set(runId, error.message);
    if (state.runId === runId) notice(error.message);
  } finally {
    state.sending.delete(runId);
    if (state.run) render();
  }
});
$("chat-input").addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key.toLowerCase() === "c" && !event.isComposing
      && event.target.selectionStart === event.target.selectionEnd
      && !$("cancel-turn").hidden && !$("cancel-turn").disabled) {
    event.preventDefault();
    control("cancel");
    return;
  }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("chat-form").requestSubmit();
  }
});
$("cancel-turn").addEventListener("click", () => control("cancel"));

async function bootstrap() {
  const config = await api("/api/bootstrap");
  const branch = config.branch || "";
  document.title = `RSI · 研究工作台${branch ? ` — ${branch}` : ""}`;
  $("workbench-caption").textContent = branch || "研究工作台";
  $("workbench-name").textContent = branch ? `研究空间 · ${branch}` : "研究空间";
  state.token = config.csrf_token;
  state.traceAvailable = (config.capabilities || []).includes("agent-trace");
  state.models = config.models || [];
  state.defaultModelSettings = config.default_model_settings || {};
  state.workspaceReady = config.workspace_ready === true;
  state.collaborationCatalog = config.collaboration_catalog || {modes: [], modules: []};
  state.defaultCollaboration = config.default_collaboration || {mode: "passk", modules: []};
}

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
setInterval(tickElapsed, 1000);
boot();

function canGuide(run) {
  return run.collaboration?.mode === "passk" && !!run.started_at && ["running", "paused"].includes(run.status) && !run.finalization?.status;
}
let goalDraft = null;
$("update-goal").addEventListener("click", () => {
  const run = state.run;
  goalDraft = {run_id: run.run_id, request_id: crypto.randomUUID(), expected_revision: run.brief?.revision || 0};
  $("revised-objective").value = run.brief?.objective || run.objective;
  $("revised-instructions").value = run.brief?.instructions || "";
  $("revised-acceptance").value = (run.brief?.acceptance || []).join("\n");
  $("goal-verification-confirmation").hidden = !run.brief?.verification;
  $("goal-verification-confirmed").checked = false;
  $("goal-revision-label").textContent = `当前目标 v${goalDraft.expected_revision + 1}，提交为 v${goalDraft.expected_revision + 2}`;
  $("update-goal-error").textContent = "";
  $("goal-dialog").showModal();
});
$("close-goal-dialog").addEventListener("click", () => $("goal-dialog").close());
$("update-goal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!goalDraft || $("submit-goal").disabled) return;
  const {run_id, ...identity} = goalDraft;
  $("submit-goal").disabled = true;
  try {
    await api(`/api/runs/${encodeURIComponent(run_id)}/update-goal`, {...identity, objective: $("revised-objective").value.trim(), instructions: $("revised-instructions").value.trim(), acceptance: $("revised-acceptance").value.split("\n").map(s => s.trim()).filter(Boolean), verification_confirmed: $("goal-verification-confirmed").checked});
    $("goal-dialog").close();
    await loadRuns();
    if (state.runId === run_id) await refresh(true);
  } catch (error) { $("update-goal-error").textContent = error.message; }
  finally { $("submit-goal").disabled = false; }
});

async function loadAgentTrace(runId, agentId, older = false) {
  const key = `${runId}/${agentId}`;
  let trace = state.agentTraces.get(key);
  if (!trace) {
    trace = {entries: new Map(), next: null, ready: false, loading: false, error: '', expanded: false};
    state.agentTraces.set(key, trace);
    if (state.agentTraces.size > 24) state.agentTraces.delete(state.agentTraces.keys().next().value);
  }
  if (!state.traceAvailable) {
    trace.error = '当前服务尚未加载完整轨迹接口，需要重启工作台后查看。';
    return;
  }
  if (trace.loading || (older && trace.next == null)) return;
  trace.loading = true;
  trace.error = '';
  try {
    const page = await api(`/api/runs/${encodeURIComponent(runId)}/trace?agent_id=${encodeURIComponent(agentId)}&limit=50${older ? `&before=${trace.next}` : ''}`);
    if (page.agent_id !== agentId) throw new Error('轨迹接收者不匹配，请重试');
    const entries = page.entries || [];
    const previousLast = Math.max(0, ...trace.entries.keys());
    const gap = !older && previousLast && entries.length && entries[0].sequence > previousLast;
    if (gap) { trace.entries.clear(); trace.ready = false; }
    if (!trace.ready || older) trace.next = page.next_before ?? null;
    for (const entry of entries) trace.entries.set(entry.sequence, entry);
    trace.ready = true;
    trace.expanded ||= older;
    if (!trace.expanded && trace.entries.size > 500) {
      const keys = [...trace.entries.keys()].sort((a, b) => a - b);
      for (const sequence of keys.slice(0, -500)) trace.entries.delete(sequence);
      trace.next = Math.min(...trace.entries.keys());
    }
  } catch (error) { trace.error = error.message; }
  finally {
    trace.loading = false;
    if (state.runId === runId && state.run && state.tab === "chat"
        && (agentId === "interaction" || renderReplyTarget(state.run).recipient === agentId)) {
      const panel = $("messages"), height = panel.scrollHeight, top = panel.scrollTop;
      renderMessages(state.run);
      const target = renderReplyTarget(state.run);
      renderChatFeedback(state.run, target, state.run.agents?.find((agent) => agent.agent_id === target.recipient));
      if (older) panel.scrollTop = top + panel.scrollHeight - height;
    }
    const node = state.run?.nodes?.find((n) => n.iteration_id === state.selected.get(runId));
    if (state.runId === runId && node?.agent_id === agentId && state.tab === "node") {
      const panel = $('node-panel');
      if (!panel) return;
      const height = panel.scrollHeight, top = panel.scrollTop;
      renderNode(state.run);
      if (older) panel.scrollTop = top + panel.scrollHeight - height;
    }
  }
}

function agentDiagnostics(run, agentId) {
  const box = element('section', 'agent-diagnostics');
  const agent = run.agents?.find((a) => a.agent_id === agentId);
  const goal = run.goals?.[agentId] || run.nodes?.find((n) => n.agent_id === agentId)?.goal;
  const activity = run.agent_activity?.[agentId] || {};
  const tools = Object.values(activity.active_tools || {});
  const job = run.jobs?.find((j) => j.agent_id === agentId && activeJob(j));
  const paused = run.status === 'paused' || run.human?.paused_agents?.includes(agentId);
  const terminal = ['stopped', 'succeeded', 'completed', 'failed', 'cancelled'].includes(run.status);
  let phase = '等待调度', explanation = '', since = activity.started_at || activity.updated_at;
  if (goal?.status === 'completed') phase = '已完成';
  else if (terminal) phase = label(run.status);
  else if (paused) { phase = '已暂停'; explanation = '可补充指导，再继续此研究员。'; }
  else if (goal?.status === 'blocked') { phase = '等待你补充'; explanation = goal.blocker || ''; }
  else if (run.worker_alive === false) { phase = '执行器未运行'; explanation = '后台执行服务未运行，请点击恢复执行。'; }
  else if (agent?.state === 'failed') { phase = '执行异常'; explanation = agent.error || '查看下方失败记录。'; }
  else if (job && (agent?.state === 'waiting' || tools.some((t) => t.name === 'wait_for_job'))) {
    phase = ['queued', 'pending', 'starting'].includes(job.status) ? '作业排队中' : '等待作业完成';
    explanation = job.progress?.message || job.progress_message || `作业 ${job.job_id}`;
    since = job.started_at || job.created_at;
  } else if (tools.length || activity.type === 'tool_started') {
    phase = toolName(tools.at(-1)?.name || activity.tool);
    explanation = tools.length > 1 ? `${tools.length} 项操作仍在进行中。` : '操作正在进行，完成后会继续回复。';
    since = tools.at(-1)?.started_at || since;
  } else if (activity.api_available === false) {
    phase = '模型服务暂不可用'; explanation = '运行时报告连接不可用，等待自动重试或恢复。';
  } else if (agent?.state === 'running') phase = '等待模型响应';
  box.append(element('strong', '', phase));
  if (since && !terminal && !paused && goal?.status !== 'completed') {
    const line = element('p', 'muted', '当前阶段已持续 '); line.append(elapsed(since)); box.append(line);
  }
  if (explanation) box.append(element('p', '', explanation));
  const last = activity.last_progress_at || activity.updated_at;
  if (last) {
    const line = element('p', 'muted', '最近活动距今 '); line.append(elapsed(last)); box.append(line);
    if (!terminal && !paused && goal?.status !== 'completed' && goal?.status !== 'blocked') {
      const warning = element('p', 'trace-stale', '超过 1 分钟没有新活动。可能仍在等待模型或工具；仅凭时间不能判定卡死。');
      warning.dataset.staleAfter = String(new Date(timestamp(last)).getTime() + 60000);
      warning.hidden = Date.now() <= Number(warning.dataset.staleAfter);
      box.append(warning);
    }
  }
  return box;
}

function readableToolText(text) {
  if (!text) return '';
  try {
    const value = JSON.parse(text);
    if (value && typeof value === 'object') {
      const command = value.command || value.cmd;
      if (typeof command === 'string') return `${value.cwd ? value.cwd + '\n' : ''}$ ${command}`;
      if (typeof value.aggregatedOutput === 'string') return `${value.exitCode != null ? '退出码 ' + value.exitCode + '\n' : ''}${value.aggregatedOutput}`;
      return JSON.stringify(value, null, 2);
    }
  } catch (_) {}
  return text;
}

function renderAgentTrace(run, agentId, trace) {
  const block = element('section', 'agent-trace');
  block.append(element('h3', '', `${agentName(agentId)} · 执行轨迹`));
  const key = `${run.run_id}/${agentId}`;
  if (trace?.next != null) {
    const more = element('button', 'quiet', '加载更早轨迹'); more.disabled = trace.loading;
    more.addEventListener('click', () => loadAgentTrace(run.run_id, agentId, true)); block.append(more);
  }
  if (trace?.error) {
    block.append(element('p', 'error', trace.error));
    const retry = element('button', 'quiet', '重试加载');
    retry.addEventListener('click', () => loadAgentTrace(run.run_id, agentId)); block.append(retry);
  }
  const events = [...(trace?.entries.values() || [])].sort((a, b) => a.sequence - b.sequence);
  const calls = new Map(), items = [];
  for (const entry of events) {
    if (['tool_started', 'tool_completed'].includes(entry.kind)) {
      let call = calls.get(entry.call_id);
      if (!call) { call = {...entry, started_at: entry.started_at || (entry.kind === "tool_started" ? entry.created_at : null), finished_at: entry.kind === "tool_completed" ? entry.created_at : null}; calls.set(entry.call_id, call); items.push(call); }
      else Object.assign(call, entry, {created_at: call.created_at,
        arguments: entry.arguments || call.arguments, started_at: entry.started_at || call.started_at,
        finished_at: entry.kind === "tool_completed" ? entry.created_at : call.finished_at});
    } else {
      if (['agent_suspended', 'agent_failed', 'agent_finished'].includes(entry.kind))
        for (const call of calls.values()) if (call.status === 'running') { call.status = entry.kind === 'agent_finished' ? 'ended' : 'interrupted'; call.finished_at = entry.created_at; }
      items.push(entry);
    }
  }
  const titles = {assistant_output: '执行说明', user_message: '你的指导', agent_message: '研究员回复',
    api_availability: '模型连接', goal_updated: '目标进展', agent_failed: '执行失败',
    agent_suspended: '执行中断', agent_finished: '本轮结束', agent_running: '开始执行', job_submitted: '提交作业', job_completed: '作业返回'};
  for (const entry of items) {
    if (entry.call_id) {
      const id = `${key}/${entry.call_id}`;
      const row = element('details', 'trace-tool'); row.open = state.traceExpansion.has(id);
      const status = {running: '尚未返回', completed: '已返回', failed: '失败', interrupted: '中断，未收到结果', ended: '本轮已结束，未记录返回', background: '已返回，后台执行中'}[entry.status] || entry.status;
      let preview = entry.arguments;
      try { const args = JSON.parse(preview); preview = args.command || args.cmd || args.path || args.filename || args.patch || args.input || args.candidate_id || ''; } catch (_) {}
      preview = typeof preview === 'string' ? preview.replace(/\s+/g, ' ').slice(0, 140) : '';
      const summary = element('summary', '', `${toolName(entry.name)}${preview ? ' · ' + preview : ''} · ${status} · ${time(entry.created_at)}`);
      row.append(summary);
      if (entry.started_at) {
        const timing = element('p', 'muted', '调用耗时 ');
        timing.append(elapsed(entry.started_at, entry.finished_at || undefined)); row.append(timing);
      }
      row.append(element('pre', 'trace-code', readableToolText(entry.arguments) || '历史记录未保存调用参数。'));
      if (entry.result) row.append(element('pre', 'trace-code', readableToolText(entry.result)));
      else if (entry.kind === 'tool_completed') row.append(element('p', 'muted', '此记录没有公开输出摘要。'));
      row.addEventListener('toggle', () => {
        if (!row.isConnected) return;
        if (row.open) state.traceExpansion.add(id); else state.traceExpansion.delete(id);
      });
      block.append(row);
    } else {
      const row = element('article', `trace-message ${entry.kind}`);
      row.append(element('small', 'muted', `${titles[entry.kind] || entry.kind} · ${time(entry.created_at)}`));
      const content = element('div', 'message-content preserve');
      appendFormattedText(content, entry.text || '');
      row.append(content);
      if (entry.job_id) {
        const link = element('button', 'quiet', '查看作业与日志');
        link.addEventListener('click', () => selectWork(run, `job:${entry.job_id}`)); row.append(link);
      }
      block.append(row);
    }
  }
  if (!items.length) block.append(element('p', 'muted', trace?.loading ? '正在读取轨迹…' : '本页暂无公开执行记录，可加载更早记录。'));
  return block;
}
