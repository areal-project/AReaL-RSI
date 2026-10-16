// Apply the original DSH pi-tui patch after installing the pinned npm package.
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const directory = fileURLToPath(new URL('./node_modules/@earendil-works/pi-tui/', import.meta.url));
const patch = fileURLToPath(new URL('./upstream/pi-tui.patch', import.meta.url));
const marker = `${directory}/.rsi-upstream-patch`;
const hash = createHash('sha256').update(readFileSync(patch)).digest('hex');
if (!existsSync(marker) || readFileSync(marker, 'utf8') !== hash) {
  execFileSync('patch', ['--batch', '--forward', '-p1', '-i', patch], { cwd: directory, stdio: 'pipe' });
  writeFileSync(marker, hash);
}
