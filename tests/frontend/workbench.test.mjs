import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {Window} from 'happy-dom';

const assets = new URL('../../src/areal_rsi/ui/workbench_static/', import.meta.url);
const source = (name) => readFileSync(new URL(name, assets), 'utf8');
const defaults = {base_url: 'https://4ipic.com', model: 'gpt-6-astra', reasoning_effort: 'xhigh', has_credentials: true};
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve)); };
const response = (value, ok = true) => ({ok, status: ok ? 200 : 503, json: async () => structuredClone(value)});
function run(id, status = 'planned') {
  return {
    run_id: id, objective: `研究 ${id}`, status, nodes: [], jobs: [], edges: [], messages: [],
    brief: {objective: id, limits: {}}, budget: {}, agents: [{agent_id: 'interaction', state: 'waiting'}],
    model_settings: {...defaults, editable: status === 'planned'}, pending_agents: [], worker_alive: true,
    events: [], event_cursor: 120, waiting_for_user: [],
  };
}
async function setup(t, records, handler) {
  const window = new Window({url: 'http://localhost:8198/', settings: {disableJavaScriptFileLoading: true, disableCSSFileLoading: true}});
  t.after(() => window.happyDOM.close());
  window.setInterval = () => 0;
  const calls = [];
  window.fetch = async (path, options = {}) => {
    calls.push({path, options, body: options.body ? JSON.parse(options.body) : null});
    const custom = handler?.(path, options, calls);
    if (custom) return custom;
    if (path === '/api/bootstrap') return response({csrf_token: 'test', default_model_settings: defaults, models: [{id: 'gpt-6-astra', efforts: ['high', 'xhigh']}, {id: 'gpt-5.6-sol', efforts: ['medium', 'high', 'xhigh']}]});
    if (path === '/api/runs') return response({runs: Object.values(records)});
    const id = path.split('/')[3]?.split('?')[0];
    assert.ok(records[id], `Unexpected API request ${path}`);
    return response(records[id]);
  };
  window.document.write(source('index.html'));
  window.HTMLElement.prototype.scrollTo = function ({left = 0, top = 0}) { this.scrollLeft = left; this.scrollTop = top; };
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.eval(source('dropdown.js') + '\n' + source('app.js'));
  await settle();
  const $ = (id) => window.document.getElementById(id);
  const switchRun = (id) => [...$('run-list').querySelectorAll('button')].find((button) => button.textContent.includes(`研究 ${id}`)).click();
  const input = (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event('input', {bubbles: true})); };
  const submit = (id) => $(id).dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true}));
  return {window, $, calls, switchRun, input, submit};
}

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
  record.nodes = [{iteration_id: 'node1', agent_id: 'researcher', state: 'completed', title: '分析方案', created_at: '2026-09-14T10:00:00Z', updated_at: '2026-09-14T10:50:00Z', assignment: {instruction: '研究一个方向'}, report: {created_at: '2026-09-14T10:10:00Z', message: '结论'}}];
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
  ui.submit('create-form'); await settle();
  assert.deepEqual(submitted.model_settings, {base_url: 'https://custom.example/v1', api_key: 'dummy-ui-key', model: 'custom-model', reasoning_effort: 'high'});
  assert.equal(ui.$('create-dialog').open, false);
  assert.equal(ui.$('create-api-key').value, '');
  assert.equal(ui.$('objective').textContent, '研究 new');
});
