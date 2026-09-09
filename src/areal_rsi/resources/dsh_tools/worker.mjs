// Tool-only Cordis composition. Model turns and durable conversations stay in Python.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { mkdir, open, readFile, rename } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { createInterface } from 'node:readline';

const [installation, workspace, stateDirectory] = process.argv.slice(2);
const require = createRequire(join(resolve(installation), 'package.json'));
async function dependency(name) {
  return import(pathToFileURL(require.resolve(`@deepseek-ai/${name}`)).href);
}

const { Context } = await dependency('cordis');
const ctx = new Context();
async function plugin(name, config = {}) {
  const module = await dependency(name);
  await ctx.plugin(module.default ?? module, config);
}
await plugin('dsh-system-prompt');
await plugin('dsh-tools', { mode: 'native' });
await plugin('dsh-subprocess-local');
await plugin('dsh-fs-local', { cwd: workspace });
await plugin('dsh-bash-local', {
  cwd: workspace, timeoutMs: 30000, maxTimeoutMs: 60000,
  maxOutputBytes: 16000, maxSpillBytes: 1000000, graceMs: 500,
});
await plugin('dsh-shell-env', { dshHome: stateDirectory });
await plugin('dsh-tool-bash', { enableRunInBackground: false });
await plugin('dsh-tool-fs', { readLimit: 200, readMaxBytes: 24000 });
await plugin('dsh-fs-observation-policy');
await plugin('dsh-tool-fs-search', { sampleOverCapGlobResults: false });

// DSH uses the owner session's identity for file observations and relative paths.
// No DSH model/session loop is mounted or prompted by this bridge.
const agent = { session: { header: { id: stateDirectory, cwd: workspace } } };
const callsDirectory = join(stateDirectory, 'calls');
await mkdir(callsDirectory, { recursive: true, mode: 0o700 });
let active;
let closing = false;
let queue = Promise.resolve();

function send(packet) {
  process.stdout.write(JSON.stringify(packet) + '\n');
}

async function persist(path, record, exclusive = false) {
  const target = exclusive ? path : path + '.tmp';
  const file = await open(target, exclusive ? 'wx' : 'w', 0o600);
  try {
    await file.writeFile(JSON.stringify(record));
    await file.sync();
  } finally {
    await file.close();
  }
  if (!exclusive) await rename(target, path);
}

function failure(code, message) {
  return { isError: true, error: { code, message }, content: [{ type: 'text', text: message }] };
}

async function execute(request) {
  const key = createHash('sha256').update(request.id).digest('hex');
  const path = join(callsDirectory, key + '.json');
  const fingerprint = JSON.stringify([request.name, request.arguments]);
  let previous;
  try {
    previous = JSON.parse(await readFile(path, 'utf8'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  if (previous) {
    if (previous.fingerprint !== fingerprint) throw new Error('工具调用标识被用于不同参数');
    return previous.result ?? failure('OUTCOME_UNKNOWN',
      '上次调用已开始，但结果未完整保存。命令可能已产生作用；先检查实际状态，再决定是否发起新的调用。系统不会自动重放。');
  }
  if (closing) return failure('CANCELLED', '工具进程正在关闭，尚未执行。');
  // Commit before entering the effect. A lost response must never repeat a command.
  await persist(path, { fingerprint, status: 'started' }, true);
  active = new AbortController();
  // Shutdown may arrive while the durable start record is being flushed.
  if (closing) active.abort();
  const timeout = setTimeout(() => active?.abort(), 65000);
  let result;
  try {
    const output = await ctx.tools.execute({
      callId: request.id, name: request.name, arguments: request.arguments,
      agent, signal: active.signal,
    });
    // Keep DSH's model-facing rendering; filesystem values can contain whole before/after files.
    result = { isError: output.isError, content: output.content };
    if (output.error) result.error = output.error;
    if (request.name === 'bash' && output.value) result.value = output.value;
  } catch (error) {
    result = failure('TOOL_ERROR', String(error.message ?? error));
  } finally {
    clearTimeout(timeout);
    active = undefined;
  }
  await persist(path, { fingerprint, status: 'completed', result });
  return result;
}

async function close() {
  if (closing) return;
  closing = true;
  active?.abort();
  await queue;
  await ctx.fiber.dispose();
  process.exitCode = 0;
}

const input = createInterface({ input: process.stdin });
input.on('line', line => {
  let request;
  try { request = JSON.parse(line); } catch { return; }
  queue = queue.then(async () => {
    try {
      send({ id: request.id, result: await execute(request) });
    } catch (error) {
      send({ id: request.id, result: failure('BRIDGE_ERROR', String(error.message ?? error)) });
    }
  });
});
input.on('close', close);
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {
  input.close();
  void close();
});
send({ ready: true, schemas: ctx.tools.schemas() });
