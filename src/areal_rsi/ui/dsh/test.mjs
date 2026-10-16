import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import xterm from '@xterm/headless';
import { createView } from './main.mjs';

class Terminal {
  constructor() { this.columns = 100; this.rows = 36; this.kittyProtocolActive = false;
    this.xterm = new xterm.Terminal({ cols: 100, rows: 36, allowProposedApi: true }); }
  start(input, resize) { this.input = input; this.resize = resize; }
  stop() { this.stopped = true; }
  async drainInput() {}
  write(data) { this.xterm.write(data); }
  moveBy(lines) { this.write(lines > 0 ? `\x1b[${lines}B` : `\x1b[${-lines}A`); }
  hideCursor() { this.write('\x1b[?25l'); }
  showCursor() { this.write('\x1b[?25h'); }
  clearLine() { this.write('\x1b[2K'); }
  clearFromCursor() { this.write('\x1b[0J'); }
  clearScreen() { this.write('\x1b[2J\x1b[H'); }
  setTitle() {}
  setProgress() {}
  async screen() {
    await new Promise(resolve => setTimeout(resolve, 50));
    await new Promise(resolve => this.xterm.write('', resolve));
    const buffer = this.xterm.buffer.active;
    return Array.from({ length: buffer.length }, (_, row) => buffer.getLine(row)?.translateToString(true)).join('\n');
  }
}

const record = () => ({ run_id: 'run-test', phase: 'chat', status: 'running',
  active_turn_id: 'turn-test', config: { model: 'test-model', effort: 'high', workspace: process.cwd() }, goal: null,
  learning: { status: 'idle' }, progress: { question: '' } });

test('upstream views are unchanged and no DSH execution runtime is bundled', () => {
  const provenance = JSON.parse(readFileSync(new URL('./upstream/provenance.json', import.meta.url)));
  for (const file of [...provenance.files, provenance.dependency_patch]) {
    const content = readFileSync(new URL(`./upstream/${file.path}`, import.meta.url));
    assert.equal(createHash('sha256').update(content).digest('hex'), file.sha256);
  }
  const bundle = readFileSync(new URL('./vendor.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(bundle, /from ["'](?:cordis|@deepseek-ai\/)/);
});

test('actual DSH components render streams, expand tools, edit and submit', async () => {
  const terminal = new Terminal();
  const sent = [];
  const view = createView(terminal, request => sent.push(request), () => {});
  view.ui.start();
  try {
    view.receive({ type: 'ready', record: record(), help: 'Context survives /rsi' });
    view.receive({ type: 'event', event: { kind: 'message', role: 'user', text: 'BUSINESS_MARKER_51' } });
    view.receive({ type: 'event', event: { kind: 'delta', role: 'chat', item_id: 'message', text: 'Streaming ' } });
    view.receive({ type: 'event', event: { kind: 'delta', role: 'chat', item_id: 'message', text: 'reply' } });
    assert.match(await terminal.screen(), /Streaming reply/);
    view.receive({ type: 'event', event: { kind: 'message', role: 'chat', item_id: 'message', text: 'Final reply' } });
    view.receive({ type: 'event', event: { kind: 'tool', role: 'leader', item_id: 'cmd', text: 'python check.py' } });
    view.receive({ type: 'event', event: { kind: 'tool', role: 'leader', item_id: 'cmd', text: 'Checks passed', completed: true } });
    terminal.input('\x0f');
    assert.match(await terminal.screen(), /Checks passed/);
    view.editor.setText('/rsi Use the preceding context');
    terminal.input('\r');
    assert.deepEqual(sent.at(-1), { action: 'submit', text: '/rsi Use the preceding context' });
    assert.equal(view.editor.getText(), '');
    terminal.input('\x1b');
    assert.deepEqual(sent.at(-1), { action: 'pause' });
  } finally { view.ui.stop(); terminal.xterm.dispose(); }
});

test('upstream question dialog routes explicit approval and free text answers', async () => {
  const terminal = new Terminal();
  const sent = [];
  const view = createView(terminal, request => sent.push(request), () => {});
  view.ui.start();
  try {
    view.receive({ type: 'ready', record: record(), help: 'Questions' });
    view.receive({ type: 'event', event: { kind: 'prompt', prompt: {
      request_id: 1, kind: 'approval', text: 'Allow this operation?', questions: [],
    } } });
    assert.match(await terminal.screen(), /Allow this operation/);
    terminal.input('\x1b[B'); terminal.input('\r');
    assert.deepEqual(sent.at(-1), { action: 'answer', request_id: 1, approved: true });
    view.receive({ type: 'event', event: { kind: 'prompt', prompt: {
      request_id: 2, kind: 'question', questions: [{ question_id: 'color', text: 'Which color?', options: [] }],
    } } });
    terminal.input('blue'); terminal.input('\r');
    assert.deepEqual(sent.at(-1), { action: 'answer', request_id: 2, answers: { color: 'blue' } });
  } finally { view.ui.stop(); terminal.xterm.dispose(); }
});
