"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  runId: new URLSearchParams(window.location.search).get("run") ||
    localStorage.getItem("rsi.workbench.run") || null,
  run: null,
  runs: [],
  events: new Map(),
  cursors: new Map(),
  selected: new Map(),
  assignmentExpansion: new Map(),
  graphSignature: "",
  detailSignature: "",
  messageSignature: "",
  tab: "chat",
  polling: false,
  sending: false,
  busy: false,
  drafts: new Map(),
  replyTargets: new Map(),
  graphRunId: null,
  detailNodeId: null,
  refreshSequence: 0,
  latestAppliedSequence: 0,
  creating: false,
  models: [],
  modelDrafts: new Map(),
  modelFormRun: null,
  modelSavingRun: null,
};
const emptyChat = $("messages").firstElementChild.cloneNode(true);
const runDropdown = new WorkbenchDropdown($("mobile-runs"), {
  label: "选择研究", placeholder: "暂无研究", onChange: (value) => selectRun(value),
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
    const supported = state.models.find((model) => model.id === value)?.efforts || [];
    if (!supported.includes(draft.reasoning_effort)) draft.reasoning_effort = "medium";
    renderModelSettings();
  },
});
const effortDropdown = new WorkbenchDropdown($("reasoning-effort"), {
  label: "推理强度",
  onChange: (value) => { modelDraft().reasoning_effort = value; renderModelSettings(); },
});
const effortLabels = {
  none: "无推理 · none", low: "低 · low", medium: "中 · medium",
  high: "高 · high", xhigh: "很高 · xhigh", max: "最高 · max",
};

function modelDraft() {
  if (!state.modelDrafts.has(state.runId)) {
    const saved = state.run?.model_settings || {};
    state.modelDrafts.set(state.runId, {
      base_url: saved.base_url || "", model: saved.model || "",
      reasoning_effort: saved.reasoning_effort || "",
    });
  }
  return state.modelDrafts.get(state.runId);
}

function modelSettingsChanged() {
  const saved = state.run?.model_settings;
  const draft = state.modelDrafts.get(state.runId);
  return !!saved?.editable && !!draft &&
    ["base_url", "model", "reasoning_effort"].some((key) => draft[key] !== saved[key]);
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
  $("model-settings-summary").textContent = `${saved.model} · ${saved.reasoning_effort}${saved.editable ? "" : " · 已锁定"}`;
  if ($("model-base-url").value !== draft.base_url) $("model-base-url").value = draft.base_url;
  $("model-base-url").disabled = disabled;
  const models = state.models.map((model) => ({ value: model.id, text: model.id }));
  if (!models.some((model) => model.value === draft.model))
    models.push({ value: draft.model, text: draft.model, disabled: true });
  modelDropdown.update(models, draft.model, disabled);
  const efforts = [...(state.models.find((model) => model.id === draft.model)?.efforts || [])];
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
        label(run.status),
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
  runDropdown.update(
    state.runs.map((run) => ({
      value: run.run_id, text: run.objective || "未命名研究", description: label(run.status),
    })),
    state.runId,
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
  const url = new URL(window.location.href);
  url.searchParams.set("run", runId);
  window.history.replaceState(null, "", url);
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
  renderModelSettings();
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  $("objective").textContent =
    run.objective || run.brief?.objective || "未命名研究";
  $("run-short").textContent = run.run_id;
  $("workspace").classList.toggle("discussing", run.status === "planned");
  const needsResume = run.worker_alive === false && (
    run.status === "running" || (run.status === "planned" && run.pending_agents?.length)
  );
  $("run-status").textContent = needsResume ? "待恢复"
    : run.status === "running" && run.execution_setup === "pending"
    ? "研究中 · 接入准备" : label(run.status);
  $("run-status").className = `status ${run.status}`;
  $("start").hidden = run.status !== "planned" || run.intake?.status !== "ready";
  $("start").disabled = state.busy || run.agents?.some(
    (agent) => agent.agent_id === "interaction" && agent.state === "running",
  );
  $("pause").hidden = run.status !== "running";
  $("resume").hidden = run.status !== "paused" && !(run.status === "stopped" && run.started_at) && !needsResume;
  $("resume").textContent = run.status === "stopped" ? "恢复研究" : needsResume
    ? (run.status === "planned" ? "继续对话" : "恢复执行") : "继续";
  $("stop").hidden = !["planned", "running", "paused"].includes(run.status);
  $("stop").textContent = run.status === "planned" ? "结束对话" : "停止";
  $("best-score").textContent = score(run.best?.score);
  $("best-caption").textContent = run.best
    ? `${run.brief?.verification?.metric || "验证分数"} · 同一验证条件`
    : "尚无可比较的验证结果";
  replace(
    "baseline-score",
    document.createTextNode(score(run.baseline?.score)),
    element("i", "", "/"),
    document.createTextNode(score(run.final?.score)),
  );
  $("baseline-caption").textContent = run.baseline
    ? "起点结果 / 最终确认"
    : "暂无起点评测结果";
  const nodes = run.nodes || [];
  $("node-count").textContent = integer(nodes.length);
  $("node-caption").textContent =
    `${nodes.filter((node) => node.state === "completed").length} 已完成 · ${(run.agents || []).filter((agent) => agent.direction).length} 个研究方向`;
  $("model-count").textContent =
    `${integer(run.budget?.model_calls ?? 0)} / ${budgetLimit(run.brief?.limits?.max_model_calls)}`;
  $("token-count").textContent =
    `${integer(run.budget?.total_tokens ?? 0)} tokens${run.budget?.reserved_tokens ? ` · ${integer(run.budget.reserved_tokens)} 已预留` : ""}`;
  $("plan").textContent =
    run.plan || "研究开始后，RSI 会在这里说明当前方向和下一步。";
  $("plan").classList.toggle("muted", !run.plan);
  $("score-metric").hidden = $("baseline-metric").hidden = !run.brief?.verification;
  $("metrics").classList.toggle("without-score", !run.brief?.verification);
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
  const ended = !["planned", "running", "paused"].includes(run.status);
  const responding = (run.agents || []).find((agent) => agent.agent_id === target.recipient);
  $("chat-input").disabled = ended;
  $("send-message").disabled = ended || state.sending || target.stale;
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
    : state.sending
    ? "正在发送…"
    : responding?.state === "running"
      ? `${agentName(target.recipient)} 正在处理…`
      : "Enter 发送 · Shift+Enter 换行";
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
    state.sending || !["planned", "running", "paused"].includes(run.status));
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
    retry.disabled = !["planned", "running"].includes(run.status);
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
    const latest = (state.events.get(run.run_id) || []).at(-1);
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

function renderGraph(run) {
  // Jobs are display objects, not persisted ResearchIterations or research outcomes.
  const nodes = [...(run.nodes || []), ...(run.jobs || []).map((job) => ({
    iteration_id: `job:${job.job_id}`, agent_id: job.agent_id, state: job.status,
    parent_iteration_id: job.iteration_id, title: jobTitle(job), job,
  }))];
  const active = nodes.filter((node) => node.job ? activeJob(node.job) : activeNode(node));
  $("graph-count").textContent = `${run.nodes?.length || 0} 个研究节点 · ${run.jobs?.length || 0} 个作业 · ${active.length} 个待执行 / 进行中`;
  $("graph-active").hidden = !active.length;
  const selected = state.selected.get(run.run_id);
  const signature = JSON.stringify([
    run.run_id,
    nodes,
    run.edges,
    run.agents?.map((agent) => [agent.agent_id, agent.direction, agent.state, agent.wait_reason]),
    run.agent_activity,
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
  if (!nodes.length) {
    viewport.style.height = "210px";
    return;
  }
  const byId = new Map(nodes.map((node) => [node.iteration_id, node]));
  const agents = [...new Set(nodes.map((node) => node.agent_id))].sort((a, b) => a === b ? 0 : a === "rsi" ? -1 : b === "rsi" ? 1 : 0);
  viewport.style.height = `${Math.min(520, Math.max(210, agents.length * 174 + 25))}px`;
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
      (agentId === "rsi" ? "RSI 执行" : run.agents?.find((agent) => agent.agent_id === agentId)?.direction) ||
      agentName(agentId);
    const lane = element("div", "graph-lane", direction);
    lane.title = direction;
    lane.style.top = `${row * 174 + 20}px`;
    canvas.append(lane);
    let nextColumn = 0, attempt = 0;
    nodes
      .filter((node) => node.agent_id === agentId)
      .sort((a, b) => {
        // Place an execution immediately after the iteration that submitted it.
        const order = (node) => node.job?.iteration_id
          ? nodes.findIndex((n) => n.iteration_id === node.job.iteration_id) + 0.5
          : nodes.indexOf(node);
        return order(a) - order(b);
      })
      .forEach((node) => {
        const column = Math.max(nextColumn, depth(node));
        nextColumn = column + 1;
        widest = Math.max(widest, column + 1);
        const position = { x: 24 + column * 246, y: row * 174 + 46 };
        positions.set(node.iteration_id, position);
        const ongoing = node.job ? activeJob(node.job) : activeNode(node);
        const button = element(
          "button",
          `graph-node ${node.job ? "execution-node" : ""} ${ongoing ? "active-node" : ""} ${selected === node.iteration_id ? "selected" : ""}`,
        );
        button.dataset.node = node.iteration_id;
        button.style.left = `${position.x}px`;
        button.style.top = `${position.y}px`;
        const top = element("div", "node-top");
        top.append(element("span", "", node.job ? "执行作业" : `研究 ${++attempt}`), badge(node.state));
        button.append(
          top,
          element(
            "h3",
            "",
            node.title || node.assignment?.instruction || "研究尝试",
          ),
        );
        if (node.job) {
          button.append(element("p", "node-progress", progressText(node.job) || node.job.progress?.stage || "查看执行状态"), progressBar(node.job));
        } else if (ongoing) {
          const owner = run.agents?.find((agent) => agent.agent_id === node.agent_id);
          button.append(element("p", "node-progress", owner?.state === "running" ? agentAction(run, owner) : owner ? agentStatus(run, owner) : "执行本轮任务"));
        }
        if (typeof node.verification?.score === "number")
          button.append(
            element(
              "p",
              "node-score",
              score(node.verification.score),
            ),
          );
        button.setAttribute(
          "aria-pressed",
          String(selected === node.iteration_id),
        );
        button.addEventListener("click", () => selectWork(run, node.iteration_id));
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
        kind: node.job ? "execution" : "parent",
      });
  for (const edge of edges) {
    const source = positions.get(edge.source),
      target = positions.get(edge.target);
    if (!source || !target || edge.source === edge.target) continue;
    const x1 = source.x + 214,
      y1 = source.y + 46,
      x2 = target.x,
      y2 = target.y + 46;
    const line = element(
      "div",
      `graph-edge ${["reference", "merge", "execution"].includes(edge.kind) ? edge.kind : "parent"}`,
    );
    Object.assign(line.style, {
      left: `${x1}px`,
      top: `${y1}px`,
      width: `${Math.hypot(x2 - x1, y2 - y1)}px`,
      transform: `rotate(${Math.atan2(y2 - y1, x2 - x1)}rad)`,
    });
    line.setAttribute("aria-hidden", "true");
    line.title =
      edge.label || (edge.kind === "parent" ? "继续探索" : edge.kind === "execution" ? "执行作业" : "引用 / 合并经验");
    canvas.prepend(line);
  }
  canvas.style.width = `${widest * 246 + 22}px`;
  canvas.style.height = `${agents.length * 174 + 16}px`;
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
function renderNode(run) {
  const job = run.jobs?.find((item) => `job:${item.job_id}` === state.selected.get(run.run_id));
  if (job) {
    renderJobDetail(run, job);
    return;
  }
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
      element("h3", "", "选择一个研究节点或作业"),
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
  const blocks = [heading, assignmentDetails(run, node)];
  if (node.report?.message) {
    const report = element("section", "detail-section");
    const body = element("div", "message-content");
    appendFormattedText(body, node.report.message);
    report.append(element("h4", "", "本轮报告"), body);
    blocks.push(report);
  } else if (node.report) {
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
          ? "尚未提交报告。"
          : "此节点未产生报告。",
      ),
    );
  if (node.verification)
    blocks.push(
      section(
        "验证记录",
        `${score(node.verification.score)} · ${phaseName(node.verification.phase)}`,
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
    const parent = element("button", "quiet", "查看所属研究节点 →");
    parent.addEventListener("click", () => selectWork(run, job.iteration_id));
    blocks.push(parent);
  }
  replace("node-panel", ...blocks);
  $("node-panel").scrollTop = scroll;
}

function renderMessages(run) {
  const messages = run.messages || [];
  const busy = (run.agents || []).filter(agent => agent.state === "running");
  const signature = JSON.stringify([run.run_id, messages, busy, run.agent_activity]);
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
    if (message.sender === "user" && message.reply_to) {
      const question = messages.find((item) => item.message_id === message.reply_to);
      if (question) block.insertBefore(
        element("p", "message-reply", `回复：${questionPreview(question, 60)}`), content);
    }
    return block;
  });
  for (const agent of busy) {
    const activity = run.agent_activity?.[agent.agent_id];
    const action = agentAction(run, agent);
    const progress = element("article", "message assistant progress-message");
    progress.setAttribute("role", "status");
    const name = agent.direction || agentName(agent.agent_id);
    progress.append(element("div", "message-label", `${name} · 进行中`),
      element("p", "", action),
      element("time", "muted", activity?.updated_at ? `最近活动 ${time(activity.updated_at)}` : ""));
    blocks.push(progress);
  }
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
    ["Research Agents 上限", limits.max_agents],
    ["节点上限", budgetLimit(limits.max_nodes)],
    ["模型调用上限", budgetLimit(limits.max_model_calls)],
    ["Token 上限", budgetLimit(limits.max_total_tokens)],
    ["验证次数上限", budgetLimit(limits.max_verify_calls)],
    ["研究时长上限", limits.max_wall_seconds === null ? "不限" : `${budgetLimit(limits.max_wall_seconds)} 秒`],
    ["作业并发上限", budgetLimit(limits.max_jobs)],
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

async function createConversation(message = "") {
  if (state.creating) return;
  state.creating = true;
  $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = true;
  notice();
  try {
    const result = await api("/api/runs", { message, message_id: crypto.randomUUID() });
    if (!result.run_id) throw new Error("未能读取研究记录，请刷新列表。");
    await loadRuns();
    state.runId = null;
    await selectRun(result.run_id);
    switchTab("chat");
    $("welcome-input").value = "";
    $("chat-input").focus();
  } catch (error) {
    notice(error.message);
  } finally {
    state.creating = false;
    $("new-run").disabled = $("mobile-new").disabled = $("welcome-send").disabled = false;
  }
}
$("new-run").addEventListener("click", () => createConversation());
$("mobile-new").addEventListener("click", () => createConversation());
$("welcome-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const message = $("welcome-input").value.trim();
  if (message) createConversation(message);
});
$("welcome-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("welcome-form").requestSubmit();
  }
});
for (const name of ["chat", "node", "brief"])
  $(`${name}-tab`).addEventListener("click", () => switchTab(name));
for (const button of $("controls").querySelectorAll("button"))
  button.addEventListener("click", () => control(button.dataset.action));
$("graph-active").addEventListener("click", () => {
  const active = [...$("graph-canvas").querySelectorAll(".active-node")];
  const current = active.findIndex((node) => node.dataset.node === state.selected.get(state.runId));
  const next = active[(current + 1) % active.length];
  if (!next) return;
  const position = {left: Math.max(0, next.offsetLeft - 24), top: Math.max(0, next.offsetTop - 46)};
  selectWork(state.run, next.dataset.node);
  $("graph-viewport").scrollTo(position);
  [...$("graph-canvas").querySelectorAll("button")].find((node) => node.dataset.node === next.dataset.node)?.focus({preventScroll: true});
});
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
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text || !state.runId || state.sending) return;
  if (modelSettingsChanged() || state.modelSavingRun) {
    notice("请先保存模型设置。");
    return;
  }
  const target = renderReplyTarget(state.run);
  if (target.stale) return;
  const runId = state.runId;
  state.sending = true;
  $("send-message").disabled = true;
  $("chat-status").textContent = "正在发送…";
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      text,
      message_id: crypto.randomUUID(),
      ...(target.reply_to ? { reply_to: target.reply_to } : { recipient: target.recipient }),
    });
    state.drafts.set(runId, "");
    state.replyTargets.delete(runId);
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

async function bootstrap() {
  const config = await api("/api/bootstrap");
  state.token = config.csrf_token;
  state.models = config.models || [];
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
boot();
