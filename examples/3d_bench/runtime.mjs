import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const dataset = resolve(root, '../../.rsi/datasets/3d-model-v2');
const template = join(dataset, 'viewer-template');
const commit = 'd7a9504cc6bb5b1918a084ef62225688a67c3aa6';
const expectedDigest = 'b799e9f0f3e6c850fa4cb7552f2f659054866893a828d867dd007f376b2c416f';
const sha256 = (value) => createHash('sha256').update(value).digest('hex');
let manifest;

function initialize(checkout) {
  if (existsSync(dataset)) throw new Error(`Dataset already exists: ${dataset}`);
  const archive = spawnSync('git', ['-C', resolve(checkout), 'archive', commit,
    'gdd-model-runner/viewer-template'], { maxBuffer: 4 * 1024 * 1024, timeout: 30_000 });
  if (archive.error || archive.status !== 0) throw new Error('Cannot export pinned GameAgent commit.');
  mkdirSync(template, { recursive: true });
  const extracted = spawnSync('tar', ['-xf', '-', '--strip-components=2', '-C', template], {
    input: archive.stdout, encoding: 'utf8', timeout: 30_000,
  });
  if (extracted.error || extracted.status !== 0) throw new Error(`Cannot extract runtime: ${extracted.stderr}`);
  const files = {};
  function walk(relative = '') {
    for (const name of readdirSync(join(template, relative)).sort()) {
      const child = relative ? `${relative}/${name}` : name;
      const path = join(template, child);
      const stat = lstatSync(path);
      if (stat.isDirectory()) walk(child);
      else if (stat.isFile()) files[child] = sha256(readFileSync(path));
      else throw new Error(`Unexpected upstream file: ${child}`);
    }
  }
  walk();
  if (sha256(JSON.stringify(files)) !== expectedDigest) throw new Error('Upstream snapshot digest mismatch.');
  writeFileSync(join(dataset, 'snapshot.json'), JSON.stringify({
    source: 'https://code.alipay.com/AReaL/GameAgent', commit,
    subdirectory: 'gdd-model-runner/viewer-template', files,
  }, null, 2) + '\n');
  console.log(`Initialized pinned runtime: ${dataset}`);
}

function verify(directory, candidate = false) {
  for (const [name, expected] of Object.entries(manifest.files)) {
    if (candidate && name === 'src/model.js') continue;
    const path = join(directory, name);
    // Fixed runtime inputs must not be redirected through candidate symlinks.
    let part = directory;
    for (const segment of name.split('/')) {
      part = join(part, segment);
      if (lstatSync(part).isSymbolicLink()) throw new Error(`Symlink in runtime: ${part}`);
    }
    if (sha256(readFileSync(path)) !== expected) throw new Error(`Runtime snapshot changed: ${path}`);
  }
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd, stdio: 'inherit', timeout: 300_000,
    env: { ...process.env, GAMEAGENT_GDD_STYLE_ID: 'piggy-island' },
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed (${result.status})`);
}

try {
  const [command, workspaceArg, outputArg, ...extra] = process.argv.slice(2);
  if (command === 'init' && workspaceArg && !outputArg && !extra.length) {
    initialize(workspaceArg);
    process.exit(0);
  }
  if (!['prepare', 'selftest', 'validate', 'capture'].includes(command)
      || !workspaceArg || extra.length || (outputArg && !['validate', 'capture'].includes(command))) {
    throw new Error('Usage: bash run.sh init GAMEAGENT_CHECKOUT; prepare|selftest WORKSPACE; validate|capture WORKSPACE NEW_OUTPUT_DIR');
  }
  if (!existsSync(join(dataset, 'snapshot.json'))) throw new Error('Run init GAMEAGENT_CHECKOUT first.');
  manifest = JSON.parse(readFileSync(join(dataset, 'snapshot.json'), 'utf8'));
  if (manifest.commit !== commit || sha256(JSON.stringify(manifest.files)) !== expectedDigest) {
    throw new Error('Runtime manifest does not match the pinned snapshot.');
  }
  verify(template);
  const workspace = resolve(workspaceArg);
  if (workspace === root || workspace.startsWith(root + sep) || workspace === dataset) {
    throw new Error('Workspace must not be the example directory or dataset root.');
  }
  if (command === 'prepare') {
    mkdirSync(workspace, { recursive: false });
    cpSync(template, workspace, { recursive: true, force: false, errorOnExist: true });
    run('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], workspace);
    console.log(`Prepared ${workspace}. Only edit src/model.js. This is not a security sandbox.`);
  } else {
    if (lstatSync(workspace).isSymbolicLink()) throw new Error('Workspace may not be a symlink.');
    verify(workspace, true);
    if (!existsSync(join(workspace, 'node_modules/three'))) throw new Error('Run prepare first.');
    if (command === 'selftest') {
      run('npm', ['run', 'check:validator-fixtures'], workspace);
      run('npm', ['run', 'build'], workspace);
    } else {
      if (!outputArg) throw new Error('An explicit NEW_OUTPUT_DIR is required.');
      const output = resolve(outputArg);
      if (output === workspace || output.startsWith(workspace + sep)
          || output === root || output.startsWith(root + sep)) {
        throw new Error('Output must be outside the workspace and example directory.');
      }
      mkdirSync(output, { recursive: false });
      const record = {
        stage: 'contract-v2-runtime', command, upstream: manifest.commit,
        snapshotSha256: sha256(JSON.stringify(manifest.files)),
        sourceSha256: sha256(readFileSync(join(workspace, 'src/model.js'))),
        node: process.version, platform: process.platform, arch: process.arch,
        three: JSON.parse(readFileSync(join(workspace, 'node_modules/three/package.json'))).version,
        style: 'piggy-island', startedAt: new Date().toISOString(), status: 'running',
      };
      try {
        if (command === 'validate') {
          run('npm', ['run', 'validate:model', '--', join(output, 'model-validation.json')], workspace);
        } else {
          run('npm', ['run', 'capture:model', '--', output], workspace);
        }
        record.status = 'succeeded';
      } catch (error) {
        record.status = 'failed';
        record.error = error.message;
        throw error;
      } finally {
        record.finishedAt = new Date().toISOString();
        writeFileSync(join(output, 'run.json'), JSON.stringify(record, null, 2) + '\n');
      }
    }
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
