import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {Window} from 'happy-dom';

const assets = new URL('../../src/areal_rsi/ui/workbench_static/', import.meta.url);
const source = (name) => readFileSync(new URL(name, assets), 'utf8');
const defaults = {base_url: 'https://4ipic.com', model: 'gpt-6-astra', reasoning_effort: 'xhigh', has_credentials: true};
const collaborationCatalog = {modes: [
  {id: 'passk', label: '纯 pass@k', version: '1', description: '独立探索，RSI 只监督继续。'},
], modules: [{id: 'leader_guidance', label: 'RSI 协调', version: '3', modes: ['passk'], requires: [], conflicts: []}], presets: [
  {id: 'passk', label: '纯 pass@k', mode: 'passk', modules: []},
  {id: 'passk_guided', label: 'pass@k + RSI 协调', mode: 'passk', modules: ['leader_guidance']},
]};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve)); };
const response = (value, ok = true) => ({ok, status: ok ? 200 : 503, json: async () => structuredClone(value)});
function run(id, status = 'planned') {
  return {
    run_id: id, objective: `研究 ${id}`, status, nodes: [], jobs: [], edges: [], messages: [],
    brief: {objective: id, limits: {}}, budget: {}, agents: [{agent_id: 'interaction', state: 'waiting'}],
    model_settings: {...defaults, editable: status === 'planned'}, pending_agents: [], worker_alive: true,
    collaboration: {mode: 'passk', modules: [], label: '纯 pass@k', editable: status === 'planned', manifest: null},
    events: [], event_cursor: 120, waiting_for_user: [],
  };
}
async function setup(t, records, handler, savedStorage = {}) {
  const window = new Window({url: 'http://localhost:8198/', settings: {disableJavaScriptFileLoading: true, disableCSSFileLoading: true}});
  t.after(() => window.happyDOM.close());
  for (const [key, value] of Object.entries(savedStorage)) window.localStorage.setItem(key, value);
  const timers = new Map();
  window.setInterval = (callback, ms) => { timers.set(ms, callback); return 0; };
  const calls = [];
  window.fetch = async (path, options = {}) => {
    calls.push({path, options, body: options.body ? JSON.parse(options.body) : null});
    const custom = handler?.(path, options, calls);
    if (custom) return custom;
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults, collaboration_catalog: collaborationCatalog, default_collaboration: {mode: 'passk', modules: []}, models: [{id: 'gpt-6-astra', efforts: ['high', 'xhigh']}, {id: 'gpt-5.6-sol', efforts: ['medium', 'high', 'xhigh']}]});
    if (path === '/api/runs') return response({runs: Object.values(records)});
    const id = path.split('/')[3]?.split('?')[0];
    assert.ok(records[id], `Unexpected API request ${path}`);
    return response(records[id]);
  };
  window.document.write(source('index.html'));
  window.HTMLElement.prototype.scrollTo = function ({left = 0, top = 0}) { this.scrollLeft = left; this.scrollTop = top; };
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.eval(source('dropdown.js') + '\n' + source('research-graph.js') + '\n' + source('app.js'));
  await settle();
  const $ = (id) => window.document.getElementById(id);
  const switchRun = (id) => [...$('run-list').querySelectorAll('button')].find((button) => button.textContent.includes(`研究 ${id}`)).click();
  const input = (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event('input', {bubbles: true})); };
  const submit = (id) => $(id).dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  const poll = async () => { await timers.get(1500)(); await settle(); };
  return {window, $, calls, switchRun, input, submit, poll};
}

test('research groups collapse, persist across navigation and reload, and share mobile selection', async (t) => {
  const recent = {id: 'recent', name: '0915-153620', created_at: '2026-09-15T07:36:20Z'};
  const older = {id: 'older', name: '0910-224132', created_at: '2026-09-10T14:41:32Z'};
  const records = {a: {...run('a'), group: recent}, b: {...run('b'), group: recent}, c: {...run('c'), group: older}};
  const ui = await setup(t, records);
  const groups = () => [...ui.$('run-list').querySelectorAll('.run-group')];
  assert.deepEqual(groups().map((group) => group.querySelector('.run-group-name').textContent), [recent.name, older.name]);
  assert.deepEqual(groups().map((group) => group.querySelector('.run-group-count').textContent), ['2', '1']);
  assert.ok(groups().every((group) => !group.open));
  groups()[0].querySelector('summary').click(); await settle();
  assert.equal(groups()[0].open, true);
  ui.switchRun('b'); await settle();
  assert.equal(ui.$('objective').textContent, '研究 b');
  assert.deepEqual(groups().map((group) => group.open), [true, false]);
  groups()[0].querySelector('summary').click(); await settle();
  ui.$('mobile-run-picker').querySelector('summary').click();
  const mobileOlder = ui.$('mobile-run-list').querySelectorAll('.run-group')[1];
  mobileOlder.querySelector('summary').click(); await settle();
  mobileOlder.querySelector('button').click(); await settle();
  assert.equal(ui.$('objective').textContent, '研究 c');
  assert.equal(ui.$('mobile-run-picker').open, false);
  assert.deepEqual(groups().map((group) => group.open), [false, true]);
  const saved = Object.fromEntries(['recent', 'older'].map((id) => {
    const key = `rsi.workbench.group.group:${id}`;
    return [key, ui.window.localStorage.getItem(key)];
  }));
  const reloaded = await setup(t, records, undefined, saved);
  assert.deepEqual([...reloaded.$('run-list').querySelectorAll('.run-group')].map((group) => group.open), [false, true]);
});

test('new research stays accessible alongside saved groups and empty groups disappear', async (t) => {
  const records = {a: {...run('a'), group: {id: 'batch', name: '0915-153620', created_at: '2026-09-15T07:36:20Z'}}};
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/runs' && options.method === 'POST') {
      records.new = run('new');
      return Promise.resolve(response(records.new));
    }
  });
  ui.$('new-run').click(); ui.submit('create-form'); await settle();
  const groups = [...ui.$('run-list').querySelectorAll('.run-group')];
  assert.deepEqual(groups.map((group) => group.querySelector('.run-group-name').textContent), ['未分组', '0915-153620']);
  assert.equal(ui.$('objective').textContent, '研究 new');
  const empty = await setup(t, {});
  assert.equal(empty.$('run-list').querySelectorAll('.run-group').length, 0);
  assert.match(empty.$('run-list').textContent, /还没有研究记录/);
});

test('collaboration selection saves before start, stays per run, and locks historical runs', async (t) => {
  const records = {a: run('a'), b: run('b', 'stopped')};
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/runs/a/collaboration' && options.method === 'POST') {
      const selection = JSON.parse(options.body);
      records.a.collaboration = {...selection, label: 'pass@k + RSI 协调', editable: true, manifest: null};
      return Promise.resolve(response(records.a));
    }
  });
  assert.equal(ui.$('collaboration-settings').hidden, false);
  ui.$('collaboration-mode-trigger').click();
  ui.$('collaboration-mode-options').querySelectorAll('[role="option"]')[1].click();
  ui.$('start').click(); await settle();
  assert.ok(!ui.calls.some((call) => call.path.endsWith('/start')));
  assert.match(ui.$('notice').textContent, /保存协作配置/);
  ui.switchRun('b'); await settle();
  assert.equal(ui.$('collaboration-mode-trigger').disabled, true);
  assert.match(ui.$('collaboration-summary').textContent, /已锁定/);
  assert.match(ui.$('collaboration-status').textContent, /历史研究/);
  ui.switchRun('a'); await settle();
  assert.match(ui.$('collaboration-mode-trigger').textContent, /pass1/);
  ui.submit('collaboration-form'); await settle();
  assert.deepEqual(ui.calls.find((call) => call.path.endsWith('/collaboration')).body, {mode: 'pass1', modules: []});
  assert.equal(ui.$('save-collaboration').disabled, true);
  ui.$('reuse-collaboration').click();
  assert.match(ui.$('create-collaboration-mode-trigger').textContent, /pass1/);
});

test('creation offers only passk and pass1, without hidden coordination modules', async (t) => {
  let submitted;
  const records = {a: run('a')};
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/bootstrap') return response({
      csrf_token: 'test', default_model_settings: defaults, models: [],
      collaboration_catalog: collaborationCatalog,
      default_collaboration: {mode: 'passk', modules: ['leader_guidance', 'leader_guidance_v022']},
    });
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = run('new');
      return response(records.new);
    }
  });
  ui.$('new-run').click();
  assert.equal(ui.$('create-collaboration-mode-trigger').textContent.trim(), 'pass@k');
  assert.equal(ui.$('create-collaboration-modules'), null);
  assert.equal(ui.$('collaboration-modules'), null);
  ui.$('create-collaboration-mode-trigger').click();
  const options = [...ui.$('create-collaboration-mode-options').querySelectorAll('[role="option"]')];
  assert.deepEqual(options.map((item) => item.textContent.replace('✓', '').trim()), ['pass@k', 'pass1']);
  options[0].click();
  ui.submit('create-form'); await settle();
  assert.deepEqual(submitted.collaboration, {mode: 'passk', modules: []});
});

test('historical coordination is preserved but reuse creates a plain passk task', async (t) => {
  const old = run('a', 'stopped');
  old.collaboration.modules = ['leader_guidance', 'leader_guidance_v022'];
  old.collaboration.manifest = {modules: [{id: 'leader_guidance', version: '3'}]};
  const before = structuredClone(old.collaboration);
  const records = {a: old};
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/runs' && options.method === 'POST') {
      records.new = run('new');
      return response(records.new);
    }
  });
  assert.match(ui.$('collaboration-summary').textContent, /原有协作配置/);
  assert.doesNotMatch(ui.$('collaboration-settings').textContent, /leader_guidance|Leader Prompt|v0\.2/);
  assert.deepEqual(old.collaboration, before);
  assert.ok(!ui.calls.some((call) => call.path.endsWith('/collaboration')));
  ui.$('reuse-collaboration').click();
  ui.submit('create-form'); await settle();
  assert.deepEqual(ui.calls.find((call) => call.path === '/api/runs' && call.options.method === 'POST').body.collaboration,
    {mode: 'passk', modules: []});
});

test('retired collaboration stays viewable without execution or reuse controls', async (t) => {
  const record = run('archived', 'paused');
  record.collaboration = {mode: 'collaborative', modules: [], label: '旧版协作（仅供查看）', supported: false, editable: false};
  record.model_settings.editable = false;
  const ui = await setup(t, {archived: record});
  assert.equal(ui.$('objective').textContent, '研究 archived');
  for (const id of ['start', 'pause', 'resume', 'reuse-collaboration']) assert.equal(ui.$(id).hidden, true);
  assert.equal(ui.$('stop').hidden, false);
  assert.equal(ui.$('chat-input').disabled, true);
  assert.equal(ui.$('collaboration-mode-trigger').disabled, true);
  assert.match(ui.$('collaboration-summary').textContent, /仅供查看/);
});

test('switching runs uses cached views and rejects a late response from the previous run', async (t) => {
  const records = {a: run('a'), b: run('b')};
  let hold = false, resolve;
  const ui = await setup(t, records, (path) => {
    if (hold && path.startsWith('/api/runs/a?')) return new Promise((r) => { resolve = r; });
  });
  ui.switchRun('b'); await settle();
  assert.equal(ui.$('objective').textContent, '研究 b');
  hold = true;
  ui.switchRun('a');
  assert.equal(ui.$('objective').textContent, '研究 a');
  assert.equal(ui.$('run-loading').hidden, true);
  assert.ok(ui.calls.at(-1).path.includes('after=120&event_limit=100'));
  ui.switchRun('b'); await settle();
  resolve(response({...records.a, objective: '迟到的旧响应'})); await settle();
  assert.equal(ui.$('objective').textContent, '研究 b');
});

test('graph shows frozen durations, zoom controls, and completed jobs on demand', async (t) => {
  const record = run('a', 'stopped');
  Object.assign(record, {started_at: '2026-09-14T10:00:00Z', finished_at: '2026-09-14T11:00:00Z'});
  record.agents.push({agent_id: 'researcher', direction: '探索方向', state: 'stopped'});
  record.nodes = [{iteration_id: 'node1', agent_id: 'researcher', state: 'completed', title: '分析方案', created_at: '2026-09-14T10:00:00Z', updated_at: '2026-09-14T10:10:00Z', assignment: {instruction: '研究一个方向'}, report: {created_at: '2026-09-14T10:10:00Z', message: '结论'}}];
  record.jobs = [{job_id: 'job1', agent_id: 'researcher', iteration_id: 'node1', status: 'succeeded', started_at: record.started_at, finished_at: record.finished_at}];
  const ui = await setup(t, {a: record});
  assert.equal(ui.$('run-elapsed').textContent, '1 小时 0 分');
  assert.equal(ui.$('graph-canvas').querySelectorAll('.graph-node').length, 1);
  assert.match(ui.$('graph-canvas').textContent, /历时 10 分 0 秒/);
  ui.$('graph-zoom-in').click();
  assert.equal(ui.$('graph-reset').textContent, '120%');
  assert.equal(ui.$('graph-canvas').style.transform, 'scale(1.2)');
  ui.$('graph-jobs').click();
  assert.equal(ui.$('graph-canvas').querySelectorAll('.graph-node').length, 2);
  ui.$('graph-expand').click();
  assert.equal(ui.$('graph-expand').getAttribute('aria-pressed'), 'true');
  ui.$('graph-canvas').querySelector('[data-node="node1"]').click();
  assert.match(ui.$('node-panel').textContent, /本轮历时 10 分 0 秒/);
});

test('progress reports do not stop node clocks; stopped nodes show best and full report history', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-16T10:27:17Z';
  record.brief.verification = {metric: 'speedup'};
  record.agents.push({agent_id: 'researcher', direction: '独立探索 2', state: 'running'});
  const reports = [
    {report_id: 'first', created_at: '2026-09-16T10:44:38Z', message: '尚未正确，继续探索。', delivery_status: 'delivered', references: [{uri: 'run://a/reports/first.json'}]},
    {report_id: 'second', created_at: '2026-09-16T11:27:03Z', message: '已有正确版本，继续验证。', delivery_status: 'pending', references: []},
  ];
  record.nodes = [{iteration_id: 'node1', agent_id: 'researcher', state: 'running',
    created_at: '2026-09-16T10:27:44Z', updated_at: '2026-09-16T10:27:44Z',
    reports, report: reports[1], verification: {score: 0.5951515, phase: 'search'}, assignment: {instruction: '独立探索整题'}}];
  record.events = [{sequence: 120, kind: 'report_submitted', agent_id: 'researcher', data: {report_id: 'second'}, created_at: reports[1].created_at}];
  const ui = await setup(t, {a: record, b: run('b')});
  let card = ui.$('graph-canvas').querySelector('[data-node="node1"]');
  assert.equal(card.querySelector('[data-elapsed-start]').dataset.elapsedEnd, undefined);
  assert.match(card.textContent, /最佳 0.5952×/);
  assert.match(card.textContent, /2 份报告/);
  assert.match(ui.$('activity').textContent, /提交研究报告/);
  card.click();
  const history = ui.$('node-panel').querySelector('.report-history');
  assert.equal(history.querySelectorAll('.report-entry').length, 2);
  assert.match(history.textContent, /已送达 RSI/);
  assert.match(history.textContent, /等待送达 RSI/);
  assert.match(history.textContent, /尚未正确，继续探索/);
  assert.match(history.textContent, /run:\/\/a\/reports\/first.json/);
  assert.equal(history.querySelectorAll('.report-entry')[0].open, true);
  assert.equal(history.querySelectorAll('.report-entry')[1].open, false);
  record.status = 'stopped'; record.finished_at = '2026-09-16T11:27:40Z';
  record.nodes[0].state = 'cancelled'; record.nodes[0].updated_at = '2026-09-16T11:27:17Z';
  ui.switchRun('b'); await settle();
  ui.switchRun('a'); await settle();
  card = ui.$('graph-canvas').querySelector('[data-node="node1"]');
  assert.match(card.textContent, /历时 59 分 33 秒/);
  card.click();
  assert.match(ui.$('node-panel').textContent, /本轮历时 59 分 33 秒/);
  assert.match(ui.$('node-panel').textContent, /结束时尚未送达 RSI/);
  assert.match(ui.$('node-panel').textContent, /本次探索最佳有效成绩/);
});

test('personal effective budget stays separate from setup time and refreshes without a new report', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-18T09:00:00Z';
  record.agents.push({agent_id: 'researcher', direction: '独立探索', state: 'running'});
  record.nodes = [{iteration_id: 'node1', agent_id: 'researcher', state: 'running', created_at: record.started_at}];
  const budget = {started_at: '2026-09-18T09:29:00Z', effective_seconds: 1680, limit_seconds: 3600,
    remaining_effective_seconds: 1920, gpu_queue_seconds: 0, api_wait_seconds: 0};
  record.budget = {clock: 'baseline-active-v1', researchers: {researcher: budget}};
  const ui = await setup(t, {a: record});
  const card = () => ui.$('graph-canvas').querySelector('[data-node="node1"]');
  assert.match(card().textContent, /有效 28.0 \/ 60 分钟/);
  card().click();
  assert.match(ui.$('node-panel').textContent, /剩余 32.0 分钟/);
  assert.equal(ui.$('node-panel').querySelector('[data-elapsed-start]').dataset.elapsedStart, budget.started_at);
  budget.effective_seconds += 60; budget.remaining_effective_seconds -= 60;
  await ui.poll();
  assert.match(card().textContent, /有效 29.0 \/ 60 分钟/);
  assert.match(ui.$('node-panel').textContent, /剩余 31.0 分钟/);
});

test('chat displays pending messages, reuses their IDs on retry, then waits for the agent', async (t) => {
  const record = run('a');
  let deliver;
  const ui = await setup(t, {a: record}, (path, options) => {
    if (path.endsWith('/chat')) return new Promise((resolve) => { deliver = () => {
      const message = JSON.parse(options.body);
      record.messages.push({...message, sender: 'user', created_at: new Date().toISOString()});
      record.agents[0].state = 'running';
      resolve(response({message_id: message.message_id}));
    }; });
  });
  ui.input('chat-input', '请确认目标'); ui.submit('chat-form'); await settle();
  assert.match(ui.$('messages').textContent, /请确认目标/);
  assert.match(ui.$('chat-feedback').textContent, /正在发送/);
  assert.equal(ui.$('send-message').disabled, true);
  deliver(); await settle();
  assert.match(ui.$('chat-feedback').textContent, /等待 Interaction Agent 回复/);
  assert.equal(ui.$('chat-input').value, '');
  assert.equal(ui.$('messages').querySelectorAll('.user').length, 1);
});

test('failed sends retain the draft and retry the same delivery identity', async (t) => {
  const record = run('a');
  const sent = [];
  const ui = await setup(t, {a: record}, (path, options) => {
    if (path.endsWith('/chat')) {
      sent.push(JSON.parse(options.body));
      if (sent.length === 1) return Promise.resolve(response({error: '连接暂不可用'}, false));
      record.messages.push({...sent.at(-1), sender: 'user'});
      return Promise.resolve(response({message_id: sent.at(-1).message_id}));
    }
  });
  ui.input('chat-input', '保留这条消息'); ui.submit('chat-form'); await settle();
  assert.equal(ui.$('chat-input').value, '保留这条消息');
  assert.match(ui.$('messages').textContent, /发送未确认/);
  [...ui.$('messages').querySelectorAll('button')].find((button) => button.textContent === '重试发送').click(); await settle();
  assert.equal(sent.length, 2);
  assert.equal(sent[0].message_id, sent[1].message_id);
});

test('creation configures the first conversation and modal dropdowns stay interactive', async (t) => {
  const records = {a: run('a')};
  let submitted;
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = run('new');
      return Promise.resolve(response(records.new));
    }
  });
  ui.$('new-run').click();
  assert.equal(ui.$('create-base-url').value, defaults.base_url);
  assert.equal(ui.$('create-model').value, defaults.model);
  assert.match(ui.$('create-api-key').placeholder, /默认密钥/);
  assert.equal(ui.$('create-effort-options').closest('dialog'), ui.$('create-dialog'));
  ui.$('create-effort-trigger').click();
  const option = ui.$('create-effort-options').querySelector('[role="option"]');
  option.click();
  ui.input('create-base-url', 'https://custom.example/v1');
  ui.input('create-api-key', 'dummy-ui-key');
  ui.input('create-model', 'custom-model');
  ui.$('create-backend-trigger').click();
  ui.$('create-backend-options').querySelectorAll('[role="option"]')[1].click();
  ui.submit('create-form'); await settle();
  assert.deepEqual(submitted.model_settings, {base_url: 'https://custom.example/v1', api_key: 'dummy-ui-key', auth_mode: 'api_key', model: 'custom-model', reasoning_effort: 'high', backend: 'codex'});
  assert.equal(ui.$('create-dialog').open, false);
  assert.equal(ui.$('create-api-key').value, '');
  assert.equal(ui.$('objective').textContent, '研究 new');
  ui.$('new-run').click();
  ui.$('create-backend-trigger').click();
  ui.$('create-backend-options').querySelectorAll('[role="option"]')[1].click();
  ui.$('create-auth-mode-trigger').click();
  ui.$('create-auth-mode-options').querySelectorAll('[role="option"]')[1].click();
  assert.equal(ui.$('create-api-key').disabled, true);
  assert.equal(ui.$('create-base-url').closest('label').hidden, true);
  ui.submit('create-form'); await settle();
  assert.equal(submitted.model_settings.auth_mode, 'codex_login');
  assert.equal(submitted.model_settings.api_key, '');
  assert.equal(submitted.model_settings.base_url, 'https://api.openai.com');
});

test('agent timeline grows down and shares use distinct reserved rows with inspectable provenance', async (t) => {
  const record = run('graph', 'stopped');
  record.finished_at = '2026-09-18T10:00:00Z';
  record.agents = [{agent_id: 'rsi', state: 'stopped'}, ...['a', 'b', 'c'].map((id) => ({agent_id: id, direction: `研究员 ${id}`, state: 'stopped'}))];
  record.nodes = ['a', 'b', 'c'].map((id) => ({iteration_id: `node-${id}`, agent_id: id, created_at: '2026-09-18T09:00:00Z', state: 'cancelled', reports: []}));
  const reference = {agent_id: 'a', uri: 'run://graph/reports/report-a.json', summary: '原始发现'};
  record.communications = [
    {message_id: 'share1', sender: 'rsi', recipient: 'c', created_at: '2026-09-18T09:10:00Z', text: '独立验证 A 的发现', sources: [reference], references: [{uri: reference.uri}], delivery_status: 'delivered'},
    {message_id: 'share2', sender: 'rsi', recipient: 'b', created_at: '2026-09-18T09:10:01Z', text: '另一条可尝试的建议', sources: [reference], references: [{uri: reference.uri}], delivery_status: 'pending'},
    {message_id: 'advice', sender: 'rsi', recipient: 'b', created_at: '2026-09-18T09:11:00Z', text: '没有明确来源的建议', sources: [], references: [], delivery_status: 'delivered'},
  ];
  const ui = await setup(t, {graph: record});
  assert.equal(ui.$('node-count').textContent, '3');
  assert.equal(ui.$('graph-canvas').querySelectorAll('.agent-lane').length, 4);
  assert.equal(ui.$('graph-canvas').querySelectorAll('.peer-sharing').length, 2);
  assert.equal(ui.$('graph-canvas').querySelectorAll('.leader-advice').length, 1);
  const event = (id) => ui.$('graph-canvas').querySelector(`[data-event="message:${id}"]`);
  assert.ok(parseFloat(event('share1').style.top) < parseFloat(event('share2').style.top));
  assert.ok(parseFloat(event('share2').style.top) < parseFloat(event('advice').style.top));
  // No intermediate agent card occupies the cross-lane route's row.
  const y = parseFloat(event('share1').style.top);
  assert.equal([...ui.$('graph-canvas').querySelectorAll('.timeline-event')].filter((card) => parseFloat(card.style.top) === y).length, 1);
  event('share1').click();
  assert.equal(ui.$('graph-detail').hidden, false);
  assert.match(ui.$('graph-detail').textContent, /原始发现/);
  assert.match(ui.$('graph-detail').textContent, /run:\/\/graph\/reports\/report-a.json/);
  event('share2').click();
  assert.match(ui.$('graph-detail').textContent, /结束时未送达/);
  ui.$('graph-sharing').click();
  assert.equal(ui.$('graph-canvas').querySelectorAll('.event-start').length, 0);
  assert.equal(ui.$('graph-canvas').querySelectorAll('.event-share').length, 2);
  const paths = [...ui.$('graph-canvas').querySelectorAll('.peer-sharing')].map((path) => path.getAttribute('d'));
  assert.equal(new Set(paths).size, 2);
});

test('pass1 goal context updates while running and clears when switching to passk', async (t) => {
  const a = run('a', 'running');
  a.collaboration = {...a.collaboration, mode: 'pass1', label: 'RSI pass1'};
  a.brief.instructions = '修复失败测试，保持 API 不变';
  a.agents = [{agent_id: 'rsi', state: 'running'}];
  a.goal = {status: 'active', summary: '已定位失败', current_step: '修复实现',
    next_steps: ['运行测试'], acceptance: ['全部测试通过'], evidence: [], blocker: '',
    revision: 1, updated_at: '2026-09-21T10:00:00Z'};
  const records = {a, b: run('b')};
  const ui = await setup(t, records, (path) => {
    if (path.endsWith('/cancel')) {
      a.status = 'paused';
      a.agents[0] = {agent_id: 'rsi', state: 'waiting', wait_reason: 'user_cancelled'};
      return response(a);
    }
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults,
      collaboration_catalog: {...collaborationCatalog, modes: [...collaborationCatalog.modes, {id: 'pass1', label: 'RSI pass1'}],
        presets: [...collaborationCatalog.presets, {id: 'pass1', label: 'RSI pass1', mode: 'pass1', modules: []}]},
      default_collaboration: {mode: 'passk', modules: []}, models: []});
  });
  assert.equal(ui.$('goal-context').hidden, false);
  assert.equal(ui.$('goal-current-step').textContent, '修复实现');
  assert.match(ui.$('chat-input-label').textContent, /RSI Agent/);
  assert.match(ui.$('chat-feedback').textContent, /正在执行目标/);
  assert.equal(ui.$('cancel-turn').hidden, false);
  ui.input('chat-input', '保留草稿');
  ui.$('chat-input').setSelectionRange(0, 2);
  ui.$('chat-input').dispatchEvent(new ui.window.KeyboardEvent('keydown', {key: 'c', ctrlKey: true, bubbles: true, cancelable: true}));
  assert.equal(ui.calls.filter((call) => call.path.endsWith('/cancel')).length, 0);
  ui.$('chat-input').setSelectionRange(4, 4);
  ui.$('chat-input').dispatchEvent(new ui.window.KeyboardEvent('keydown', {key: 'c', ctrlKey: true, bubbles: true, cancelable: true}));
  await settle();
  assert.equal(ui.calls.filter((call) => call.path.endsWith('/cancel')).length, 1);
  assert.match(ui.$('chat-feedback').textContent, /当前执行已取消/);
  assert.equal(ui.$('chat-input').value, '保留草稿');
  assert.equal(ui.$('cancel-turn').hidden, true);
  a.status = 'running'; a.agents[0] = {agent_id: 'rsi', state: 'running'};
  await ui.poll();
  a.events.push({sequence: 121, kind: 'assistant_output', agent_id: 'rsi',
    data: {text: '已定位视觉输入限制 <script>unsafe</script>'}});
  a.goal_turns = [{number: 1, status: 'running', started_at: '2026-09-21T10:00:00Z', inputs: []}];
  a.messages.push({message_id: 'followup', sender: 'user', recipient: 'rsi', text: '使用远程目录', created_at: '2026-09-21T10:01:00Z'});
  await ui.poll();
  assert.match(ui.$('chat-feedback').textContent, /已定位视觉输入限制/);
  assert.equal(ui.$('chat-feedback').querySelector('script'), null);
  assert.match(ui.$('chat-feedback').textContent, /补充消息仍在排队/);
  a.goal_turns[0].inputs = [...a.messages];
  await ui.poll();
  assert.doesNotMatch(ui.$('chat-feedback').textContent, /补充消息仍在排队/);
  a.goal = {...a.goal, status: 'blocked', summary: '等待条件', blocker: '<script>缺少依赖</script>', revision: 2};
  await ui.poll();
  assert.equal(ui.$('goal-status').textContent, '等待解除阻塞');
  assert.match(ui.$('goal-blocker').textContent, /缺少依赖/);
  assert.equal(ui.$('goal-blocker').querySelector('script'), null);
  a.goal = {...a.goal, status: 'completed', summary: '验收完成', blocker: '', evidence: ['pytest: 通过'], revision: 3};
  a.status = 'succeeded'; a.worker_alive = false;
  await ui.poll();
  assert.equal(ui.$('goal-status').textContent, '目标完成');
  assert.match(ui.$('goal-evidence').textContent, /pytest: 通过/);
  ui.switchRun('b'); await settle();
  assert.equal(ui.$('goal-context').hidden, true);
  assert.doesNotMatch(ui.$('chat-feedback').textContent, /已定位视觉输入限制/);
  ui.$('new-run').click();
  ui.$('create-collaboration-mode-trigger').click();
  [...ui.$('create-collaboration-mode-options').querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('pass1')).click();
  assert.equal(ui.$('create-message').required, true);
  assert.equal(ui.$('create-submit').textContent, '开始目标');
  assert.match(ui.$('create-description').textContent, /一个 Agent/);
});


test('goal progress streams across turns without exposing round boundaries', async (t) => {
  const a = run('a', 'running');
  a.collaboration = {...a.collaboration, mode: 'pass1'};
  const turn = (number, status = 'completed') => ({number, status,
    started_at: '2026-09-21T10:00:00Z', brief: {objective: '修复代码', instructions: '保持 API'},
    inputs: [{sender: 'user', text: '本轮输入 ' + number}],
    context_before: {summary: '开始前快照'}, context_after: {summary: '当前进展'},
    output: ['开始检查'], tokens_before: 10, tokens_after: 20, tools_completed: 1});
  a.goal_turns = [turn(3, 'running')]; a.goal_turns_next = 3; a.goal_turns_total = 3;
  const ui = await setup(t, {a}, (path) => path.includes('goal_before=3')
    ? response({...a, goal_turns: [turn(2), turn(1)], goal_turns_next: null}) : null);
  assert.equal(ui.$('goal-turn-list').querySelector('details'), null);
  assert.equal(ui.$('runtime-turn-count').parentElement.hidden, true);
  a.goal_turns[0].output[0] = '已**定位问题**。\n\n接下来验证。\n\n- 检查 `loss`\n- 运行测试\n\n```python\nprint(42)\n\nprint(43)\n```';
  await ui.poll();
  const message = ui.$('goal-turn-list').firstElementChild;
  assert.equal(message.querySelectorAll('p').length, 2);
  assert.equal(message.querySelector('strong').textContent, '定位问题');
  assert.equal(message.querySelectorAll('ul > li').length, 2);
  assert.match(message.querySelector('pre').textContent, /print\(42\)\n\nprint\(43\)/);
  a.goal_turns[0].output.push('<script>新进展</script>');
  a.goal_turns[0].context_after = {summary: '验证通过'};
  await ui.poll();
  assert.match(ui.$('goal-turn-list').textContent, /新进展/);
  assert.equal(ui.$('goal-turn-list').querySelector('script'), null);
  assert.doesNotMatch(ui.$('goal-turn-list').textContent, /exec_command/);
  assert.doesNotMatch(ui.$('goal-turn-list').textContent, /快照|回合|本轮输入/);
  ui.$('goal-load-older').click(); await settle();
  assert.equal(ui.$('goal-turn-list').children.length, 4);
  assert.equal(ui.$('goal-load-older').hidden, true);
  assert.equal(ui.$('goal-turn-list').lastElementChild.textContent, '<script>新进展</script>');
  await ui.poll();
  assert.equal(ui.$('goal-turn-list').children.length, 4);
});

for (const hours of ['', '4']) {
test(`development form prepares a draft with ${hours || 'no'} explicit time limit`, async (t) => {
  const defaultsSetup = {repository: '/workspace/repo', revision: 'main', acceptance: 'Run project tests', parallelism: 2, wall_seconds: 14400};
  const records = {a: run('a')};
  let submitted;
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults,
      collaboration_catalog: collaborationCatalog, default_collaboration: {mode: 'passk', modules: []}, models: [],
      workspace_ready: true});
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = {...run('new'), preparation: {status: 'pending', setup: {...defaultsSetup, wall_seconds: submitted.wall_seconds}, checks: []}};
      return response({run_id: 'new'});
    }
    if (path.endsWith('/prepare')) {
      records.new.preparation.status = 'running';
      return response({run_id: 'new'});
    }
  });
  ui.$('new-run').click();
  for (const id of ['create-profile', 'create-repository', 'create-revision', 'create-parallelism', 'create-acceptance']) assert.equal(ui.$(id), null);
  assert.equal(ui.$('create-options').open, false);
  assert.equal(ui.$('create-hours').value, ''); // Ignore the profile's four-hour preset.
  ui.input('create-hours', hours);
  ui.input('create-message', 'Implement the feature');
  ui.submit('create-form'); await settle();
  assert.equal(submitted.use_workspace, true);
  assert.equal(submitted.development, undefined);
  assert.equal(submitted.message, 'Implement the feature');
  assert.equal(submitted.wall_seconds, hours ? 14400 : null);
  assert.equal(ui.$('preparation').hidden, false);
  assert.equal(ui.$('start').hidden, true);
  records.new.preparation.status = 'ready';
  records.new.preparation.checks = [{name: 'Workspace', status: 'passed', message: 'Ready'}];
  await ui.poll();
  assert.match(ui.$('preparation-checks').textContent, /通过 · Workspace/);
  assert.equal(ui.$('start').hidden, false);
  assert.match(ui.$('preparation-workspace').textContent, hours ? /4 小时/ : /未设置运行时限/);
});
}

test('passk routes human guidance to one researcher and revises the shared goal', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T03:00:00Z';
  record.finalization = {};
  record.brief = {objective: 'Old goal', instructions: 'Old requirements', revision: 0, limits: {}};
  record.human = {paused_agents: [], assisted: false, goal_changes: []};
  for (let i = 1; i <= 2; i++) {
    record.agents.push({agent_id: `research_${i}`, direction: `独立探索 ${i}`, state: 'running'});
    record.nodes.push({iteration_id: `node${i}`, agent_id: `research_${i}`, state: 'running', assignment: {instruction: 'Old goal'}});
  }
  const ui = await setup(t, {a: record}, (path, options) => {
    if (options.method !== 'POST') return;
    const body = JSON.parse(options.body);
    if (path.endsWith('/chat')) {
      record.messages.push({...body, message_id: 'server-' + body.message_id, sender: 'user', created_at: record.started_at, delivery_status: 'pending'});
      record.human.assisted = true;
      return response({message_id: 'server-' + body.message_id});
    }
    if (path.endsWith('/agent-control')) {
      record.human.paused_agents = body.paused ? [body.agent_id] : [];
      return response(record);
    }
    if (path.endsWith('/revise-goal')) {
      assert.equal(body.expected_revision, 0);
      assert.ok(body.request_id);
      record.brief = {...record.brief, objective: body.objective, instructions: body.instructions, revision: 1};
      record.objective = body.objective;
      return response(record);
    }
  });
  assert.equal(ui.$('chat-input').disabled, false);
  ui.$('graph-canvas').querySelector('.graph-node').click();
  const button = (text) => [...ui.$('node-panel').querySelectorAll('button')].find((b) => b.textContent === text);
  button('暂停这个 Agent').click(); await settle();
  assert.deepEqual(ui.calls.find((x) => x.path.endsWith('/agent-control')).body, {agent_id: 'research_1', paused: true});
  assert.equal(button('继续这个 Agent').disabled, false);
  button('指导这个 Agent').click();
  ui.input('chat-input', 'Use Chinese'); ui.submit('chat-form'); await settle();
  const sent = ui.calls.find((x) => x.path.endsWith('/chat')).body;
  assert.equal(sent.recipient, 'research_1');
  assert.equal(ui.$('messages').querySelectorAll('article').length, 1);
  assert.match(ui.$('messages').textContent, /待送达/);
  record.messages[0].delivery_status = 'delivered'; await ui.poll();
  assert.match(ui.$('messages').textContent, /已送达/);
  assert.match(ui.$('human-status').textContent, /人工指导/);
  ui.$('revise-goal').click();
  ui.input('revised-objective', 'New shared goal');
  ui.input('revised-instructions', 'New requirements');
  ui.submit('revise-goal-form'); await settle();
  assert.equal(ui.$('objective').textContent, 'New shared goal');
  assert.match(ui.$('human-status').textContent, /v2/);
});
