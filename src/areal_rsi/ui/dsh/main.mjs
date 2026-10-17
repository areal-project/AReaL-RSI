// RSI event/command adapter. Rendering, editing and dialogs are upstream DSH/pi-tui.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import {
  TUI, ProcessTerminal, Container, Text, Spacer, Editor,
  CombinedAutocompleteProvider, Key, matchesKey,
} from '@earendil-works/pi-tui';
import {
  createPalette, markdownTheme, selectTheme, displayText,
  UserMessageComponent, StreamingAssistantComponent, ToolCardComponent,
  QuestionDialog, StepTimingTracker,
} from './vendor.mjs';

export function createView(terminal, send, quit) {
  const ui = new TUI(terminal, true);
  const palette = createPalette(true);
  const markdown = markdownTheme(palette);
  const conversation = new Container();
  const status = new Text('准备会话…', 1, 0);
  const editor = new Editor(ui, { borderColor: palette.dim, selectList: selectTheme(palette) }, {
    paddingX: 1, frame: 'none', prompt: { first: '❯ ', continuation: '  ' },
  });
  const items = new Map();
  const tools = new Map();
  const toolOutput = new Map();
  const tracker = new StepTimingTracker();
  let visibility = 'collapsed';
  let record;
  let overlay;
  let activePrompt;
  let serial = 0;
  const commandNames = ['rsi', 'start', 'chat', 'pause', 'resume', 'stop', 'status', 'budget', 'model', 'effort', 'learn',
    'learn-mode', 'docs', 'image', 'sessions', 'open', 'new', 'help', 'quit'];
  editor.setAutocompleteProvider(new CombinedAutocompleteProvider(
    commandNames.map(name => ({ name, description: `/${name}` })), process.cwd(),
  ));
  ui.addChild(new Text(palette.bold('AReaL RSI') + palette.dim(' · DSH terminal / Codex'), 1, 1));
  ui.addChild(conversation);
  ui.addChild(new Spacer(1));
  ui.addChild(status);
  ui.addChild(editor);
  ui.addChild(new Text(palette.dim('Enter 发送 · Shift+Enter 换行 · Esc 暂停 · Ctrl+O 工具 · Ctrl+Q 退出'), 1, 0));
  ui.setFocus(editor);

  const notice = text => {
    conversation.addChild(new Text(palette.dim(displayText(text)), 1, 1));
    ui.requestRender();
  };
  const submit = text => {
    if (!text.trim()) return;
    editor.addToHistory(text);
    editor.setText('');
    send({ action: 'submit', text });
  };
  editor.onSubmit = submit;
  ui.addInputListener(data => {
    if (matchesKey(data, Key.ctrl('q'))) { quit(); return { consume: true }; }
    if (matchesKey(data, Key.ctrl('c'))) {
      if (record?.active_turn_id) send({ action: 'pause' }); else quit();
      return { consume: true };
    }
    if (overlay) return;
    if (matchesKey(data, Key.escape)) { send({ action: 'pause' }); return { consume: true }; }
    if (matchesKey(data, Key.ctrl('o'))) {
      visibility = { collapsed: 'expanded', expanded: 'hidden', hidden: 'collapsed' }[visibility];
      for (const tool of tools.values()) tool.setVisibility(visibility);
      ui.requestRender();
      return { consume: true };
    }
  });

  const showPrompt = prompt => {
    overlay?.hide();
    activePrompt = prompt.request_id;
    const questions = prompt.kind === 'approval'
      ? [{ question_id: 'approval', text: prompt.text, options: ['拒绝', '批准'] }]
      : prompt.questions;
    const answers = {};
    const show = index => {
      const question = questions[index];
      const close = () => { overlay?.hide(); overlay = undefined; activePrompt = undefined; ui.setFocus(editor); };
      const dialog = new QuestionDialog(
        { question: question.text, options: question.options.map(label => ({ label })) },
        index + 1, questions.length, questions.length - index, 8, () => terminal.rows - 4,
        palette, selection => {
          const answer = selection.custom || selection.selected.join(', ');
          answers[question.question_id] = answer;
          overlay?.hide();
          if (index + 1 < questions.length) { show(index + 1); return; }
          close();
          send(prompt.kind === 'approval'
            ? { action: 'answer', request_id: prompt.request_id, approved: answer === '批准' }
            : { action: 'answer', request_id: prompt.request_id, answers });
        }, () => { close(); send({ action: 'pause' }); },
      );
      overlay = ui.showOverlay(dialog, { width: '90%', maxHeight: '90%', anchor: 'center' });
      ui.requestRender();
    };
    show(0);
  };

  const event = value => {
    if (value.kind === 'prompt') { showPrompt(value.prompt); return; }
    if (value.kind === 'error') { notice(value.text); return; }
    if (value.kind === 'status') return;
    const id = value.item_id || `local-${serial++}`;
    if (value.role === 'user') {
      conversation.addChild(new UserMessageComponent(value.text, palette, markdown));
    } else if (value.kind === 'tool' || value.role === 'tool') {
      let card = tools.get(id);
      if (!card) {
        card = new ToolCardComponent('Codex', { value: value.text, valid: true }, undefined, 8, 1000, palette, markdown);
        card.setVisibility(visibility);
        tools.set(id, card);
        conversation.addChild(card);
      }
      if (value.completed || value.kind === 'delta') {
        const text = value.kind === 'delta' ? (toolOutput.get(id) || '') + value.text : value.text;
        toolOutput.set(id, text);
        card.updateResult({ message: { content: [{ type: 'tool-result', content: [{ type: 'text', text }],
          isError: /^exit=[1-9]/m.test(value.text) }] } });
      }
    } else {
      let message = items.get(id);
      if (!message) {
        message = new StreamingAssistantComponent({ turn: serial++, step: 0 }, () => [], tracker, Date.now, false, palette, markdown);
        message.update({ type: 'block-start', index: 0, blockType: 'text' });
        items.set(id, message);
        conversation.addChild(message);
      }
      if (value.kind === 'delta') message.update({ type: 'text-delta', index: 0, text: value.text });
      else message.settle([{ type: 'text', text: value.text }]);
    }
    ui.requestRender();
  };
  const receive = packet => {
    if (packet.type === 'ready') {
      conversation.clear(); items.clear(); tools.clear(); toolOutput.clear();
      overlay?.hide(); overlay = undefined; activePrompt = undefined;
      editor.setAutocompleteProvider(new CombinedAutocompleteProvider(
        commandNames.map(name => ({ name, description: `/${name}` })), packet.record.config.workspace,
      ));
      notice(packet.help);
    }
    if (packet.record) {
      record = packet.record;
      status.setText(displayText(`${record.run_id} · ${record.phase} · ${record.status} · ${record.config.model}`
        + ` · ${record.config.effort}`
        + (record.goal ? `\n${record.goal.objective} · ${record.tokens_used}/${record.goal.limits.max_total_tokens ?? '不限'} tokens`
          + ` · ${record.seconds_used.toFixed(0)}/${record.goal.limits.max_wall_seconds ?? '不限'}s` : '')
        + (record.status === 'budget_limited' ? '\n预算已耗尽；/budget 查看，/budget off 取消限制后 /resume 继续。' : '')
        + (record.learning.status === 'running' ? '\n正在总结经验…' : '')
        + (record.learning.status === 'awaiting_feedback' ? '\n等待经验复盘：/learn 你的意见；直接 /learn 可跳过补充。' : '')
        + (record.progress.question ? `\n${record.progress.question}` : '')));
      if (activePrompt !== undefined && !record.active_turn_id) {
        overlay?.hide(); overlay = undefined; activePrompt = undefined; ui.setFocus(editor);
      }
    }
    if (packet.type === 'event') event(packet.event);
    if (packet.type === 'notice') notice(packet.text);
    ui.requestRender();
  };
  return { ui, editor, conversation, receive, submit, notice };
}

async function main() {
  if (!process.stdin.isTTY || !process.stdout.isTTY) throw new Error('TUI requires a terminal; use areal-rsi run for pipes.');
  const backend = spawn(process.env.RSI_PYTHON, ['-m', 'areal_rsi', '_ui', ...process.argv.slice(2)], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let closing = false;
  const send = request => { if (!closing) backend.stdin.write(JSON.stringify(request) + '\n'); };
  const quit = () => { if (!closing) { send({ action: 'close' }); closing = true; backend.stdin.end(); } };
  const view = createView(new ProcessTerminal(), send, quit);
  view.ui.start();
  createInterface({ input: backend.stdout }).on('line', line => {
    try { view.receive(JSON.parse(line)); } catch (error) { view.notice(String(error)); }
  });
  backend.stderr.on('data', data => view.notice(data.toString()));
  backend.on('error', error => { view.ui.stop(); console.error(error.message); process.exitCode = 1; });
  backend.on('exit', code => { view.ui.stop(); process.exitCode = code ?? 1; });
  process.on('SIGTERM', quit);
  process.on('SIGINT', quit);
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
