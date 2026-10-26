import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  renameSync,
  existsSync,
  rmSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync, execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  childEnvironment,
  dataDir,
  runtimeEnv,
  workdirMetadataDir,
  instanceEnvironment,
} from "../packages/core/src/runtime.js";
import {
  setupWorkdir,
  prepare,
  cleanup,
  OWNER,
} from "../packages/core/src/index.js";
import { codexArgs } from "../packages/harness-codex/src/index.js";
import { claudeArgs } from "../packages/harness-claude/src/index.js";
import { createDaemon } from "../packages/daemon/src/index.js";
import { Controlled } from "./helpers.js";

test("Hamlet 默认目录及变量优先级；旧数据目录直接复用而不新建空数据库", () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-brand-home-"));
  try {
    assert.equal(dataDir({}, dir), join(dir, ".hamlet"));
    mkdirSync(join(dir, ".detached"));
    writeFileSync(join(dir, ".detached/state.db"), "existing");
    assert.equal(dataDir({}, dir), join(dir, ".detached"));
    assert.equal(existsSync(join(dir, ".hamlet")), false);
    assert.equal(dataDir({ DETACHED_HOME: "/old" }, dir), "/old");
    assert.equal(
      dataDir({ HAMLET_HOME: "/new", DETACHED_HOME: "/old" }, dir),
      "/new",
    );
    assert.equal(runtimeEnv("URL", { DETACHED_URL: "old" }), "old");
    assert.equal(
      runtimeEnv("URL", { HAMLET_URL: "new", DETACHED_URL: "old" }),
      "new",
    );
    const env = instanceEnvironment("http://127.0.0.1:1", "token", "abcdef-1");
    assert.equal(env.HAMLET_TOKEN, env.DETACHED_TOKEN);
    assert.equal(env.HAMLET_INSTANCE_ID, env.DETACHED_INSTANCE_ID);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("新 workdir 使用 .hamlet；已有 .detached 标记与实例基准原地复用", () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-brand-layout-"));
  try {
    const w = setupWorkdir({ workdir: join(dir, "work"), git_init: false });
    assert.equal(existsSync(join(w.root, ".hamlet/workdir.json")), true);
    renameSync(join(w.root, ".hamlet"), join(w.root, ".detached"));
    writeFileSync(join(w.main_path, "existing"), "data");
    const reused = setupWorkdir({ workdir: w.root });
    assert.equal(reused.id, w.id);
    assert.equal(workdirMetadataDir(w.root), join(w.root, ".detached"));
    assert.equal(readFileSync(join(w.main_path, "existing"), "utf8"), "data");
    const path = prepare(reused, "abcdef-1");
    assert.equal(existsSync(join(w.root, ".detached/bases/abcdef-1")), true);
    assert.equal(existsSync(join(w.root, ".hamlet")), false);
    cleanup(reused, { id: "abcdef-1", worktree_path: path });
    assert.equal(existsSync(path), false);
    assert.equal(existsSync(join(w.root, ".detached/bases/abcdef-1")), false);
    assert.equal(existsSync(w.base_path), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("harness 默认不注入 MCP 或凭据；旧 CLI 仍为同一入口", () => {
  const o = {
    cwd: "/tmp",
    systemPrompt: "system",
    prompt: "prompt",
    env: {},
  };
  const args = codexArgs(o);
  assert.ok(!args.some((x) => x.includes("mcp_servers")));
  const claude = claudeArgs(o);
  const config = JSON.parse(claude[claude.indexOf("--mcp-config") + 1]);
  assert.deepEqual(config, { mcpServers: {} });
  assert.ok(claude.includes("--strict-mcp-config"));
  const primary = execFileSync(
      process.execPath,
      [resolve("bin/hamlet.js"), "--help"],
      { encoding: "utf8" },
    ),
    legacy = execFileSync(
      process.execPath,
      [resolve("bin/detached.js"), "--help"],
      { encoding: "utf8" },
    );
  assert.match(primary, /Hamlet · 坊/);
  assert.match(primary, /hamlet start/);
  assert.equal(primary, legacy);
});

test("新旧认证环境与 cookie 均可连接同一个 Hamlet daemon", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-brand-auth-"));
  const d = await createDaemon({ dir, harnesses: { codex: new Controlled() } });
  const exec = promisify(execFile);
  try {
    const url = await d.listen();
    const health = (await (await fetch(url + "/health")).json()) as any;
    assert.equal(health.name, "Hamlet");
    for (const cookie of ["hamlet", "detached"]) {
      const r = await fetch(url + "/api/identity", {
        headers: { Cookie: `${cookie}=${d.service.auth.ownerToken}` },
      });
      assert.equal(r.status, 200);
    }
    const login = await fetch(url + "/?token=" + d.service.auth.ownerToken, {
      redirect: "manual",
    });
    assert.match(login.headers.get("set-cookie")!, /^hamlet=/);
    for (const prefix of ["HAMLET", "DETACHED"]) {
      const env = {
        ...childEnvironment(),
        [`${prefix}_URL`]: url,
        [`${prefix}_TOKEN`]: d.service.auth.ownerToken,
      };
      const cli = async (name: string) =>
        JSON.parse(
          (
            await exec(
              process.execPath,
              [resolve("bin/hamlet.js"), "call", name, "{}"],
              { env },
            )
          ).stdout,
        );
      assert.equal((await cli("whoami")).identity, "owner");
      assert.deepEqual(await cli("group_list"), []);
    }
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
