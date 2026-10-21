import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import {
  OWNER,
  childEnvironment,
  instanceEnvironment,
} from "../packages/core/src/index.js";
import {
  CodexHarness,
  codexArgs,
} from "../packages/harness-codex/src/index.js";
import { assertCodexShellEnvironment } from "../packages/harness-codex/src/environment.js";
import { Controlled } from "./helpers.js";
const command = process.env.HAMLET_CODEX_SHELL_TEST_BIN;
const configArgs = (args: string[]) =>
  args.flatMap((arg, i) => (arg === "-c" ? [arg, args[i + 1]] : []));

// Real command/exec uses Codex's shell environment builder without a model,
// session or MCP server. Never forward protocol diagnostics or secret env.
async function actualCommands(
  args: string[],
  env: NodeJS.ProcessEnv,
  cwd: string,
  commands: string[][],
) {
  const child = spawn(
    command!,
    ["app-server", "--stdio", ...configArgs(args)],
    {
      cwd,
      env,
      stdio: ["pipe", "pipe", "ignore"],
      detached: true,
    },
  );
  const pending = new Map<
    number,
    { resolve: (r: any) => void; reject: (e: Error) => void }
  >();
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    const value = JSON.parse(line),
      entry = pending.get(value.id);
    if (entry) {
      pending.delete(value.id);
      if (value.error) entry.reject(new Error("Codex RPC failed"));
      else entry.resolve(value.result);
    }
  });
  const fail = () => {
    for (const p of pending.values())
      p.reject(new Error("Codex app-server exited"));
    pending.clear();
  };
  child.on("error", fail);
  child.on("exit", fail);
  let id = 0;
  const request = (method: string, params: any) =>
    new Promise<any>((resolve, reject) => {
      pending.set(++id, { resolve, reject });
      child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    });
  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      (async () => {
        await request("initialize", {
          clientInfo: { name: "hamlet-shell-test", version: "1" },
          capabilities: { experimentalApi: true },
        });
        child.stdin.write(JSON.stringify({ method: "initialized" }) + "\n");
        const results = [];
        for (const command of commands)
          results.push(
            await request("command/exec", {
              command,
              cwd,
              sandboxPolicy: { type: "dangerFullAccess" },
              timeoutMs: 5000,
            }),
          );
        return results;
      })(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error("Codex command test timed out")),
          15000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
    lines.close();
    const closed = new Promise<void>((r) => child.once("close", () => r()));
    try {
      process.kill(-child.pid!, "SIGKILL");
    } catch {
      child.kill("SIGKILL");
    }
    await closed;
  }
}

const policies = [
  { name: "all", toml: 'inherit="all"', value: { inherit: "all" }, safe: true },
  {
    name: "core",
    toml: 'inherit="core"',
    value: { inherit: "core" },
    safe: false,
  },
  {
    name: "none",
    toml: 'inherit="none"',
    value: { inherit: "none" },
    safe: false,
  },
  {
    name: "exclude-all-identity",
    toml: 'exclude=["HAMLET_*","DETACHED_*"]',
    value: { exclude: ["HAMLET_*", "DETACHED_*"] },
    safe: false,
  },
  {
    name: "include-only-core",
    toml: 'include_only=["HOME","PATH"]',
    value: { include_only: ["HOME", "PATH"] },
    safe: false,
  },
  {
    name: "automatic-secret-filter",
    toml: "ignore_default_excludes=false",
    value: { ignore_default_excludes: false },
    safe: false,
  },
  {
    name: "replace-token",
    toml: 'set={HAMLET_TOKEN="wrong-token"}',
    value: { set: { HAMLET_TOKEN: "wrong-token" } },
    safe: false,
  },
  {
    name: "keyed-filter-core-only",
    toml: 'filters={HOME="include",PATH="include"}',
    value: { filters: { HOME: "include", PATH: "include" } },
    safe: false,
  },
  {
    name: "keyed-filter-safe",
    toml: 'inherit="all"\nfilters={"HOME"="include","PATH"="include","HAMLET_*"="include","DETACHED_*"="include","UNRELATED_*"="exclude"}',
    value: {
      inherit: "all",
      filters: {
        HOME: "include",
        PATH: "include",
        "HAMLET_*": "include",
        "DETACHED_*": "include",
        "UNRELATED_*": "exclude",
      },
    },
    safe: true,
  },
  // Preserve a restrictive user allowlist and exclusion of unrelated secrets.
  {
    name: "narrow-safe",
    toml: 'inherit="all"\nexclude=["UNRELATED_*"]\ninclude_only=["HOME","PATH","HAMLET_*","DETACHED_*"]',
    value: {
      inherit: "all",
      exclude: ["UNRELATED_*"],
      include_only: ["HOME", "PATH", "HAMLET_*", "DETACHED_*"],
    },
    safe: true,
  },
];

test(
  "真实 Codex shell：fork/resume 有效身份或启动前拒绝，不放宽用户环境策略",
  {
    skip:
      !command &&
      "set HAMLET_CODEX_SHELL_TEST_BIN=codex for real command-environment validation",
  },
  async (t) => {
    const home = mkdtempSync(join(tmpdir(), "hamlet-codex-shell-"));
    const d = await createDaemon({
      dir: join(home, ".hamlet"),
      harnesses: { codex: new Controlled() },
    });
    try {
      await d.listen();
      const s = d.service,
        group = await s.call("group_create", { name: "shell-test" }, OWNER);
      const a = await s.call(
        "agent_create",
        {
          group: group.id,
          role_name: "worker",
          identity: "worker",
          harness: "codex",
          workdir: join(home, "work"),
          worktree: false,
        },
        OWNER,
      );
      const instanceID = `${a.id}-1`;
      s.store.insert("instances", {
        id: instanceID,
        logical_id: a.id,
        seq: 1,
        status: "idle",
        config_snapshot: { ...a, workdir: a.workdir },
        created_at: new Date().toISOString(),
      });
      const token = s.auth.sign({ kind: "instance", id: instanceID });
      for (const mode of ["fork", "resume"]) {
        for (const layer of [
          "user",
          "project",
          "options-table",
          "options-dotted",
        ]) {
          for (const policy of policies) {
            await t.test(`${mode}/${layer}/${policy.name}`, async () => {
              const root = join(home, `${mode}-${layer}-${policy.name}`),
                cwd = join(root, "project"),
                codexHome = join(root, "codex");
              mkdirSync(join(cwd, ".codex"), { recursive: true });
              mkdirSync(codexHome);
              const userFile = join(codexHome, "config.toml"),
                projectFile = join(cwd, ".codex/config.toml");
              const text = `[shell_environment_policy]\n${policy.toml}\n`;
              writeFileSync(
                userFile,
                `[projects.${JSON.stringify(cwd)}]\ntrust_level="trusted"\n` +
                  (layer === "user" ? text : ""),
              );
              writeFileSync(projectFile, layer === "project" ? text : "");
              const before = [userFile, projectFile].map((p) =>
                readFileSync(p, "utf8"),
              );
              const options =
                layer === "options-table"
                  ? { shell_environment_policy: policy.value }
                  : layer === "options-dotted"
                    ? Object.fromEntries(
                        Object.entries(policy.value).map(([k, v]) => [
                          `shell_environment_policy.${k}`,
                          v,
                        ]),
                      )
                    : {};
              const env = {
                ...childEnvironment(),
                HOME: home,
                CODEX_HOME: codexHome,
                ...instanceEnvironment(s.url, token, instanceID),
                HAMLET_NODE: process.execPath,
                HAMLET_CLI: resolve("bin/hamlet.js"),
                HAMLET_SKILL_DIR: resolve("skills/hamlet"),
                HAMLET_RUN_ID: "test-run",
                DETACHED_RUN_ID: "test-run",
                UNRELATED_SECRET: "must-stay-filtered",
              };
              const o = {
                cwd,
                env,
                options,
                prompt: "unused: no model",
                systemPrompt: "unused",
                ...(mode === "resume" ? { sessionId: "unused-session" } : {}),
              };
              const args = codexArgs(o);
              assert.ok(!JSON.stringify(args).includes(token));
              if (policy.safe) {
                assertCodexShellEnvironment(command!, args, o);
                const [me, owner, presence] = await actualCommands(
                  args,
                  env,
                  cwd,
                  [
                    [process.execPath, env.HAMLET_CLI, "call", "whoami", "{}"],
                    [
                      process.execPath,
                      env.HAMLET_CLI,
                      "call",
                      "group_create",
                      '{"name":"must-deny"}',
                    ],
                    [
                      process.execPath,
                      "-e",
                      "process.stdout.write(JSON.stringify({unrelated:!!process.env.UNRELATED_SECRET}))",
                    ],
                  ],
                );
                assert.equal(me.exitCode, 0);
                assert.equal(JSON.parse(me.stdout).id, instanceID);
                assert.equal(JSON.parse(me.stdout).identity, "worker");
                assert.notEqual(owner.exitCode, 0);
                assert.match(owner.stderr, /无权限/);
                if (["narrow-safe", "keyed-filter-safe"].includes(policy.name))
                  assert.equal(JSON.parse(presence.stdout).unrelated, false);
                assert.ok(
                  !JSON.stringify([me, owner, presence]).includes(token),
                );
              } else {
                const h = new CodexHarness(command!);
                assert.throws(
                  () =>
                    mode === "fork"
                      ? h.start(o)
                      : h.resume({ ...o, sessionId: "unused-session" }),
                  (e: any) =>
                    e.message.includes("未启动子 Agent") &&
                    !e.message.includes(token),
                );
              }
              assert.deepEqual(
                [userFile, projectFile].map((p) => readFileSync(p, "utf8")),
                before,
              );
              assert.equal(s.store.all("groups").length, 1);
            });
          }
        }
      }
    } finally {
      await d.close();
      rmSync(home, { recursive: true, force: true });
    }
  },
);
