import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import type { StartOptions, ResumeOptions } from "../../core/src/types.js";
import { childEnvironment } from "../../core/src/runtime.js";

const identityKeys = [
  "HAMLET_URL",
  "HAMLET_TOKEN",
  "HAMLET_INSTANCE_ID",
  "DETACHED_URL",
  "DETACHED_TOKEN",
  "DETACHED_INSTANCE_ID",
  "HAMLET_NODE",
  "HAMLET_CLI",
  "HAMLET_SKILL_DIR",
  "HAMLET_RUN_ID",
  "DETACHED_RUN_ID",
];

// A separate Node process keeps the Harness start/resume contract synchronous
// while speaking app-server's asynchronous JSON-RPC protocol. Only a success
// marker leaves it: neither Codex diagnostics nor command output are forwarded.
const probe = String.raw`
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const { command, configArgs, cwd, keys, expected } = JSON.parse(input);
const child = spawn(command, ["app-server", "--stdio", ...configArgs], {
  cwd, env: process.env, stdio: ["pipe", "pipe", "ignore"], detached: true,
});
let finished = false;
const stop = (signal) => {
  try { process.kill(-child.pid, signal); } catch { child.kill(signal); }
};
const finish = (ok) => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  stop("SIGTERM");
  const force = setTimeout(() => { stop("SIGKILL"); process.exit(1); }, 500);
  child.once("close", () => {
    clearTimeout(force);
    if (ok) process.stdout.write("hamlet-environment-ok");
    process.exit(ok ? 0 : 1);
  });
  if (child.exitCode !== null || child.signalCode !== null) {
    clearTimeout(force); process.exit(1);
  }
};
// Shared storage can make app-server/config startup exceed seven seconds.
// Keep the same identity/hash checks while allowing bounded startup latency.
const timer = setTimeout(() => finish(false), 30000);
process.on("SIGTERM", () => finish(false));
child.on("error", () => finish(false));
child.on("exit", () => { if (!finished) finish(false); });
child.stdin.on("error", () => finish(false));
const send = (value) => child.stdin.write(JSON.stringify(value) + "\n");
const lines = createInterface({ input: child.stdout });
lines.on("line", (line) => {
  if (finished) return;
  try {
    const value = JSON.parse(line);
    if (value.id === 1) {
      if (value.error) return finish(false);
      send({ method: "initialized" });
      // Hash only the runtime identity/path variables, never the whole shell
      // environment. Values stay in the inherited environment, not argv/RPC.
      const script = 'const {createHash}=require("node:crypto");' +
        'const keys=' + JSON.stringify(keys) + ';' +
        'process.stdout.write(createHash("sha256").update(JSON.stringify(' +
        'keys.map(k=>[k,process.env[k]??null]))).digest("hex"));';
      send({ id: 2, method: "command/exec", params: {
        command: [process.execPath, "-e", script], cwd,
        sandboxPolicy: { type: "dangerFullAccess" }, timeoutMs: 10000,
      } });
    } else if (value.id === 2) {
      finish(!value.error && value.result?.exitCode === 0 &&
        value.result?.stdout === expected);
    }
  } catch { finish(false); }
});
send({ id: 1, method: "initialize", params: {
  clientInfo: { name: "hamlet-environment-check", version: "1" },
  capabilities: { experimentalApi: true },
} });
`;

export function assertCodexShellEnvironment(
  command: string,
  args: string[],
  o: StartOptions | ResumeOptions,
) {
  // Required identity/path values must already exist before asking Codex to
  // filter them. CLI discovery cannot recognize a completely erased identity.
  const required = identityKeys.slice(0, 9);
  const env = { ...childEnvironment(), ...o.env };
  if (required.some((key) => !o.env[key]))
    throw new Error("Codex 实例身份或 CLI 路径不完整；未启动子 Agent");
  const expected = createHash("sha256")
    .update(JSON.stringify(identityKeys.map((key) => [key, env[key] ?? null])))
    .digest("hex");
  const configArgs = args.flatMap((arg, i) =>
    arg === "-c" ? [arg, args[i + 1]] : [],
  );
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", probe],
    {
      cwd: o.cwd,
      env,
      input: JSON.stringify({
        command,
        configArgs,
        cwd: o.cwd,
        keys: identityKeys,
        expected,
      }),
      encoding: "utf8",
      timeout: 35000,
      maxBuffer: 64 * 1024,
    },
  );
  if (result.status !== 0 || result.stdout !== "hamlet-environment-ok")
    throw new Error(
      "Codex 命令环境未保留受限实例身份或无法验证；未启动子 Agent。请检查 shell_environment_policy；不会回退 Owner 或放宽环境过滤",
    );
}
