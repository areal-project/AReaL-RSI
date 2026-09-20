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
  createEffort: "xhigh",
  modelDrafts: new Map(),
  modelFormRun: null,
  modelSavingRun: null,
  collaborationCatalog: {modes: [], modules: []},
  defaultCollaboration: {mode: "passk", modules: []},
  collaborationDrafts: new Map(),
  collaborationSavingRun: null,
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
    render();
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
  label: "Agent 后端", onChange: (value) => { modelDraft().backend = value; renderModelSettings(); },
});

const collaborationDropdown = new WorkbenchDropdown($("collaboration-mode"), {
  label: "基础流程", onChange: (mode) => {
    state.collaborationDrafts.set(state.runId, selectCollaborationPreset(mode));
    renderCollaboration();
  },
});
const createCollaborationDropdown = new WorkbenchDropdown($("create-collaboration-mode"), {
  label: "协作方式", onChange: (mode) => {
    state.createCollaboration = selectCollaborationPreset(mode);
    renderCreateCollaboration();
  },
});
function collaborationSelection(value) {
  return {mode: value.mode, modules: [...(value.modules || [])].sort()};
}
function collaborationPresets() {
  return state.collaborationCatalog.presets || state.collaborationCatalog.modes.map((mode) => ({...mode, mode: mode.id, modules: []}));
}
function selectCollaborationPreset(id) {
  return collaborationSelection(collaborationPresets().find((preset) => preset.id === id));
}
function matchingCollaborationPreset(selection) {
  return collaborationPresets().find((preset) => JSON.stringify(collaborationSelection(preset)) === JSON.stringify(collaborationSelection(selection)));
}
function collaborationChanged() {
  const draft = state.collaborationDrafts.get(state.runId), saved = state.run?.collaboration;
  return !!draft && !!saved && JSON.stringify(collaborationSelection(draft)) !== JSON.stringify(collaborationSelection(saved));
}
function renderCollaborationFields(prefix, dropdown, selection, disabled, onChange) {
  const catalog = state.collaborationCatalog;
  const preset = matchingCollaborationPreset(selection);
  dropdown.update(collaborationPresets().map((item) => ({value: item.id, text: item.label})), preset?.id || selection.mode, disabled);
  $(`${prefix}description`).textContent = preset?.description || catalog.modes.find((mode) => mode.id === selection.mode)?.description || "";
  const container = $(`${prefix}modules`);
  container.replaceChildren();
  const modules = catalog.modules.filter((module) => module.modes.includes(selection.mode));
  if (!modules.length) container.append(element("p", "muted", "当前没有可选的附加模块。后续模块接入后可在这里组合。"));
  for (const module of modules) {
    const label = element("label", "collaboration-module");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = module.id;
    input.checked = selection.modules.includes(module.id);
    input.disabled = disabled || modules.some((item) => selection.modules.includes(item.id) && (item.requires || []).includes(module.id));
    input.addEventListener("change", () => {
      const selected = new Set(selection.modules);
      if (input.checked) {
        selected.add(module.id);
        for (const dependency of module.requires || []) selected.add(dependency);
      } else selected.delete(module.id);
      onChange({mode: selection.mode, modules: [...selected].sort()});
    });
    const description = element("span", "", `${module.label} · v${module.version}`);
    description.append(element("small", "muted", module.description));
    label.append(input, description);
    container.append(label);
  }
}
function renderCreateCollaboration() {
  renderCollaborationFields("create-collaboration-", createCollaborationDropdown, state.createCollaboration, state.creating,
    (selection) => { state.createCollaboration = selection; renderCreateCollaboration(); });
}
function renderCollaboration() {
  const saved = state.run?.collaboration;
  $("collaboration-settings").hidden = !saved?.mode;
  if (!saved?.mode) return;
  if (!saved.editable) state.collaborationDrafts.delete(state.runId);
  const draft = state.collaborationDrafts.get(state.runId) || collaborationSelection(saved);
  const disabled = !saved.editable || !!state.collaborationSavingRun || state.busy;
  const currentVersion = !saved.manifest || (saved.manifest.modules || []).every((recorded) =>
    state.collaborationCatalog.modules.some((module) => module.id === recorded.id && module.version === recorded.version));
  const label = currentVersion
    ? matchingCollaborationPreset(saved)?.label || [saved.label || saved.mode, ...(saved.modules || [])].join(" + ")
    : `${saved.mode} · 历史协作版本`;
  $("collaboration-summary").textContent = [label, saved.editable ? "启动前可修改" : "已锁定"].join(" · ");
  renderCollaborationFields("collaboration-", collaborationDropdown, draft, disabled, (selection) => {
    state.collaborationDrafts.set(state.runId, selection); renderCollaboration();
  });
  if (!saved.editable) {
    const recorded = new Map((saved.manifest?.modules || []).map((module) => [module.id, module.version]));
    $("collaboration-modules").replaceChildren(...(saved.modules?.length ? saved.modules.map((id) =>
      element("p", "muted", `${id} · ${recorded.has(id) ? `v${recorded.get(id)}` : "历史版本未记录"}`))
      : [element("p", "muted", "未启用附加模块。")]) );
  }
  $("reuse-collaboration").hidden = saved.supported === false;
  $("save-collaboration").hidden = !saved.editable;
  $("save-collaboration").disabled = disabled || !collaborationChanged();
  $("save-collaboration").textContent = state.collaborationSavingRun === state.runId ? "保存中…" : "保存协作配置";
  $("collaboration-status").textContent = saved.editable
    ? (collaborationChanged() ? "有未保存的修改，请保存后开始研究。" : "启动时冻结配置和模块版本；启动后不能修改。")
    : saved.manifest ? `配置指纹 ${saved.manifest.fingerprint.slice(0, 12)} · v${saved.manifest.mode_version}`
    : "历史研究：按原 mode 展示，未补写版本记录。";
}
$("collaboration-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.run?.collaboration?.editable || state.collaborationSavingRun || !collaborationChanged()) return;
  const runId = state.runId, selection = collaborationSelection(state.collaborationDrafts.get(runId));
  state.collaborationSavingRun = runId;
  renderCollaboration();
  notice();
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/collaboration`, selection);
    state.collaborationDrafts.delete(runId);
    await refresh(true);
  } catch (error) { notice(error.message); }
  finally { state.collaborationSavingRun = null; renderCollaboration(); }
});
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
      reasoning_effort: saved.reasoning_effort || "", backend: saved.backend || "standard", api_key: "",
    });
  }
  return state.modelDrafts.get(state.runId);
}

function modelSettingsChanged() {
  const saved = state.run?.model_settings;
  const draft = state.modelDrafts.get(state.runId);
  return !!saved?.editable && !!draft &&
    (!!draft.api_key || ["base_url", "model", "reasoning_effort", "backend"].some((key) => draft[key] !== (saved[key] || (key === "backend" ? "standard" : ""))));
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
  $("model-settings-summary").textContent = `${saved.backend || "standard"} · ${saved.model} · ${saved.reasoning_effort}${saved.editable ? "" : " · 已锁定"}`;
  if ($("model-base-url").value !== draft.base_url) $("model-base-url").value = draft.base_url;
  $("model-base-url").disabled = disabled;
  $("model-name").value = draft.model;
  $("model-name").disabled = disabled;
  $("model-api-key").value = draft.api_key || "";
  $("model-api-key").disabled = disabled;
  $("model-api-key").placeholder = saved.has_credentials ? "已配置 · 留空沿用当前密钥" : "请输入 API Key";
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
  cancelled: "已取消",
  created: "已创建",
  starting: "启动中",
  timed_out: "已超时",
  lost: "执行失联",
};
const eventNames = {
  run_created: "创建研究",
  run_started: "研究开始",
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
}
const activeJob = (job) => ["pending", "queued", "starting", "running"].includes(job.status);
const activeNode = (node) => ["pending", "running"].includes(node.state);
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
  const activity = run.agent_activity?.[agent.agent_id];
  if (activity?.type === "model_retry") {
    const at = time(activity.next_retry_at * 1000);
    return `API 暂不可用（${activity.error_code}），已失败 ${activity.attempt} 次；预计 ${at} 自动重试，当前进度已保存`;
  }
  if (activity?.type === "model_attempt" && activity.attempt > 1)
    return `正在第 ${activity.attempt} 次尝试连接 API，等待模型响应`;
  const names = {read: "读取文件", grep: "检索代码", glob: "查找文件", bash: "执行命令",
    write: "写入文件", edit: "修改文件", spawn_researcher: "安排研究任务",
    get_state: "查看研究进展", list_records: "查找研究记录", read_record: "阅读研究记录",
    save_candidate: "保存候选版本", checkout_candidate: "切换候选版本",
    verify_candidate: "提交验证", run_job: "运行实验", wait_for_job: "等待实验结果",
    job_status: "查看作业状态", read_job_output_file: "阅读实验反馈",
    save_evidence: "记录研究发现", share_evidence: "共享研究发现",
    update_plan: "调整研究计划", assign: "安排下一轮任务", message_researcher: "补充研究要求",
    wait: "等待后续消息", submit_report: "提交本轮报告"};
  return activity?.type === "tool_started" ? names[activity.tool] || `执行 ${activity.tool}`
    : activity?.type === "tool_completed" ? `已完成${names[activity.tool] || activity.tool}，继续处理`
    : "模型正在处理任务";
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
async function api(path, body, signal) {
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
    throw new Error(error.error || `请求失败（${response.status}）`);
  }
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
    if (Number.isInteger(run.event_cursor) && run.event_cursor < after)
      state.events.clear();
    for (const event of run.events || [])
      state.events.set(event.sequence, event);
    while (state.events.size > 100)
      state.events.delete(state.events.keys().next().value);
    state.cursors.set(runId, run.event_cursor ?? after);
    state.run = run;
    state.runCache.set(runId, run);
    // Keep a bounded set of full projections, retaining independent event cursors.
    if (state.runCache.size > 12) {
      const oldest = [...state.runCache.keys()].find((key) => key !== runId);
      state.runCache.delete(oldest);
    }
    $("run-loading").hidden = true;
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
    if (error.name === "AbortError" || state.runId !== runId) return;
    connected(false);
    if (force) notice(error.message);
  } finally {
    if (state.refreshController === controller) state.polling = false;
  }
}

function render() {
  const run = state.run;
  if (!run) return;
  renderModelSettings();
  renderCollaboration();
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  $("objective").textContent =
    run.objective || run.brief?.objective || "未命名研究";
  $("run-short").textContent = run.run_id;
  $("workspace").classList.toggle("discussing", run.status === "planned");
  const needsResume = run.worker_alive === false && (
    run.status === "running" || (run.status === "planned" && run.pending_agents?.length)
  );
  $("run-status").textContent = run.finalization?.status === "running" ? "最终复测与归档中"
    : run.finalization?.status === "failed" ? "收尾失败 · 可恢复"
    : run.finalization?.status === "completed" ? (run.status === "succeeded" ? "已达标" : run.brief?.verification?.target == null ? "已完成" : "已完成 · 未达标")
    : needsResume ? "待恢复"
    : run.status === "running" && run.execution_setup === "pending"
    ? "研究中 · 接入准备" : label(run.status);
  $("run-status").className = `status ${run.status}`;
  $("start").hidden = run.status !== "planned" || run.intake?.status !== "ready";
  $("start").disabled = state.busy || run.agents?.some(
    (agent) => agent.agent_id === "interaction" && agent.state === "running",
  );
  $("pause").hidden = run.status !== "running";
  $("resume").hidden = run.status !== "paused" && !(run.status === "stopped" && run.started_at) && !needsResume;
  $("resume").hidden ||= ["running", "completed"].includes(run.finalization?.status);
  $("resume").textContent = run.finalization?.status === "failed" ? "恢复收尾" : run.status === "stopped" ? "恢复研究" : needsResume
    ? (run.status === "planned" ? "继续对话" : "恢复执行") : "继续";
  $("stop").hidden = !["planned", "running", "paused"].includes(run.status)
    && run.finalization?.status !== "running";
  $("stop").textContent = run.status === "planned" ? "结束对话" : "停止";
  if (run.collaboration?.supported === false) {
    for (const id of ["start", "pause", "resume"]) $(id).hidden = true;
  }
  $("delete-run").disabled = state.busy || state.sending.has(run.run_id) || !!state.modelSavingRun;
  $("delete-run").title = deletionBlocked(run) ? "请先停止研究再删除" : "删除这项研究的本地记录";
  $("best-score").textContent = score(run.best?.score);
  $("final-score").textContent = score(run.final?.score);
  const researchers = ResearchGraph.researchers(run);
  $("node-count").textContent = integer(researchers.length);
  $("runtime-turn-count").textContent = integer(run.budget?.runtime_turns ?? 0);
  $("plan").textContent =
    run.plan || "研究开始后，RSI 会在这里说明当前方向和下一步。";
  $("plan").classList.toggle("muted", !run.plan);
  $("score-metric").hidden = $("final-metric").hidden = !run.brief?.verification;
  $("metrics").classList.toggle("without-score", !run.brief?.verification);
  $("run-elapsed").replaceChildren(elapsed(run.started_at, run.finished_at));
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
  const ended = run.collaboration?.supported === false || !["planned", "running", "paused"].includes(run.status);
  const responding = (run.agents || []).find((agent) => agent.agent_id === target.recipient);
  $("chat-input").disabled = ended;
  $("send-message").disabled = ended || state.sending.has(run.run_id) || target.stale;
  $("chat-input-label").textContent = `向 ${agentName(target.recipient)} 发送消息`;
  $("chat-input").placeholder = target.reply_to
    ? "回答这个问题，或说明处理进展…"
    : "补充想法、提供环境，或询问进展…";
  $("chat-status").textContent = ended
    ? "研究已结束，记录保留供查看"
    : target.stale
    ? "该问题已回复，请重新选择发送对象"
    : run.status === "paused"
    ? "研究已暂停，消息将在继续后处理"
    : state.sending.has(run.run_id)
    ? "正在发送…"
    : responding?.state === "running"
      ? `${agentName(target.recipient)} 正在处理…`
      : "Enter 发送 · Shift+Enter 换行";
  renderChatFeedback(run, target, responding);
  if (run.budget_stop_reason) notice(run.budget_stop_reason);
}

function agentName(id) {
  return {rsi: "RSI Agent", interaction: "Interaction Agent"}[id]
    || state.run?.agents?.find((agent) => agent.agent_id === id)?.direction || "Research Agent";
}
function questionPreview(question, length = 48) {
  const text = (question.question || question.text || "请查看对话").replace(/\s+/g, " ");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}
function renderReplyTarget(run) {
  const questions = (run.waiting_for_user || []).filter((q) => q.message_id);
  const choices = [
    ...questions.map((q) => ({
      value: q.message_id,
      text: `回复 ${agentName(q.agent_id)} · ${questionPreview(q, 24)}`,
      recipient: q.agent_id,
      reply_to: q.message_id,
    })),
    { value: "interaction", recipient: "interaction", text: "Interaction Agent · 新消息" },
    ...(run.status !== "planned"
      ? [{ value: "rsi", recipient: "rsi", text: "RSI Agent · 新消息" }] : []),
  ];
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
      text: "问题已回复，请重新选择", disabled: true };
    choices.unshift(target);
  }
  recipientDropdown.update(choices, selected.value,
    state.sending.has(run.run_id) || !["planned", "running", "paused"].includes(run.status));
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
  const preparing = run.status === "planned";
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
      line.append(element("p", "", node.title || node.assignment?.instruction || "执行本轮研究任务"));
      line.dataset.work = node.iteration_id;
      line.addEventListener("click", () => selectWork(run, node.iteration_id));
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
    top.append(element("span", "", "当前状态"), badge(owner?.state || node?.state));
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
  const job = run.jobs?.find((item) => `job:${item.job_id}` === state.selected.get(run.run_id));
  if (job) {
    renderJobDetail(run, job);
    return;
  }
  const node = run.nodes?.find(
    (item) => item.iteration_id === state.selected.get(run.run_id),
  );
  const budget = run.budget?.researchers?.[node?.agent_id];
  const signature = JSON.stringify([run.run_id, run.finished_at, node, budget]);
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
    badge(node.state),
    element("h3", "", "本轮任务"),
    element("p", "muted", node.iteration_id),
  );
  const [started, finished] = nodeTimes(run, node);
  const timing = element("p", "detail-timing");
  if (budget) timing.append(element("span", "", `有效时间 ${(budget.effective_seconds / 60).toFixed(1)} / ${(budget.limit_seconds / 60).toFixed(0)} 分钟，剩余 ${(budget.remaining_effective_seconds / 60).toFixed(1)} 分钟；`));
  timing.append(document.createTextNode(budget ? "实际历时 " : "本轮历时 "), elapsed(started, finished));
  if (budget) timing.append(element("small", "muted", `GPU 排队 ${(budget.gpu_queue_seconds / 60).toFixed(1)} 分钟 · API 故障等待 ${(budget.api_wait_seconds / 60).toFixed(1)} 分钟`));
  if (started) timing.append(element("small", "muted", `开始 ${new Date(timestamp(started)).toLocaleString("zh-CN")}${finished ? ` · 结束 ${new Date(timestamp(finished)).toLocaleString("zh-CN")}` : " · 进行中"}`));
  const blocks = [heading, timing, assignmentDetails(run, node)];
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
  const open = ["planned", "running"].includes(run.status);
  const unserved = run.worker_alive === false && (pending || working);
  box.hidden = !open || !(sending || pending || working);
  if (box.hidden) return;
  const status = sending ? "正在发送消息…" : unserved ? "消息已保存，等待恢复服务"
    : working ? `等待 ${agentName(target.recipient)} 回复` : `消息已收到，等待 ${agentName(target.recipient)} 处理`;
  const row = element("div", "chat-feedback-title");
  if (!unserved) { const spinner = element("span", "spinner"); spinner.setAttribute("aria-hidden", "true"); row.append(spinner); }
  row.append(document.createTextNode(status));
  const detail = unserved ? "点击继续对话或恢复执行。" : working ? agentAction(run, agent) : "";
  box.replaceChildren(row, element("small", "muted", detail));
}

function renderMessages(run) {
  const messages = [...(run.messages || [])];
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
            : "Interaction Agent",
      ),
      element("time", "", time(message.created_at)),
    );
    const content = element("div", "message-content");
    if (message.sender === "user") content.textContent = message.text;
    else appendFormattedText(content, message.text || "");
    block.append(heading, content);
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
    ["GPU", limits.gpu_ids?.join(", ") || "尚未配置"],
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
  if (verification?.description)
    blocks.push(section("验证条件", verification.description));
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
}

async function control(action, params = {}) {
  if (!state.runId || state.busy) return;
  if (action === "start" && (collaborationChanged() || state.collaborationSavingRun)) {
    notice("请先保存协作配置。");
    return;
  }
  if (action === "start" && (modelSettingsChanged() || state.modelSavingRun)) {
    notice("请先保存模型设置。");
    return;
  }
  state.busy = true;
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
      state.drafts, state.replyTargets, state.outgoing, state.modelDrafts]) cache.delete(runId);
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

async function createConversation(message = "", modelSettings, collaboration) {
  if (state.creating) return;
  state.creating = true;
  $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = true;
  notice();
  try {
    $("create-submit").disabled = true;
    $("create-submit").textContent = "正在创建…";
    const result = await api("/api/runs", { message, message_id: crypto.randomUUID(), ...(modelSettings ? {model_settings: modelSettings} : {}), ...(collaboration ? {collaboration} : {}) });
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
    $("create-submit").textContent = "创建研究";
    state.creating = false;
    $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = false;
  }
}
const createPreset = new WorkbenchDropdown($("create-model-preset"), {
  label: "常用模型", onChange: (value) => { $("create-model").value = value; updateCreateModels(); },
});
const createBackend = new WorkbenchDropdown($("create-backend"), {
  label: "Agent 后端", onChange: (value) => { state.createBackend = value; },
});
const createEffort = new WorkbenchDropdown($("create-effort"), {
  label: "推理强度", onChange: (value) => { state.createEffort = value; },
});
function updateCreateModels() {
  const model = $("create-model").value;
  const efforts = state.models.find((item) => item.id === model)?.efforts || Object.keys(effortLabels);
  if (!efforts.includes(state.createEffort)) state.createEffort = "medium";
  createPreset.update(state.models.map((item) => ({value: item.id, text: item.id})), model);
  createEffort.update(efforts.map((value) => ({value, text: effortLabels[value]})), state.createEffort);
}
function openCreate(message = "", collaboration = state.defaultCollaboration) {
  if (state.creating) return;
  const defaults = state.defaultModelSettings;
  $("create-base-url").value = defaults.base_url || "https://4ipic.com";
  $("create-model").value = defaults.model || "gpt-5.6-luna";
  $("create-api-key").value = "";
  $("create-api-key").placeholder = defaults.has_credentials ? "留空使用默认密钥（已配置）" : "请输入 API Key";
  $("create-message").value = message;
  $("create-error").hidden = true;
  state.createEffort = defaults.reasoning_effort || "xhigh";
  state.createBackend = defaults.backend || "standard";
  createBackend.update(backendOptions, state.createBackend);
  state.createCollaboration = collaborationSelection(collaboration);
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
    base_url: $("create-base-url").value.trim(), api_key: $("create-api-key").value.trim(),
    model: $("create-model").value.trim(), reasoning_effort: state.createEffort, backend: state.createBackend,
  }, collaborationSelection(state.createCollaboration));
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
    await api(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      text,
      message_id: message.message_id,
      ...(target.reply_to ? { reply_to: target.reply_to } : { recipient: target.recipient }),
    });
    state.drafts.set(runId, "");
    state.replyTargets.delete(runId);
    if (state.runId === runId && $("chat-input").value.trim() === text)
      $("chat-input").value = "";
    message.delivery = null;
    if (state.runId === runId) {
      await refresh(true);
      $("messages").scrollTop = $("messages").scrollHeight;
    }
  } catch (error) {
    message.delivery = "failed";
    if (state.runId === runId) notice(error.message);
  } finally {
    state.sending.delete(runId);
    if (state.run) render();
  }
});
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("chat-form").requestSubmit();
  }
});

async function bootstrap() {
  const config = await api("/api/bootstrap");
  const branch = config.branch || "";
  document.title = `RSI · 研究工作台${branch ? ` — ${branch}` : ""}`;
  $("workbench-caption").textContent = branch || "研究工作台";
  $("workbench-name").textContent = branch ? `研究空间 · ${branch}` : "研究空间";
  state.token = config.csrf_token;
  state.models = config.models || [];
  state.defaultModelSettings = config.default_model_settings || {};
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
