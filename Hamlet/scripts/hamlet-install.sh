#!/usr/bin/env bash
# Run from the project where Codex should discover the Skill.
# Source is resolved relative to this script, not the working directory.
# New install: HAMLET_PORT=34883 bash ./Hamlet/scripts/hamlet-install.sh
# Update/restart with existing data and port:
# HAMLET_RESTART=1 HAMLET_PUBLIC_URL=https://your-host bash ./Hamlet/scripts/hamlet-install.sh
set -euo pipefail
project="$PWD"
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
npx --yes pnpm@10.28.2 install --frozen-lockfile
npx --yes pnpm@10.28.2 build
export HAMLET_HOME="${HAMLET_HOME:-$HOME/.hamlet}"
node --input-type=module <<'JS'
import { spawn } from "node:child_process";
import {
  mkdirSync,
  openSync,
  closeSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { Auth } from "./packages/core/dist/index.js";
const dir = process.env.HAMLET_HOME;
mkdirSync(dir, { recursive: true, mode: 0o700 });
const live = async () => {
  try {
    const i = JSON.parse(readFileSync(dir + "/daemon.json", "utf8"));
    const r = await fetch(i.url + "/health", {
      signal: AbortSignal.timeout(500),
    });
    const h = await r.json();
    return r.ok && h.pid === i.pid ? i : null;
  } catch {
    return null;
  }
};
let info = await live();
const port = Number(process.env.HAMLET_PORT || info?.port);
if (!Number.isInteger(port) || port < 1 || port > 65535)
  throw Error("新安装请通过 HAMLET_PORT 指定 1–65535 的端口");
if (info && process.env.HAMLET_RESTART === "1") {
  const response = await fetch(info.url + "/api/shutdown", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + new Auth(dir).ownerToken,
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  if (!response.ok) throw Error("旧服务停止失败，未强制终止");
  for (let n = 0; n < 100; n++) {
    if (!existsSync(dir + "/daemon.lock") && !(await live())) break;
    if (n === 99) throw Error("旧服务尚未退出，未启动新服务");
    await new Promise((r) => setTimeout(r, 100));
  }
  info = null;
}
if (info) {
  if (info.port !== port)
    throw Error(`现有服务位于 ${info.url}，请先明确是否停止它再更换端口`);
} else {
  const fd = openSync(dir + "/daemon.log", "a", 0o600);
  const child = spawn(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      'import {runDaemon} from "./packages/daemon/dist/index.js";await runDaemon({port:Number(process.env.HAMLET_PORT)});',
    ],
    {
      cwd: process.cwd(),
      env: { ...process.env, HAMLET_PORT: String(port) },
      detached: true,
      stdio: ["ignore", fd, fd],
    },
  );
  closeSync(fd);
  child.unref();
  let error;
  child.on("error", (e) => {
    error = e;
  });
  for (let n = 0; n < 100; n++) {
    if (error) throw error;
    info = await live();
    if (info?.pid === child.pid && info.port === port) break;
    info = null;
    if (child.exitCode !== null) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!info) throw Error(`启动失败，请查看 ${dir}/daemon.log`);
}
if (process.env.HAMLET_PUBLIC_URL) {
  const response = await fetch(info.url + "/api/tools/ui_open", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + new Auth(dir).ownerToken,
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  if (!response.ok) throw Error("无法验证 Web 登录入口");
  const login = await response.json();
  if (
    new URL(login.url).origin !== new URL(process.env.HAMLET_PUBLIC_URL).origin
  )
    throw Error(
      "运行服务的公开地址不匹配；请使用新版源码并设置 HAMLET_RESTART=1",
    );
}
console.log("服务地址：" + info.url);
JS
if [ ! -e "$project/.agents/skills/hamlet" ]; then
  node bin/hamlet.js skill install --path "$project/.agents/skills/hamlet"
else
  echo "Skill 已存在，保留现有文件：$project/.agents/skills/hamlet"
fi
node bin/hamlet.js ui
