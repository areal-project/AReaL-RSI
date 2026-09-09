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
function chart(run){
  const w=900,h=230,left=48,top=16,bottom=38,right=18,max=Math.max(1,run.max_iterations);
  const x=i=>left+(w-left-right)*i/max,y=score=>top+(h-top-bottom)*(1-score);
  let svg=`<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="各研究线逐轮 VALIDATE 得分，横轴为迭代轮次">`;
  for(const v of [0,.5,1])svg+=`<line class="grid" x1="${left}" y1="${y(v)}" x2="${w-right}" y2="${y(v)}"/><text x="3" y="${y(v)+5}">${v*100}%</text>`;
  const ticks=new Set([0,max]);for(let i=0;i<=max;i+=Math.max(1,Math.ceil(max/8)))ticks.add(i);
  for(const i of [...ticks].sort((a,b)=>a-b))svg+=`<text x="${x(i)}" y="${h-10}" text-anchor="middle">${i===0?"基线":i}</text>`;
  run.agents.forEach((agent,index)=>{
    const points=agent.history.map(s=>[s.iteration,s.validation_score]);
    if(typeof run.baseline_score==="number")points.unshift([0,run.baseline_score]);
    const valid=points.filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
    const color=colors[index%colors.length];
    svg+=`<polyline fill="none" stroke="${color}" stroke-width="2" points="${valid.map(p=>`${x(p[0])},${y(p[1])}`).join(" ")}"/>`;
    for(const p of valid)svg+=`<circle cx="${x(p[0])}" cy="${y(p[1])}" r="4" fill="${color}"><title>${esc(agent.agent_id)} · 第 ${p[0]} 轮 · ${percent(p[1])}</title></circle>`;
  });
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
      <p class="meta">TRAIN ${percent(step.train_score)} · ${esc(stamp(step.finished_at))}</p></details>`).join("")}</article>`;
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
    <section class="stats" aria-label="实验概览"><div class="stat"><div class="label">基线验证得分</div><div class="value">${percent(run.baseline_score)}</div><div class="meta">${run.baseline_score===null?(run.state==="running"&&run.worker_alive&&run.baseline_progress?.task_count?`正在评测 ${run.baseline_progress.task}/${run.baseline_progress.task_count} 题`:"尚无有效基线结果"):"固定数据分区"}</div></div>
    <div class="stat"><div class="label">历史最佳验证得分</div><div class="value">${percent(run.best_score)}</div><div class="meta">允许持平或退化，不保证提升</div></div>
    <div class="stat"><div class="label">已完成研究轮次</div><div class="value">${completed} <span class="meta">/ ${run.max_iterations*run.research_agent_count}</span></div><div class="meta">${run.research_agent_count} 条独立研究线</div></div>
    <div class="stat"><div class="label">开始时间</div><div class="value meta">${esc(stamp(run.started_at))}</div><div class="meta">${run.finished_at?`结束 ${esc(stamp(run.finished_at))}`:"尚未结束"}</div></div></section>
    <section class="panel"><h2>验证得分 · 按研究线比较</h2>${chart(run)}<div class="legend">${run.agents.map((a,i)=>`<span class="line-${i}">${esc(a.agent_id)}</span>`).join("")}</div></section>
    <h2>独立研究线与迭代记录</h2><section class="agents">${run.agents.length?run.agents.map((a,i)=>agentCard(a,i,run.baseline_score)).join(""):'<p class="muted">研究线尚未初始化。</p>'}</section>
    <footer>最佳版本 <code>${esc(run.best_solution_id)}</code> · 本地只读视图，每 3 秒刷新；评测错误不会作为成功样本隐藏。</footer>`;
}
async function get(path){const response=await fetch(path,{cache:"no-store"});const data=await response.json();if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;}
async function refresh(){
  if(busy)return;busy=true;
  try{
    const {runs}=await get("/api/runs");
    if(!runs.some(r=>r.run_id===selected))selected=runs[0]?.run_id||"";
    document.getElementById("runs").innerHTML=runs.map(run=>`<a href="#${encodeURIComponent(run.run_id)}" class="${run.run_id===selected?"selected":""}"><span class="name">${esc(run.goal)}</span><span class="sub">${esc(states[run.state]||run.state)} · ${run.research_agent_count} 条研究线</span><span class="sub">${esc(run.run_id)}</span></a>`).join("")||'<p class="meta">暂无实验</p>';
    if(selected){
      const run=await get(`/api/runs/${encodeURIComponent(selected)}`);
      const signature=JSON.stringify({...run,observed_at:null});
      if(signature!==lastRendered){const opened=[...document.querySelectorAll("details[open]")].map(el=>el.dataset.key);render(run);for(const detail of document.querySelectorAll("details"))detail.open=opened.includes(detail.dataset.key);lastRendered=signature;}
    }else{document.getElementById("main").innerHTML='<section class="empty"><h1>还没有实验记录</h1><p>在使用相同 RSI_DATA_ROOT 的终端中运行 <code>uv run rsi tui</code>，确认一个实验后这里会自动显示进度。</p></section>';lastRendered="";}
    document.getElementById("connection").textContent=`已连接 · ${new Date().toLocaleTimeString("zh-CN",{hour12:false})}`;
  }catch(error){document.getElementById("connection").textContent=`读取失败：${error.message} · 将自动重试`;}finally{busy=false;}
}
document.getElementById("refresh").addEventListener("click",refresh);
window.addEventListener("hashchange",()=>{selected=hashRun();lastRendered="";refresh();});
refresh();setInterval(refresh,3000);
