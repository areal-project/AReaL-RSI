import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {Window} from 'happy-dom';

const assets = new URL('../../src/areal_rsi/ui/workbench_static/', import.meta.url);
const source = (name) => readFileSync(new URL(name, assets), 'utf8');
const defaults = {base_url: 'https://4ipic.com', model: 'gpt-6-astra', reasoning_effort: 'medium', has_credentials: true};
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
    if (path === '/api/bootstrap') return response({csrf_token: 'test', capabilities: ['agent-trace'], default_model_settings: defaults, collaboration_catalog: collaborationCatalog, default_collaboration: {mode: 'passk', modules: []}, models: [{id: 'gpt-6-astra', efforts: ['medium', 'high', 'xhigh']}, {id: 'gpt-5.6-sol', efforts: ['medium', 'high', 'xhigh']}]});
    if (path === '/api/runs') return response({runs: Object.values(records)});
    if (path.includes('/trace?')) return response({agent_id: new URL(path, 'http://localhost').searchParams.get('agent_id'), entries: [], next_before: null});
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

test('task submission works on HTTP browsers without crypto.randomUUID', async (t) => {
  const records = {};
  const submitted = {};
  const ui = await setup(t, records, (path, options) => {
    if (options.method !== 'POST') return;
    if (path === '/api/runs') {
      submitted.create = JSON.parse(options.body);
      records.new = {...run('new', 'running'), started_at: '2026-09-30T00:00:00Z'};
      records.new.brief.acceptance = ['Task complete'];
      return response({run_id: 'new'});
    }
    if (path.endsWith('/chat')) {
      submitted.chat = JSON.parse(options.body);
      return response({message_id: submitted.chat.message_id});
    }
    if (path.endsWith('/update-goal')) {
      submitted.goal = JSON.parse(options.body);
      return response(records.new);
    }
  });
  Object.defineProperty(ui.window.crypto, 'randomUUID', {value: undefined, configurable: true});
  assert.equal(ui.window.crypto.randomUUID, undefined);

  ui.$('new-run').click();
  ui.input('create-message', 'Complete the task');
  ui.submit('create-form'); await settle();
  assert.equal(ui.$('objective').textContent, '研究 new');

  ui.input('chat-input', 'One more requirement');
  ui.submit('chat-form'); await settle();
  ui.$('update-goal').click();
  ui.input('revised-objective', 'Revised task');
  ui.input('revised-acceptance', 'Task complete');
  ui.submit('update-goal-form'); await settle();

  const ids = [submitted.create?.message_id, submitted.chat?.message_id, submitted.goal?.request_id];
  assert.ok(ids.every((id) => /^[0-9a-f]{32}$/.test(id)));
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(ui.$('create-error').hidden, true);
});

for (const count of [1, 2]) {
test(`new tasks default to one researcher despite deployment default ${count}`, async (t) => {
  let submitted;
  const records = {a: run('a')};
  records.a.collaboration.max_agents = count;
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/bootstrap') return response({
      csrf_token: 'test', default_model_settings: defaults, models: [],
      collaboration_catalog: collaborationCatalog,
      default_collaboration: {mode: 'passk', max_agents: count, modules: ['leader_guidance']},
    });
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = run('new');
      return response(records.new);
    }
  });
  assert.equal(ui.$('collaboration-summary').textContent, 'pass@k');
  assert.equal(ui.$('collaboration-mode'), null);
  assert.equal(ui.$('save-collaboration'), null);
  ui.$('new-run').click();
  assert.equal(ui.$('create-collaboration-mode'), null);
  assert.doesNotMatch(ui.$('create-dialog').textContent, /pass@1|pass1|Leader Prompt/);
  ui.input('create-message', 'Complete the task');
  ui.submit('create-form'); await settle();
  assert.deepEqual(submitted.collaboration, {mode: 'passk', modules: [], max_agents: 1});
});
}

test('historical coordination is preserved but reuse creates a plain passk task', async (t) => {
  const old = run('a', 'stopped');
  old.collaboration.max_agents = 3;
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
    {mode: 'passk', modules: [], max_agents: 3});
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
  assert.equal(ui.$('collaboration-mode-trigger'), null);
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
  assert.match(ui.$('chat-status').textContent, /发送失败/);
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
  assert.match(ui.$('create-effort-trigger').textContent, /中 · medium/);
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
  assert.equal(ui.$('create-backend-trigger').textContent, 'codex');
  ui.submit('create-form'); await settle();
  assert.deepEqual(submitted.model_settings, {base_url: 'https://custom.example/v1', api_key: 'dummy-ui-key', auth_mode: 'api_key', model: 'custom-model', reasoning_effort: 'medium', backend: 'codex'});
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

test('new task shows the selected reasoning effort for gpt-5.6-sol', async (t) => {
  const records = {a: run('a')};
  let submitted;
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = run('new');
      return response(records.new);
    }
  });
  ui.$('new-run').click();
  ui.input('create-model', 'gpt-5.6-sol');
  ui.$('create-effort-trigger').click();
  [...ui.$('create-effort-options').querySelectorAll('[role="option"]')]
    .find((option) => option.textContent.includes('xhigh')).click();
  ui.$('create-effort-trigger').click();
  const medium = [...ui.$('create-effort-options').querySelectorAll('[role="option"]')]
    .find((option) => option.textContent.includes('medium'));
  medium.click();
  assert.match(ui.$('create-effort-trigger').textContent, /中 · medium/);
  ui.submit('create-form'); await settle();
  assert.equal(submitted.model_settings.reasoning_effort, 'medium');
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

test('single-researcher goal context updates and clears when switching tasks', async (t) => {
  const a = run('a', 'running');
  a.collaboration = {...a.collaboration, mode: 'passk', max_agents: 1, label: 'pass@1'};
  a.started_at = '2026-09-21T10:00:00Z';
  a.brief.instructions = '修复失败测试，保持 API 不变';
  a.agents = [{agent_id: 'research_one', state: 'running'}];
  a.goal = {status: 'active', summary: '已定位失败', current_step: '修复实现',
    next_steps: ['运行测试'], acceptance: ['全部测试通过'], evidence: [], blocker: '',
    revision: 1, updated_at: '2026-09-21T10:00:00Z'};
  const records = {a, b: run('b')};
  const ui = await setup(t, records, (path) => {
    if (path.endsWith('/cancel')) {
      a.status = 'paused';
      a.agents[0] = {agent_id: 'research_one', state: 'waiting', wait_reason: 'user_cancelled'};
      return response(a);
    }
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults,
      collaboration_catalog: {...collaborationCatalog, modes: [...collaborationCatalog.modes, {id: 'pass1', label: 'RSI pass1'}],
        presets: [...collaborationCatalog.presets, {id: 'pass1', label: 'RSI pass1', mode: 'pass1', modules: []}]},
      default_collaboration: {mode: 'passk', modules: []}, models: []});
  });
  assert.equal(ui.$('goal-context').hidden, false);
  assert.equal(ui.$('goal-current-step').textContent, '修复实现');
  assert.match(ui.$('chat-input-label').textContent, /Research Agent/);
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
  a.status = 'running'; a.agents[0] = {agent_id: 'research_one', state: 'running'};
  await ui.poll();
  a.events.push({sequence: 121, kind: 'assistant_output', agent_id: 'research_one',
    data: {text: '已定位视觉输入限制 <script>unsafe</script>'}});
  a.goal_turns = [{number: 1, status: 'running', started_at: '2026-09-21T10:00:00Z', inputs: []}];
  a.messages.push({message_id: 'followup', sender: 'user', recipient: 'research_one', text: '使用远程目录', created_at: '2026-09-21T10:01:00Z'});
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
  assert.equal(ui.$('create-collaboration-mode'), null);
  assert.equal(ui.$('create-submit').textContent, '创建任务');
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
test(`workspace creation starts intake with ${hours || 'no'} explicit time limit`, async (t) => {
  const defaultsSetup = {repository: '/workspace/repo', revision: 'main', acceptance: 'Run project tests', parallelism: 2, wall_seconds: 14400};
  const records = {a: run('a')};
  let submitted;
  const ui = await setup(t, records, (path, options) => {
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults,
      collaboration_catalog: collaborationCatalog, default_collaboration: {mode: 'passk', modules: []}, models: [],
      workspace_ready: true});
    if (path === '/api/runs' && options.method === 'POST') {
      submitted = JSON.parse(options.body);
      records.new = {...run('new'), intake: {status: 'discussing'}, preparation: {status: 'pending', setup: {...defaultsSetup, wall_seconds: submitted.wall_seconds}, checks: []}};
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
  assert.equal(ui.$('chat-input').disabled, false);
  assert.equal(ui.$('chat-panel').hidden, false);
  assert.ok(!ui.calls.some((call) => call.path.endsWith('/prepare')));
  records.new.preparation.status = 'ready';
  records.new.preparation.checks = [{name: 'Workspace', status: 'passed', message: 'Ready'}];
  await ui.poll();
  assert.match(ui.$('preparation-checks').textContent, /通过 · Workspace/);
  assert.equal(ui.$('start').hidden, true);
  records.new.intake.status = 'ready';
  await ui.poll();
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
    if (path.endsWith('/update-goal')) {
      assert.equal(body.expected_revision, 0);
      assert.deepEqual(body.acceptance, ['New check', 'Another check']);
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
  ui.$('update-goal').click();
  ui.input('revised-objective', 'New shared goal');
  ui.input('revised-instructions', 'New requirements');
  ui.input('revised-acceptance', 'New check\nAnother check');
  ui.submit('update-goal-form'); await settle();
  assert.equal(ui.$('objective').textContent, 'New shared goal');
  assert.match(ui.$('human-status').textContent, /v2/);
});

test('individually completed researchers display completion while peers keep running', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T10:00:00Z';
  record.collaboration.max_agents = 2;
  record.collaboration.rsi_model_enabled = false;
  record.goals = {research_1: {status: 'completed', summary: 'done', updated_at: '2026-09-22T10:01:00Z'}};
  record.agents = [{agent_id: 'rsi', state: 'waiting'}, {agent_id: 'research_1', state: 'waiting'}, {agent_id: 'research_2', state: 'running'}];
  record.nodes = [1, 2].map((i) => ({iteration_id: `n${i}`, agent_id: `research_${i}`, state: 'running', created_at: record.started_at,
    assignment: {instruction: 'Independent work'}, goal: record.goals[`research_${i}`]}));
  const ui = await setup(t, {a: record});
  const cards = [...ui.$('graph-canvas').querySelectorAll('.agent-summary')];
  assert.match(cards[0].textContent, /自动调度/);
  assert.match(cards[1].textContent, /已完成/);
  assert.equal(cards[1].classList.contains('active-node'), false);
  assert.equal(cards[2].classList.contains('active-node'), true);
  assert.match(cards[1].textContent, /1 分 0 秒/);
});

function traceRun() {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T10:00:00Z';
  record.agents = [{agent_id: 'rsi', state: 'waiting'}, ...[1, 2].map((i) => ({agent_id: `research_${i}`, state: 'running', direction: `Researcher ${i}`}))];
  record.nodes = [1, 2].map((i) => ({iteration_id: `node${i}`, agent_id: `research_${i}`, state: 'running', created_at: record.started_at, assignment: {instruction: 'Task'}}));
  return record;
}

test('researcher trace pages keep tools, guidance and peers separate', async (t) => {
  const record = traceRun();
  record.agent_activity = {research_1: {type: 'tool_completed', updated_at: '2020-01-01T00:00:00Z', started_at: '2020-01-01T00:00:00Z', active_tools: {}}};
  const ui = await setup(t, {a: record}, (path) => {
    if (!path.includes('/trace?')) return;
    const query = new URL(path, 'http://localhost').searchParams;
    const id = query.get('agent_id');
    if (id === 'research_2') return response({agent_id: id, entries: [{sequence: 30, kind: 'assistant_output', text: 'peer-only-output', created_at: record.started_at}], next_before: null});
    if (query.has('before')) return response({agent_id: id, entries: [{sequence: 5, kind: 'user_message', text: 'earlier guidance', created_at: record.started_at}], next_before: null});
    return response({agent_id: id, next_before: 20, entries: [
      {sequence: 20, kind: 'tool_started', name: 'exec_command', call_id: 'cmd', status: 'running', created_at: '2026-09-22T10:00:00Z', arguments: '{"command":"cat alpha.py"}'},
      {sequence: 21, kind: 'assistant_output', text: '<script>unsafe</script>检查 alpha', created_at: '2026-09-22T10:00:01Z'},
      {sequence: 22, kind: 'tool_completed', name: 'exec_command', call_id: 'cmd', status: 'failed', created_at: '2026-09-22T10:00:05Z', result: '{"aggregatedOutput":"check failed","exitCode":1}'},
    ]});
  });
  ui.$('graph-canvas').querySelector('[data-node="node1"]').click(); await settle();
  assert.match(ui.$('node-panel').textContent, /执行轨迹/);
  assert.match(ui.$('node-panel').textContent, /等待模型响应/);
  assert.match(ui.$('node-panel').textContent, /不能判定卡死/);
  assert.equal(ui.$('node-panel').querySelectorAll('.trace-tool').length, 1);
  const tool = ui.$('node-panel').querySelector('.trace-tool'); tool.open = true; await settle();
  assert.match(tool.textContent, /cat alpha.py/);
  assert.match(tool.textContent, /失败/);
  assert.match(tool.textContent, /5 秒/);
  assert.match(tool.textContent, /退出码 1/);
  assert.equal(ui.$('node-panel').querySelector('script'), null);
  [...ui.$('node-panel').querySelectorAll('button')].find((b) => b.textContent === '加载更早轨迹').click(); await settle();
  assert.match(ui.$('node-panel').textContent, /earlier guidance/);
  ui.$('graph-canvas').querySelector('[data-node="node2"]').click(); await settle();
  assert.match(ui.$('node-panel').textContent, /peer-only-output/);
  assert.doesNotMatch(ui.$('node-panel').textContent, /alpha.py|earlier guidance/);
  ui.$('graph-canvas').querySelector('[data-node="node1"]').click(); await settle();
  assert.equal(ui.$('node-panel').querySelector('.trace-tool').open, true);
});

test('late trace replies do not replace the currently selected researcher', async (t) => {
  const record = traceRun(); let finish;
  const ui = await setup(t, {a: record}, (path) => {
    if (!path.includes('/trace?')) return;
    if (path.includes('agent_id=research_1')) return new Promise((resolve) => { finish = resolve; });
    return response({agent_id: 'research_2', entries: [{sequence: 2, kind: 'assistant_output', text: 'second selected'}], next_before: null});
  });
  ui.$('graph-canvas').querySelector('[data-node="node1"]').click(); await settle();
  ui.$('graph-canvas').querySelector('[data-node="node2"]').click(); await settle();
  finish(response({agent_id: 'research_1', entries: [{sequence: 1, kind: 'assistant_output', text: 'late first'}], next_before: null})); await settle();
  assert.match(ui.$('node-panel').textContent, /second selected/);
  assert.doesNotMatch(ui.$('node-panel').textContent, /late first/);
});

test('agent diagnostics distinguish tool execution, API waits and explicit blocking', async (t) => {
  const record = traceRun();
  record.agent_activity = {research_1: {type: 'tool_completed', api_available: false,
    active_tools: {slow: {name: 'slow-command', started_at: new Date().toISOString()}}}};
  const ui = await setup(t, {a: record});
  ui.$('graph-canvas').querySelector('[data-node="node1"]').click(); await settle();
  assert.match(ui.$('node-panel').querySelector('.agent-diagnostics').textContent, /slow-command/);
  record.agent_activity.research_1.active_tools = {}; await ui.poll();
  assert.match(ui.$('node-panel').querySelector('.agent-diagnostics').textContent, /模型服务暂不可用/);
  record.goals = {research_1: {status: 'blocked', blocker: '需要你提供测试数据'}}; await ui.poll();
  assert.match(ui.$('node-panel').querySelector('.agent-diagnostics').textContent, /需要你提供测试数据/);
  record.goals = {}; record.worker_alive = false; await ui.poll();
  assert.match(ui.$('node-panel').querySelector('.agent-diagnostics').textContent, /执行器未运行/);
});


test('guidance follows the selected second researcher after a trace switch', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T00:00:00Z';
  for (let i = 1; i <= 2; i++) {
    record.agents.push({agent_id: `research_${i}`, direction: `独立探索 ${i}`, state: 'waiting'});
    record.nodes.push({iteration_id: `node${i}`, agent_id: `research_${i}`, state: 'running', assignment: {instruction: 'Task'}});
  }
  const ui = await setup(t, {a: record});
  ui.$('node-tab').click(); await settle();
  ui.input('trace-researcher', 'node2');
  ui.$('trace-researcher').dispatchEvent(new ui.window.Event('change'));
  await settle(); await ui.poll();
  [...ui.$('node-panel').querySelectorAll('button')].find(b => b.textContent === '指导这个 Agent').click();
  assert.equal(ui.$('chat-input-label').textContent, '向 独立探索 2 发送消息');
  ui.input('chat-input', 'Only researcher 2'); ui.submit('chat-form'); await settle();
  assert.equal(ui.calls.find(c => c.path.endsWith('/chat')).body.recipient, 'research_2');
});

test('a blocked researcher can be selected and guided while the run is active', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T00:00:00Z';
  record.agents.push({agent_id: 'research_1', direction: '独立探索 1', state: 'waiting', wait_reason: 'goal_blocked'});
  record.nodes.push({iteration_id: 'node1', agent_id: 'research_1', state: 'running',
    goal: {status: 'blocked', blocker: '需要训练环境', summary: '等待补充'}, assignment: {instruction: 'Task'}});
  record.goals = {research_1: record.nodes[0].goal};
  const ui = await setup(t, {a: record});
  ui.$('node-tab').click(); await settle();
  const guide = [...ui.$('node-panel').querySelectorAll('button')]
    .find((button) => button.textContent === '指导这个 Agent');
  const now = ui.window.Date.now;
  ui.window.Date.now = () => now() + 10000;
  await ui.poll();
  assert.equal([...ui.$('node-panel').querySelectorAll('button')]
    .find((button) => button.textContent === '指导这个 Agent'), guide);
  guide.click();
  assert.equal(ui.$('chat-panel').hidden, false);
  assert.equal(ui.$('chat-input').disabled, false);
  assert.equal(ui.$('chat-input-label').textContent, '向 独立探索 1 发送消息');
  ui.input('chat-input', '训练环境已准备好'); ui.submit('chat-form'); await settle();
  assert.equal(ui.calls.find((call) => call.path.endsWith('/chat')).body.recipient, 'research_1');
});

test('ended passk has no resume action', async (t) => {
  const record = run('a', 'stopped');
  record.started_at = '2026-09-22T00:00:00Z';
  const ui = await setup(t, {a: record});
  assert.equal(ui.$('resume').hidden, true);
});

for (const status of ['stopped', 'succeeded', 'completed', 'failed']) {
test(`interaction chat stays available after ${status} research`, async (t) => {
  const record = run('a', status);
  record.started_at = '2026-09-22T00:00:00Z';
  record.agents.push({agent_id: 'research_1', direction: '独立探索 1', state: 'stopped'});
  let sent;
  const ui = await setup(t, {a: record}, (path, options) => {
    if (path.endsWith('/chat')) {
      sent = JSON.parse(options.body);
      return response({message_id: sent.message_id});
    }
  });
  assert.equal(ui.$('chat-input').disabled, false);
  assert.equal(ui.$('send-message').disabled, false);
  assert.match(ui.$('chat-recipient-trigger').textContent, /Interaction Agent/);
  ui.input('chat-input', '继续讨论交付'); ui.submit('chat-form'); await settle();
  assert.equal(sent.recipient, 'interaction');
  assert.equal(ui.calls.filter((call) => call.path.endsWith('/resume')).length, 0);
});
}

test('a draft for a stopped researcher asks for an explicit new recipient', async (t) => {
  const record = run('a', 'running');
  record.started_at = '2026-09-22T00:00:00Z';
  record.agents.push({agent_id: 'research_1', direction: '独立探索 1', state: 'waiting'});
  const ui = await setup(t, {a: record});
  ui.input('chat-input', '保留这条指导');
  record.status = 'stopped';
  record.agents.at(-1).state = 'stopped';
  await ui.poll();
  assert.equal(ui.$('chat-input').value, '保留这条指导');
  assert.equal(ui.$('send-message').disabled, true);
  assert.match(ui.$('chat-status').textContent, /改选 Interaction Agent/);
  ui.$('chat-recipient-trigger').click();
  [...ui.$('chat-recipient-options').querySelectorAll('[role="option"]')]
    .find((option) => option.textContent.includes('Interaction Agent')
      && option.getAttribute('aria-disabled') !== 'true').click();
  assert.equal(ui.$('send-message').disabled, false);
  assert.equal(ui.$('chat-input').value, '保留这条指导');
});

test('the source repository and linked worktree are visible in the run', async (t) => {
  const record = run('a');
  record.workspace_dir = '/work/project';
  record.worktree_dirs = {research_1: '/work/data/worktrees/a/research_1'};
  const ui = await setup(t, {a: record});
  assert.equal(ui.$('workspace-target').textContent,
    '源 Git 仓库：/work/project\nresearch_1 工作区：/work/data/worktrees/a/research_1');
  assert.equal(ui.$('workspace-target').hidden, false);
});


test('single researcher shows confirmed intake acceptance before progress arrives', async (t) => {
  const record = run('a', 'running');
  record.collaboration.max_agents = 1;
  record.brief.acceptance = ['Empty list returns zero'];
  const ui = await setup(t, {a: record});
  assert.match(ui.$('goal-acceptance').textContent, /Empty list returns zero/);
});

test('Interaction conversation restores explanations, paginates and keeps final replies unique', async (t) => {
  const record = run('a');
  record.agents[0].state = 'running';
  record.agent_activity = {interaction: {type: 'tool_started', tool: 'functions.exec_command'}};
  record.messages = [{message_id: 'user', sender: 'user', recipient: 'interaction', text: '检查环境', created_at: '2026-09-22T10:00:00Z'},
    {message_id: 'reply', sender: 'interaction', recipient: 'user', text: '环境已就绪', created_at: '2026-09-22T10:00:03Z'}];
  const handler = (path) => {
    if (!path.includes('/trace?')) return;
    return response({agent_id: 'interaction', next_before: path.includes('before=') ? null : 8, entries: path.includes('before=')
      ? [{sequence: 3, kind: 'assistant_output', text: '先确认你的需求', created_at: '2026-09-22T09:59:00Z'}]
      : [{sequence: 8, kind: 'assistant_output', text: '正在检查 **Python 环境**\n<script>unsafe</script>', created_at: '2026-09-22T10:00:01Z'},
         {sequence: 9, kind: 'assistant_output', text: '环境已就绪', created_at: '2026-09-22T10:00:02Z'}]});
  };
  const ui = await setup(t, {a: record}, handler);
  assert.match(ui.$('messages').textContent, /正在检查 Python 环境/);
  assert.match(ui.$('messages').textContent, /过程说明/);
  assert.equal(ui.$('messages').querySelector('strong').textContent, 'Python 环境');
  assert.equal(ui.$('messages').querySelector('script'), null);
  assert.equal(ui.$('messages').textContent.split('环境已就绪').length, 2);
  assert.match(ui.$('chat-feedback').textContent, /执行命令/);
  assert.doesNotMatch(ui.$('chat-feedback').textContent, /exec_command/);
  ui.$('chat-history').querySelector('button').click(); await settle();
  assert.match(ui.$('messages').textContent, /先确认你的需求/);
  await ui.poll();
  assert.match(ui.$('messages').textContent, /先确认你的需求/);
  const reloaded = await setup(t, {a: record}, handler);
  assert.match(reloaded.$('messages').textContent, /正在检查 Python 环境/);
});

test('late conversation outputs stay in their own run and failed history can be retried', async (t) => {
  let finish;
  let failed = true;
  const ui = await setup(t, {a: run('a'), b: run('b')}, (path) => {
    if (!path.includes('/trace?')) return;
    if (path.includes('/a/')) return new Promise((resolve) => { finish = resolve; });
    if (failed) return response({error: '暂时无法读取'}, false);
    return response({agent_id: 'interaction', entries: [{sequence: 1, kind: 'assistant_output', text: '任务 B 的说明'}], next_before: null});
  });
  ui.switchRun('b'); await settle();
  finish(response({agent_id: 'interaction', entries: [{sequence: 1, kind: 'assistant_output', text: '任务 A 的说明'}], next_before: null})); await settle();
  assert.doesNotMatch(ui.$('messages').textContent, /任务 A 的说明/);
  assert.match(ui.$('chat-history').textContent, /暂时无法读取/);
  failed = false;
  ui.$('chat-history').querySelector('button').click(); await settle();
  assert.match(ui.$('messages').textContent, /任务 B 的说明/);
  assert.equal(ui.$('chat-history').hidden, true);
});

test('started research keeps Interaction explanations beside the selected researcher', async (t) => {
  const record = traceRun();
  const ui = await setup(t, {a: record}, (path) => {
    if (!path.includes('/trace?')) return;
    const id = new URL(path, 'http://localhost').searchParams.get('agent_id');
    return response({agent_id: id, entries: [{sequence: id === 'interaction' ? 1 : 2,
      kind: 'assistant_output', text: id === 'interaction' ? '已确认任务与验收条件' : '正在验证实现',
      created_at: record.started_at}], next_before: null});
  });
  assert.match(ui.$('messages').textContent, /已确认任务与验收条件/);
  assert.match(ui.$('messages').textContent, /正在验证实现/);
  record.agent_activity = {research_1: {type: 'tool_completed', active_tools: {
    slow: {name: 'functions.exec_command', started_at: record.started_at}}}};
  await ui.poll();
  assert.match(ui.$('chat-feedback').textContent, /执行命令/);
  assert.doesNotMatch(ui.$('chat-feedback').textContent, /操作已结束/);
});

test('initial detail failure exits the spinner and recovers on the next poll', async (t) => {
  let failed = true;
  const ui = await setup(t, {a: run('a')}, (path) => {
    if (path.startsWith('/api/runs/a?') && failed) return response({error: '工作台请求超时'}, false);
  });
  assert.equal(ui.$('run-loading').hidden, true);
  assert.equal(ui.$('workspace').hidden, true);
  assert.match(ui.$('notice').textContent, /研究记录加载失败.*工作台请求超时/);
  failed = false;
  await ui.poll();
  assert.equal(ui.$('workspace').hidden, false);
  assert.equal(ui.$('notice').hidden, true);
});
