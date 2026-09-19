/* The workbench graph is a read-only projection of agents and recorded events. */
const ResearchGraph = (() => {
  const millis = (value) => typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(value) || 0;
  const researchers = (run) => [...new Set([
    ...(run.agents || []).filter((a) => a.direction).map((a) => a.agent_id),
    ...(run.nodes || []).map((n) => n.agent_id),
  ])];
  const sharedFrom = (message) => [...new Set((message.sources || [])
    .map((source) => source.agent_id).filter((id) => id !== message.recipient && id !== "rsi"))];

  function model(run, {allJobs = false, collaborationOnly = false} = {}) {
    const ids = researchers(run);
    const lanes = [...(ids.length && run.agents?.some((a) => a.agent_id === "rsi") ? ["rsi"] : []), ...ids];
    const events = [];
    const messages = run.communications || [];
    const byReport = new Map(messages.filter((m) => m.report_id).map((m) => [m.report_id, m]));
    const event = (value) => events.push({...value, time: millis(value.at)});
    for (const node of run.nodes || []) {
      if (!collaborationOnly) event({id: `start:${node.iteration_id}`, agent: node.agent_id,
        at: node.created_at, kind: "start", node, title: "开始探索"});
      for (const report of node.reports?.length ? node.reports : node.report ? [node.report] : []) {
        const message = byReport.get(report.report_id);
        event({id: message ? `message:${message.message_id}` : `report:${report.report_id || node.iteration_id}`,
          agent: node.agent_id, at: report.created_at, kind: "report", node, report, message,
          title: report.message || "向 RSI 汇报进展"});
      }
    }
    for (const message of messages) {
      if (message.report_id && events.some((e) => e.message?.message_id === message.message_id)) continue;
      const advice = message.sender === "rsi";
      const from = advice ? sharedFrom(message) : [];
      event({id: `message:${message.message_id}`, agent: advice ? message.recipient : message.sender,
        at: message.created_at, kind: advice ? from.length ? "share" : "advice" : "report",
        from: advice ? from.length ? from : ["rsi"] : [], message, title: message.text});
    }
    if (!collaborationOnly) {
      const best = new Map();
      for (const job of [...(run.jobs || [])].sort((a, b) => millis(a.finished_at || a.started_at || a.created_at) - millis(b.finished_at || b.started_at || b.created_at))) {
        if (!lanes.includes(job.agent_id)) lanes.push(job.agent_id);
        const eligible = job.search_eligible === true;
        const previous = best.get(job.agent_id);
        const improves = eligible && (previous === undefined || (run.brief?.verification?.direction === "minimize"
          ? job.result.score < previous : job.result.score > previous));
        if (improves) best.set(job.agent_id, job.result.score);
        const active = ["pending", "queued", "starting", "running"].includes(job.status);
        if (allJobs || active || improves || job.phase === "final") event({
          id: `job:${job.job_id}`, agent: job.agent_id, at: job.finished_at || job.started_at || job.created_at,
          kind: "job", job, improves, title: improves ? "刷新个人最佳" : job.title || "执行作业",
        });
      }
      // Historical collaborative runs may also record explicit references in assignments.
      for (const edge of run.edges || []) {
        if (!["reference", "merge"].includes(edge.kind)) continue;
        const source = run.nodes.find((n) => n.iteration_id === edge.source);
        const target = events.find((e) => e.id === `start:${edge.target}`);
        if (source && target && source.agent_id !== target.agent) {
          target.from = [...new Set([...(target.from || []), source.agent_id])];
          target.title = "参考已有研究，开始探索";
        }
      }
    }
    return {lanes, events: events.filter((e) => lanes.includes(e.agent)).sort((a, b) => a.time - b.time || a.id.localeCompare(b.id))};
  }

  function layout(model) {
    const laneWidth = 232, left = 66, top = 288, rowHeight = 94;
    const laneIndex = new Map(model.lanes.map((id, index) => [id, index]));
    let row = -1, occupied = new Set(), rowTime = 0;
    const entries = model.events.map((event) => {
      const lane = laneIndex.get(event.agent);
      const sources = (event.from || []).filter((id) => laneIndex.has(id));
      const ends = [lane, ...sources.map((id) => laneIndex.get(id))];
      const start = Math.min(...ends), end = Math.max(...ends);
      const needed = Array.from({length: end - start + 1}, (_, index) => start + index);
      // Sharing owns the whole crossed span. Other cards can never cover its route.
      if (row < 0 || event.time - rowTime > 90000 || needed.some((index) => occupied.has(index))) {
        row++; occupied = new Set(); rowTime = event.time;
      }
      needed.forEach((index) => occupied.add(index));
      return {...event, sources, row, x: left + lane * laneWidth + 20, y: top + row * rowHeight};
    });
    return {entries, laneWidth, left, top, rowHeight,
      width: left + model.lanes.length * laneWidth + 16,
      height: top + Math.max(1, row + 1) * rowHeight + 32};
  }
  return {model, layout, researchers, sharedFrom};
})();
