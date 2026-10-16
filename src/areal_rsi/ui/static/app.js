"use strict";
const colors=["#64d9c3","#91b8ff","#ffc984","#d5a1f6","#ee91b0","#b5d78d","#72c7ec","#deb687"];
const states={planned:"待启动",running:"运行中",succeeded:"运行已完成",achieved:"目标达成",budget_exhausted:"预算耗尽",paused:"已暂停",blocked:"受阻",needs_input:"待补充",ready:"待确认",failed:"失败",cancelled:"已停止"};
const phases={waiting:"等待首轮",research:"分析当前版本",evaluate_train:"执行 TRAIN 评测",evaluate_validate:"执行 VALIDATE 评测",propose:"提出修改",build:"构建候选版本",select:"选择下一轮版本",iteration_completed:"本轮完成",completed:"研究完成",failed:"执行失败",cancelled:"已停止",pruned:"已剪枝"};
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const percent=value=>typeof value==="number"&&Number.isFinite(value)?`${(value*100).toFixed(1)}%`:"—";
const stamp=value=>value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"—";
const badge=state=>`<span class="badge ${esc(state)}">${esc(states[state]||state)}</span>`;
function hashRun(){try{return decodeURIComponent(location.hash.slice(1));}catch{return "";}}
let selected=hashRun(),busy=false,lastRendered="",timeAxis=false;
function chart(run){
  const w=900,h=230,left=48,top=16,bottom=38,right=40;
  const start=Date.parse(run.started_at)||0;
  const seconds=value=>Math.max(0,(Date.parse(value)-start)/1000);
  const elapsed=step=>step.elapsed_seconds??seconds(step.finished_at);
  const finalSeconds=run.final_acceptance?(run.final_elapsed_seconds??seconds(run.final_acceptance.at)):null;
  const finalIteration=Math.max(0,...run.agents.flatMap(a=>a.history.map(s=>s.iteration)));
  const max=timeAxis?Math.max(1,...run.agents.flatMap(a=>a.history.map(elapsed)),...(Number.isFinite(finalSeconds)?[finalSeconds]:[])):Math.max(1,run.max_iterations);
  const x=i=>left+(w-left-right)*i/max,y=score=>top+(h-top-bottom)*(1-score);
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="各研究线逐轮 VALIDATE 得分，横轴为${timeAxis?"执行秒数":"迭代轮次"}">`;
  for(const v of [0,.5,1])svg+=`<line class="grid" x1="${left}" y1="${y(v)}" x2="${w-right}" y2="${y(v)}"/><text x="3" y="${y(v)+5}">${v*100}%</text>`;
  const ticks=new Set([0,max]);for(let i=0;i<=max;i+=Math.max(1,Math.ceil(max/8)))ticks.add(i);
  for(const i of [...ticks].sort((a,b)=>a-b))svg+=`<text x="${x(i)}" y="${h-10}" text-anchor="middle">${i===0?"基线":timeAxis?Number(i.toFixed(1)):i}</text>`;
  run.agents.forEach((agent,index)=>{
    const points=agent.history.map(s=>[timeAxis?elapsed(s):s.iteration,s.validation_score]);
    if(typeof run.baseline_score==="number")points.unshift([0,run.baseline_score]);
    const valid=points.filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
    const color=colors[index%colors.length];
    svg+=`<polyline fill="none" stroke="${color}" stroke-width="2" points="${valid.map(p=>`${x(p[0])},${y(p[1])}`).join(" ")}"/>`;
    for(const p of valid)svg+=`<circle cx="${x(p[0])}" cy="${y(p[1])}" r="4" fill="${color}"><title>${esc(agent.agent_id)} · ${timeAxis?`${p[0].toFixed(1)} 秒`:`第 ${p[0]} 轮`} · ${percent(p[1])}</title></circle>`;
  });
  if(typeof run.best_score==="number")svg+=`<line x1="${left}" y1="${y(run.best_score)}" x2="${w-right}" y2="${y(run.best_score)}" stroke="#fff" stroke-dasharray="4 4"><title>历史最佳 VALIDATE</title></line>`;
  const finalPosition=timeAxis?finalSeconds:finalIteration;
  if(run.final_acceptance&&Number.isFinite(finalPosition))svg+=`<rect x="${x(finalPosition)-5}" y="${y(run.final_acceptance.candidate.score)-5}" width="10" height="10" fill="#ffc984"><title>独立最终验收 · ${timeAxis?`${finalSeconds.toFixed(1)} 秒`:`第 ${finalIteration} 轮之后`}</title></rect>`;
  return svg+"</svg>";
}
function agentCard(agent,index,baseline){
  const latest=agent.history.at(-1),delta=latest&&typeof baseline==="number"?latest.validation_score-baseline:null;
  const task=agent.phase.startsWith("evaluate_")&&agent.task_count?` · 任务 ${agent.task}/${agent.task_count}`:"";
  return `<article class="agent"><div class="agenthead"><h3>${esc(agent.agent_id)}</h3>${badge(agent.state)}</div>
    <p class="meta">模型 ${esc(agent.model||"未记录")} · 使用统一实验目标</p><div class="phase">${esc(phases[agent.phase]||agent.phase)}${esc(task)}</div>
    <progress value="${Number(agent.completed_iterations)}" max="${Number(agent.max_iterations)}" aria-label="${esc(agent.agent_id)} 轮次进度"></progress>
    <div class="phase">已完成 ${agent.completed_iterations} / ${agent.max_iterations} 轮</div>
    <div class="scoreline"><strong>${percent(latest?.validation_score)}</strong><span class="${delta>0?"positive":delta<0?"negative":"muted"}">${delta===null?"尚无候选验证结果":`${delta>=0?"+":""}${(delta*100).toFixed(1)} 个百分点`}</span></div>
    ${agent.error?`<p class="error">${esc(agent.error)}</p>`:""}
    ${agent.current_reason&&!latest?`<p class="meta">当前修改依据：${esc(agent.current_reason)}</p>`:""}
    ${agent.history.map(step=>`<details data-key="${esc(agent.agent_id)}:${step.iteration}"><summary>第 ${step.iteration} 轮 · 验证 ${percent(step.validation_score)}</summary><p>${esc(step.reason)}</p>
      <div class="version"><span>基于</span><code>${esc(step.input_solution_id)}</code><span>候选</span><code>${esc(step.candidate_solution_id)}</code><span>下一轮</span><code>${esc(step.next_solution_id||"结束研究")}</code></div>
      <p class="meta">TRAIN（输入版本 ${esc(step.train_solution_id)}）${percent(step.train_score)} · VALIDATE（候选版本 ${esc(step.validation_solution_id)}）${percent(step.validation_score)} · ${esc(stamp(step.finished_at))}</p>
      <pre>${esc(JSON.stringify(step.validation_metrics,null,2))}</pre>
      <details data-key="${esc(agent.agent_id)}:${step.iteration}:diff"><summary>源码 diff</summary><pre>${esc(step.diff||"未记录")}</pre></details>
      ${(step.screenshots||[]).map(row=>`<figure><img class="evidence" src="/screenshots/${encodeURIComponent(selected)}/${encodeURIComponent(row.digest)}" alt="${esc(row.task_id)}"><figcaption>${esc(row.task_id)}</figcaption></figure>`).join("")}
      ${step.preview?`<a href="${esc(step.preview)}" target="_blank" rel="noopener">隔离预览版本</a><iframe title="Artifact 版本预览" sandbox="allow-scripts allow-same-origin" src="${esc(step.preview)}"></iframe>`:""}</details>`).join("")}</article>`;
}
function render(run){
  const completed=run.agents.reduce((n,a)=>n+a.completed_iterations,0);
  document.title=`RSI · ${run.run_id}`;
  document.getElementById("main").innerHTML=`<div class="runhead"><div><h1>${esc(run.goal)}</h1><div class="meta"><code>${esc(run.run_id)}</code></div></div>${badge(run.goal_contract?.state||run.state)}</div>
    <details class="goal" data-key="goal"><summary>查看完整目标</summary><p>${esc(run.goal)}</p></details>
    <p class="meta">${esc(run.workflow)} · ${esc(run.benchmark)} · Interaction Agent ${esc(run.interaction_model||"未记录")} · RSI Agent ${esc(run.rsi_model||run.teacher_model||"未记录")} · 目标模型 ${esc(run.target_model||run.model||"未记录")}</p>
    ${run.state==="running"&&!run.worker_alive?'<div class="notice">记录显示运行中，但未发现持有此 Run 执行锁的进程。请通过 CLI 核对或恢复；页面不会重新启动实验。</div>':""}
    ${run.error?`<div class="notice">${esc(run.error)}</div>`:""}
    ${run.goal_contract?`<section class="panel"><h2>Goal ${esc(run.goal_contract.goal_id)} · revision ${run.goal_contract.revision}</h2><p>状态 ${esc(run.goal_contract.state)} · 原因 ${esc(run.goal_contract.outcome?.stop_reason||(run.goal_contract.state==="running"?"执行中":"未记录执行结论"))} · 执行耗时 ${esc(run.goal_contract.resource_usage?.elapsed??run.goal_contract.outcome?.elapsed_seconds??"运行中")} 秒 / ${run.goal_contract.budget.max_seconds} 秒</p>${(run.goal_contract.retry_history||[]).length?`<details data-key="attempt-history"><summary>失败尝试与重试</summary><p>旧记录缺失的失败原因显示为 null，不推测原因。</p><pre>${esc(JSON.stringify(run.goal_contract.retry_history,null,2))}</pre></details>`:""}${run.goal_contract.previous_outcome?`<details data-key="previous-outcome"><summary>上次执行结论</summary><pre>${esc(JSON.stringify(run.goal_contract.previous_outcome,null,2))}</pre></details>`:""}<p>交付版本 <code>${esc(run.goal_contract.outcome?.solution_id||"待独立验收")}</code></p><pre>${esc(JSON.stringify(run.goal_contract.acceptance,null,2))}</pre><p class="meta">Token、费用与人工时间：${esc(run.goal_contract.outcome?.token_usage?JSON.stringify(run.goal_contract.outcome.token_usage):"未知")} · 身份 ${esc(run.goal_contract.identity)}</p></section>`:`<div class="notice">${esc(run.historical_acceptance)}</div>`}
    ${run.final_acceptance?`<section class="panel"><h2>独立最终验收 · ${run.final_acceptance.passed?"通过":"未通过"}</h2><p>冻结基线 ${percent(run.final_acceptance.baseline.score)} · 交付候选 ${percent(run.final_acceptance.candidate.score)}</p><pre>${esc(JSON.stringify(run.final_acceptance.metrics,null,2))}</pre><p>最终评测已关闭搜索，结果未用于当前选版。</p></section>`:""}
    ${run.goal_contract?`<section class="panel"><h2>Research Skills</h2><pre>${esc(JSON.stringify(run.goal_contract.researcher.skills.map(s=>({name:s.skill_id,version:s.version,digest:s.content.digest})),null,2))}</pre><p>配置 / 真实加载 / 资源调用证据</p><pre>${esc(JSON.stringify(run.skill_evidence,null,2))}</pre><p class="meta">目录枚举单独记录，不计为资源读取或执行；语义影响未知。</p><p>提炼候选与单独成本</p><pre>${esc(JSON.stringify(run.skill_captures,null,2))}</pre><p>Skill 生命周期</p><pre>${esc(JSON.stringify(run.skill_lifecycle,null,2))}</pre><p>跨任务对照（全部运行）</p><pre>${esc(JSON.stringify(run.skill_reuse,null,2))}</pre></section>`:""}
    <section class="stats" aria-label="实验概览"><div class="stat"><div class="label">基线验证得分</div><div class="value">${percent(run.baseline_score)}</div><div class="meta">${run.baseline_score===null?(run.state==="running"&&run.worker_alive&&run.baseline_progress?.task_count?`正在评测 ${run.baseline_progress.task}/${run.baseline_progress.task_count} 题`:"尚无有效基线结果"):"固定数据分区"}</div></div>
    <div class="stat"><div class="label">历史最佳验证得分</div><div class="value">${percent(run.best_score)}</div><div class="meta">允许持平或退化，不保证提升</div></div>
    <div class="stat"><div class="label">已完成研究轮次</div><div class="value">${completed} <span class="meta">/ ${run.max_iterations*run.research_agent_count}</span></div><div class="meta">${run.research_agent_count} 条独立研究线</div></div>
    <div class="stat"><div class="label">开始时间</div><div class="value meta">${esc(stamp(run.started_at))}</div><div class="meta">${run.finished_at?`结束 ${esc(stamp(run.finished_at))}`:"尚未结束"}</div></div></section>
    <section class="panel"><h2>验证得分 · 按研究线比较</h2><button id="axis" type="button">横轴：${timeAxis?"执行秒数":"迭代轮次"}</button>${chart(run)}<div class="legend">${run.agents.map((a,i)=>`<span class="line-${i}">${esc(a.agent_id)}</span>`).join("")}</div></section>
    <h2>独立研究线与迭代记录</h2><section class="agents">${run.agents.length?run.agents.map((a,i)=>agentCard(a,i,run.baseline_score)).join(""):'<p class="muted">研究线尚未初始化。</p>'}</section>
    <footer>最佳版本 <code>${esc(run.best_solution_id)}</code> · 本地只读视图，每 3 秒刷新；评测错误不会作为成功样本隐藏。</footer>`;
}
async function get(path){const response=await fetch(path,{cache:"no-store"});const data=await response.json();if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const {runs}=await get("/api/runs");
    if(!runs.some(r=>r.run_id===selected))selected=runs[0]?.run_id||"";
    document.getElementById("runs").innerHTML=runs.map(run=>`<a href="#${encodeURIComponent(run.run_id)}" class="${run.run_id===selected?"selected":""}"><span class="name">${esc(run.goal)}</span><span class="sub">${esc(states[run.goal_state||run.state]||(run.goal_state||run.state))} · ${run.research_agent_count} 条研究线</span><span class="sub">${esc(run.run_id)}</span></a>`).join("")||'<p class="meta">暂无实验</p>';
    if(selected){
      const run=await get(`/api/runs/${encodeURIComponent(selected)}`);
      const signature=JSON.stringify({...run,observed_at:null});
      if(signature!==lastRendered){const opened=[...document.querySelectorAll("details[open]")].map(el=>el.dataset.key).filter(Boolean);render(run);for(const detail of document.querySelectorAll("details"))detail.open=opened.includes(detail.dataset.key);lastRendered=signature;}
    }else{document.getElementById("main").innerHTML='<section class="empty"><h1>还没有实验记录</h1><p>在使用相同 RSI_DATA_ROOT 的终端中运行 <code>uv run rsi tui</code>，确认一个实验后这里会自动显示进度。</p></section>';lastRendered="";}
    document.getElementById("connection").textContent=`已连接 · ${new Date().toLocaleTimeString("zh-CN",{hour12:false})}`;
  }catch(error){document.getElementById("connection").textContent=`读取失败：${error.message} · 将自动重试`;}finally{busy=false;}
}
document.getElementById("main").addEventListener("click",event=>{if(event.target.id==="axis"){timeAxis=!timeAxis;lastRendered="";refresh();}});
document.getElementById("refresh").addEventListener("click",refresh);
window.addEventListener("hashchange",()=>{selected=hashRun();lastRendered="";refresh();});
refresh();setInterval(refresh,3000);
