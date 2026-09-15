"use strict";
const colors=["#64d9c3","#91b8ff","#ffc984","#d5a1f6","#ee91b0","#b5d78d","#72c7ec","#deb687"];
const states={planned:"待启动",running:"运行中",succeeded:"已完成",failed:"失败",cancelled:"已停止"};
const remoteStates={prepared:"准备中",submitting:"提交中",submitted:"已提交",unknown:"提交待核对",rejected:"提交被拒绝",stop_requested:"正在停止",collected:"结果已回收",RUNNING:"运行中",PENDING:"等待中",AGGREGATING:"汇总中",DONE:"已完成",STOPPING:"停止中",STOPPED:"已停止",FAILED:"失败",CANCELLED:"已取消",TIMEOUT:"超时"};
const phases={waiting:"等待首轮",research:"分析当前版本",evaluate_train:"执行 TRAIN 评测",evaluate_validate:"执行 VALIDATE 评测",propose:"提出修改",build:"构建候选版本",select:"选择下一轮版本",iteration_completed:"本轮完成",completed:"研究完成",failed:"执行失败",cancelled:"已停止",pruned:"已剪枝"};
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const percent=value=>typeof value==="number"&&Number.isFinite(value)?`${(value*100).toFixed(1)}%`:"—";
const stamp=value=>value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"—";
const badge=state=>`<span class="badge ${esc(state)}">${esc(states[state]||state)}</span>`;
function hashRun(){try{return decodeURIComponent(location.hash.slice(1));}catch{return "";}}
let selected=hashRun(),busy=false,lastRendered="";
function versionTarget(run,id){return `version-${run.run_id}-${id}`;}
function versionHistory(run,agent){
  const baseline={solution_id:run.baseline_solution_id,iteration:0,score:run.baseline_score,label:"基线版本"};
  const versions=new Map([[baseline.solution_id,baseline]]);
  const steps=agent.history.map(step=>{
    const node={solution_id:step.candidate_solution_id,iteration:step.iteration,score:step.validation_score,label:`第 ${step.iteration} 轮候选`};
    const parent=versions.get(step.input_solution_id)||null;
    versions.set(node.solution_id,node);
    return {step,node,parent};
  });
  return {baseline,steps};
}
function versionLink(run,node,label){
  return `<a class="version-link" href="#${encodeURIComponent(run.run_id)}" data-version-target="${esc(versionTarget(run,node.solution_id))}" aria-label="查看${esc(label||node.label)}：${esc(node.solution_id)}">${esc(label||node.label)}<code>${esc(node.solution_id)}</code></a>`;
}
function chart(run){
  const w=900,h=230,left=48,top=16,bottom=38,right=18,max=Math.max(1,run.max_iterations);
  const x=i=>left+(w-left-right)*i/max,y=score=>top+(h-top-bottom)*(1-score);
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="group" aria-label="各研究线 VALIDATE 得分与实际父版本连线，点击节点查看修改原因">`;
  for(const v of [0,.5,1])svg+=`<line class="grid" x1="${left}" y1="${y(v)}" x2="${w-right}" y2="${y(v)}"/><text x="3" y="${y(v)+5}">${v*100}%</text>`;
  const ticks=new Set([0,max]);for(let i=0;i<=max;i+=Math.max(1,Math.ceil(max/8)))ticks.add(i);
  for(const i of [...ticks].sort((a,b)=>a-b))svg+=`<text x="${x(i)}" y="${h-10}" text-anchor="middle">${i===0?"基线":i}</text>`;
  run.agents.forEach((agent,index)=>{
    const {baseline,steps}=versionHistory(run,agent),color=colors[index%colors.length];
    svg+=`<defs><marker id="parent-arrow-${index}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${color}"/></marker></defs>`;
    for(const {step,node,parent} of steps){
      if(!parent||!Number.isFinite(parent.score)||!Number.isFinite(node.score))continue;
      const dx=x(node.iteration)-x(parent.iteration),dy=y(node.score)-y(parent.score),length=Math.hypot(dx,dy);
      if(!length)continue;
      svg+=`<line class="parent-edge" data-parent-version="${esc(parent.solution_id)}" data-child-version="${esc(node.solution_id)}" x1="${x(parent.iteration)+dx/length*5}" y1="${y(parent.score)+dy/length*5}" x2="${x(node.iteration)-dx/length*8}" y2="${y(node.score)-dy/length*8}" stroke="${color}" stroke-width="2" marker-end="url(#parent-arrow-${index})"><title>${esc(agent.agent_id)} · ${esc(parent.label)} → ${esc(node.label)}：${esc(step.reason||"未记录修改原因")}</title></line>`;
    }
    for(const node of [baseline,...steps.map(item=>item.node)]){
      if(!Number.isFinite(node.score)||!Number.isFinite(node.iteration))continue;
      svg+=`<a href="#${encodeURIComponent(run.run_id)}" data-version-target="${esc(versionTarget(run,node.solution_id))}" aria-label="${esc(agent.agent_id)} · 查看${esc(node.label)} · 验证 ${percent(node.score)}"><circle cx="${x(node.iteration)}" cy="${y(node.score)}" r="12" fill="transparent"/><circle cx="${x(node.iteration)}" cy="${y(node.score)}" r="4" fill="${color}"/><title>${esc(node.label)} · ${esc(node.solution_id)} · ${percent(node.score)}</title></a>`;
    }
  });
  return svg+"</svg>";
}
function iterationCard(run,agent,entry){
  const {step,node,parent}=entry;
  const previous=parent?versionLink(run,parent):`<span class="missing-parent">未找到上一版本<code>${esc(step.input_solution_id||"未记录")}</code></span>`;
  return `<section class="iteration" id="${esc(versionTarget(run,node.solution_id))}" tabindex="-1" aria-label="${esc(agent.agent_id)} 第 ${step.iteration} 轮候选">
    <h4>第 ${step.iteration} 轮 <span>验证 ${percent(step.validation_score)}</span></h4>
    <div class="ancestry"><div><span class="label">上一版本</span>${previous}</div><span class="version-arrow" aria-label="改进为">→</span><div><span class="label">本轮候选</span><code>${esc(node.solution_id)}</code></div></div>
    <div class="iteration-reason"><strong>修改原因</strong><p>${esc(step.reason||"未记录修改原因")}</p></div>
    <details data-key="${esc(agent.agent_id)}:${step.iteration}"><summary>版本详情与训练结果</summary><div class="version"><span>下一轮</span><code>${esc(step.next_solution_id||"结束研究")}</code></div>
    <p class="meta">TRAIN ${percent(step.train_score)} · ${esc(stamp(step.finished_at))}</p></details></section>`;
}
function agentCard(agent,index,run){
  const baseline=run.baseline_score,latest=agent.history.at(-1),delta=latest&&typeof baseline==="number"?latest.validation_score-baseline:null;
  const task=agent.phase.startsWith("evaluate_")&&agent.task_count?` · 任务 ${agent.task}/${agent.task_count}`:"";
  const currentReason=agent.current_reason&&(!latest||agent.iteration>latest.iteration);
  return `<article class="agent"><div class="agenthead"><h3>${esc(agent.agent_id)}</h3>${badge(agent.state)}</div>
    <p class="meta">模型 ${esc(agent.model||"未记录")} · 使用统一实验目标</p><div class="phase">${esc(phases[agent.phase]||agent.phase)}${esc(task)}</div>
    <progress value="${Number(agent.completed_iterations)}" max="${Number(agent.max_iterations)}" aria-label="${esc(agent.agent_id)} 轮次进度"></progress>
    <div class="phase">已完成 ${agent.completed_iterations} / ${agent.max_iterations} 轮</div>
    <div class="scoreline"><strong>${percent(latest?.validation_score)}</strong><span class="${delta>0?"positive":delta<0?"negative":"muted"}">${delta===null?"尚无候选验证结果":`${delta>=0?"+":""}${(delta*100).toFixed(1)} 个百分点`}</span></div>
    ${agent.error?`<p class="error">${esc(agent.error)}</p>`:""}
    ${currentReason?`<p class="meta">当前修改依据：${esc(agent.current_reason)}</p>`:""}
    ${versionHistory(run,agent).steps.map(entry=>iterationCard(run,agent,entry)).join("")}</article>`;
}
const deltaLabel=value=>Number.isFinite(value)?`${value>=0?"+":""}${(value*100).toFixed(1)} pp`:"—";
function scorePanel(run){
  const rows=run.score_history||[];
  if(!rows.length)return '<section class="panel"><h2>Task Agent · Benchmark 跑分</h2><p class="muted">尚无完整 VALIDATE 评测；完成后自动显示。</p></section>';
  const w=900,h=240,left=65,right=20,top=20,bottom=40;
  const low=Math.min(0,...rows.map(r=>r.score)),high=Math.max(1,...rows.map(r=>r.score));
  const x=i=>left+(w-left-right)*i/Math.max(1,rows.length-1);
  const y=s=>top+(h-top-bottom)*(high-s)/(high-low);
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="按评测完成顺序排列的 Task Agent 分数与历史最佳曲线">`;
  for(const s of [low,(low+high)/2,high])svg+=`<line class="grid" x1="${left}" y1="${y(s)}" x2="${w-right}" y2="${y(s)}"/><text x="0" y="${y(s)+5}">${percent(s)}</text>`;
  svg+=`<polyline fill="none" stroke="#ffc984" stroke-width="2" stroke-dasharray="6 4" points="${rows.map((r,i)=>`${x(i)},${y(r.best_score)}`).join(" ")}"/>`;
  rows.forEach((r,i)=>{
    const index=run.agents.findIndex(a=>a.agent_id===r.agent_id),color=index<0?"#ffffff":colors[index%colors.length];
    svg+=`<circle cx="${x(i)}" cy="${y(r.score)}" r="5" fill="${color}"><title>${esc(r.agent_id)} · 第 ${r.iteration} 轮 · ${esc(r.solution_id)} · ${percent(r.score)} · ${esc(stamp(r.evaluated_at))}</title></circle>`;
    if(i===0||i===rows.length-1||i%Math.ceil(rows.length/8)===0)svg+=`<text x="${x(i)}" y="${h-12}" text-anchor="middle">${i+1}</text>`;
  });
  svg+='</svg>';
  const labels={baseline:"基线",selected:"继续采用",reverted:"选择其他版本",stopped:"研究线结束",pending:"待完成版本选择"};
  const change=value=>`<span class="${value>0?"positive":value<0?"negative":"muted"}">${deltaLabel(value)}</span>`;
  return `<section class="panel"><h2>Task Agent · Benchmark 跑分</h2><p class="meta">${esc(run.benchmark)} · VALIDATE 固定分区。横轴为评测完成序号；圆点为每个版本实测分数，金色虚线为历史最高分。评测失败不记为零分。</p>${svg}
    <div class="legend"><span>历史最高 ${percent(rows.at(-1).best_score)}</span><span>已完成 ${rows.length} 次评测（含基线）</span></div>
    <details data-key="score-history"><summary>逐版本跑分记录与提升幅度</summary><div class="score-table"><table><thead><tr><th>研究线 / 轮次</th><th>Task Agent 版本</th><th>VALIDATE</th><th>较基线</th><th>较父版本</th><th>后续选择</th><th>评测完成</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.agent_id)} / ${r.iteration}</td><td><code>${esc(r.solution_id)}</code></td><td>${percent(r.score)}</td><td>${change(r.baseline_delta)}</td><td>${change(r.parent_delta)}</td><td>${esc(labels[r.selection]||r.selection)}</td><td>${esc(stamp(r.evaluated_at))}</td></tr>`).join("")}</tbody></table></div></details></section>`;
}
function render(run){
  const completed=run.agents.reduce((n,a)=>n+a.completed_iterations,0);
  document.title=`RSI · ${run.run_id}`;
  document.getElementById("main").innerHTML=`<div class="runhead"><div><h1>${esc(run.goal)}</h1><div class="meta"><code>${esc(run.run_id)}</code></div></div>${badge(run.state)}</div>
    <details class="goal" data-key="goal"><summary>查看完整目标</summary><p>${esc(run.goal)}</p></details>
    <p class="meta">${esc(run.workflow)} · ${esc(run.benchmark)} · Interaction Agent ${esc(run.interaction_model||"未记录")} · RSI Agent ${esc(run.rsi_model||run.teacher_model||"未记录")} · 目标模型 ${esc(run.target_model||run.model||"未记录")}</p>
    ${run.state==="running"&&!run.worker_alive?'<div class="notice">记录显示运行中，但未发现持有此 Run 执行锁的进程。请通过 CLI 核对或恢复；页面不会重新启动实验。</div>':""}
    ${run.error?`<div class="notice">${esc(run.error)}</div>`:""}
    ${run.remote_jobs?.length?`<details class="goal" data-key="remote-jobs"><summary>远端评测任务（${run.remote_jobs.length}）</summary>${run.remote_jobs.map(job=>`<p>${job.url?`<a href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">${esc(job.job_id)}</a>`:esc(job.job_id||"提交待核对")} · ${esc(remoteStates[job.state]||job.state)} · 成功 ${Number(job.task_counts?.done||0)}，失败 ${Number(job.task_counts?.failed||0)}，取消 ${Number(job.task_counts?.cancelled||0)} / 共 ${Number(job.task_counts?.total||0)} 题</p>`).join("")}</details>`:""}
    <section class="stats" aria-label="实验概览"><div class="stat baseline-version" id="${esc(versionTarget(run,run.baseline_solution_id))}" tabindex="-1"><div class="label">基线版本 · 验证得分</div><div class="value">${percent(run.baseline_score)}</div><div class="meta">${run.baseline_score===null?(run.state==="running"&&run.worker_alive&&run.baseline_progress?.task_count?`正在评测 ${run.baseline_progress.task}/${run.baseline_progress.task_count} 题`:"尚无有效基线结果"):"固定数据分区"}</div><code class="baseline-id" title="${esc(run.baseline_solution_id)}">${esc(run.baseline_solution_id)}</code></div>
    <div class="stat"><div class="label">历史最佳验证得分</div><div class="value">${percent(run.best_score)}</div><div class="meta">允许持平或退化，不保证提升</div></div>
    <div class="stat"><div class="label">已完成研究轮次</div><div class="value">${completed} <span class="meta">/ ${run.max_iterations*run.research_agent_count}</span></div><div class="meta">${run.research_agent_count} 条独立研究线</div></div>
    <div class="stat"><div class="label">开始时间</div><div class="value meta">${esc(stamp(run.started_at))}</div><div class="meta">${run.finished_at?`结束 ${esc(stamp(run.finished_at))}`:"尚未结束"}</div></div></section>
    <section class="panel"><h2>验证得分 · 版本演进关系</h2><p class="meta">箭头从实际输入版本指向本轮候选；点击节点查看版本及修改原因。</p>${chart(run)}<div class="legend">${run.agents.map((a,i)=>`<span class="line-${i}">${esc(a.agent_id)}</span>`).join("")}</div></section>
    ${scorePanel(run)}
    <h2>独立研究线与迭代记录</h2><section class="agents">${run.agents.length?run.agents.map((a,i)=>agentCard(a,i,run)).join(""):'<p class="muted">研究线尚未初始化。</p>'}</section>
    <footer>最佳版本 <code>${esc(run.best_solution_id)}</code> · 本地只读视图，每 3 秒刷新；评测错误不会作为成功样本隐藏。</footer>`;
}
async function get(path){const response=await fetch(path,{cache:"no-store"});const data=await response.json();if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const {runs,workspace_root}=await get("/api/runs");
    document.getElementById("workspace").textContent=workspace_root||"未提供，请重启 Web 服务";
    if(!runs.some(r=>r.run_id===selected))selected=runs[0]?.run_id||"";
    document.getElementById("runs").innerHTML=runs.map(run=>`<a href="#${encodeURIComponent(run.run_id)}" class="${run.run_id===selected?"selected":""}"><span class="name">${esc(run.goal)}</span><span class="sub">${esc(states[run.state]||run.state)} · ${run.research_agent_count} 条研究线</span><span class="sub">${esc(run.run_id)}</span></a>`).join("")||'<p class="meta">暂无实验</p>';
    if(selected){
      const run=await get(`/api/runs/${encodeURIComponent(selected)}`);
      const signature=JSON.stringify({...run,observed_at:null});
      if(signature!==lastRendered){const opened=[...document.querySelectorAll("details[open]")].map(el=>el.dataset.key);render(run);for(const detail of document.querySelectorAll("details"))detail.open=opened.includes(detail.dataset.key);lastRendered=signature;}
    }else{document.getElementById("main").innerHTML='<section class="empty"><h1>当前工作区没有实验记录</h1><p>请核对上方的读取目录是否与 TUI 一致。可用 <code>rsi web --root 实验工作区</code> 显式指定；确认实验后这里会自动显示进度。</p></section>';lastRendered="";}
    document.getElementById("connection").textContent=`已连接 · ${new Date().toLocaleTimeString("zh-CN",{hour12:false})}`;
  }catch(error){document.getElementById("connection").textContent=`读取失败：${error.message} · 将自动重试`;}finally{busy=false;}
}
document.getElementById("main").addEventListener("click",event=>{
  const link=event.target.closest?.("[data-version-target]");
  if(!link)return;
  event.preventDefault();
  const target=document.getElementById(link.dataset.versionTarget);
  if(!target)return;
  document.querySelectorAll(".version-focus").forEach(el=>el.classList.remove("version-focus"));
  target.classList.add("version-focus");
  target.scrollIntoView({behavior:"smooth",block:"center"});
  target.focus({preventScroll:true});
});
document.getElementById("refresh").addEventListener("click",refresh);
window.addEventListener("hashchange",()=>{selected=hashRun();lastRendered="";refresh();});
refresh();setInterval(refresh,3000);
