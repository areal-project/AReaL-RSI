import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import {
  CodexHarness,
  codexArgs,
} from "../packages/harness-codex/src/index.js";
import { ClaudeHarness } from "../packages/harness-claude/src/index.js";
import { OWNER } from "../packages/core/src/index.js";
import { assertCodexShellEnvironment } from "../packages/harness-codex/src/environment.js";
import {
  childEnvironment,
  instanceEnvironment,
} from "../packages/core/src/runtime.js";
import { until } from "./helpers.js";
const exec = promisify(execFile);
const cli = resolve("bin/hamlet.js");

test("Skill 安装到显式目录，完整复制引用并拒绝覆盖", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-skill-"));
  try {
    const target = join(dir, "skills", "hamlet");
    const r = await exec(process.execPath, [
      cli,
      "skill",
      "install",
      "--path",
      target,
    ]);
    assert.equal(JSON.parse(r.stdout).path, target);
    assert.equal(
      readFileSync(join(target, "SKILL.md"), "utf8"),
      readFileSync("skills/hamlet/SKILL.md", "utf8"),
    );
    assert.ok(existsSync(join(target, "references/api.md")));
    const launched = await exec(
      "bash",
      [join(target, "scripts/hamlet"), "--help"],
      { cwd: dir },
    );
    assert.match(launched.stdout, /hamlet call/);
    await assert.rejects(
      exec(process.execPath, [cli, "skill", "install", "--path", target]),
      /目标已存在/,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("子身份凭据不完整时拒绝回退本机 Owner；非法 JSON 不启动 daemon", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-no-owner-"));
  try {
    for (const partial of [
      { HAMLET_URL: "http://127.0.0.1:1" },
      { HAMLET_TOKEN: "incomplete" },
      { HAMLET_INSTANCE_ID: "abcdef-1" },
      { DETACHED_INSTANCE_ID: "abcdef-1" },
      { DOORBELL_AGENT_ID: "abcdef" },
    ]) {
      await assert.rejects(
        exec(process.execPath, [cli, "call", "whoami", "{}"], {
          env: { ...childEnvironment(), HAMLET_HOME: dir, ...partial },
        }),
        /禁止回退 Owner/,
      );
    }
    await assert.rejects(
      exec(process.execPath, [cli, "call", "group_create", "[]"], {
        env: { ...childEnvironment(), HAMLET_HOME: dir },
      }),
      /JSON 对象/,
    );
    assert.equal(existsSync(join(dir, "secret")), false);
    assert.equal(existsSync(join(dir, "daemon.json")), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("Codex 命令环境检查失败时拒绝启动且不回显诊断中的凭据", () => {
  const o = {
    cwd: process.cwd(),
    prompt: "unused",
    systemPrompt: "unused",
    env: {
      ...instanceEnvironment("http://127.0.0.1:1", "test-secret", "test-1"),
      HAMLET_NODE: process.execPath,
      HAMLET_CLI: cli,
      HAMLET_SKILL_DIR: resolve("skills/hamlet"),
      FAKE_APP_SERVER_ERROR: "sensitive-config-diagnostic",
    },
  };
  assert.throws(
    () =>
      assertCodexShellEnvironment(
        resolve("tests/fixtures/fake-codex.mjs"),
        codexArgs(o),
        o,
      ),
    (e: any) =>
      e.message.includes("未启动子 Agent") &&
      !e.message.includes("sensitive-config-diagnostic") &&
      !e.message.includes("test-secret"),
  );
});

test("Codex 命令环境检查允许超过七秒的冷启动并保留身份校验", () => {
  const o = {
    cwd: process.cwd(),
    prompt: "unused",
    systemPrompt: "unused",
    env: {
      ...instanceEnvironment("http://127.0.0.1:1", "test-secret", "test-1"),
      HAMLET_NODE: process.execPath,
      HAMLET_CLI: cli,
      HAMLET_SKILL_DIR: resolve("skills/hamlet"),
      FAKE_APP_SERVER_INITIALIZE_DELAY_MS: "8000",
    },
  };
  assert.doesNotThrow(() =>
    assertCodexShellEnvironment(
      resolve("tests/fixtures/fake-codex.mjs"),
      codexArgs(o),
      o,
    ),
  );
});

for (const harness of ["codex", "claude"]) {
  test(`${harness} 子进程通过 CLI 验证实例身份、同组消息、拒绝 Owner/提权/跨坊，fork/resume`, async () => {
    const dir = mkdtempSync(join(tmpdir(), `hamlet-cli-${harness}-`));
    const harnesses = {
      [harness]:
        harness === "codex"
          ? new CodexHarness(resolve("tests/fixtures/fake-codex.mjs"))
          : new ClaudeHarness(resolve("tests/fixtures/fake-claude.mjs")),
    };
    const d = await createDaemon({ dir, harnesses });
    try {
      await d.listen();
      const s = d.service;
      const g = await s.call("group_create", { name: "one" }, OWNER);
      const otherGroup = await s.call("group_create", { name: "two" }, OWNER);
      const agent = (group: string, role_name: string, extra: any = {}) =>
        s.call(
          "agent_create",
          {
            group,
            role_name,
            identity: "worker",
            harness,
            model: "fixture-model",
            harness_options: {},
            run_mode: "resident",
            workdir: join(dir, role_name),
            worktree: false,
            prompt_tpl: "",
            system_prompt_tpl: "",
            extra_env: {},
            doorbell_script: "",
            doorbell_enabled: false,
            ...extra,
          },
          OWNER,
        );
      const other = await agent(otherGroup.id, "other");
      const first = s.ring(OWNER, { target: other.id, prompt: "first" });
      await until(
        () => s.store.get("messages", first.id)?.status === "done",
        10000,
      );
      const notify = await agent(g.id, "notify");
      for (const identity of ["worker", "admin"]) {
        const report = join(dir, `${identity}-report`);
        const a = await agent(g.id, identity, {
          identity,
          extra_env: {
            FAKE_CLI_REPORT: report,
            FAKE_TEST_HARNESS: harness,
            FAKE_OTHER_INSTANCE: `${other.id}-1`,
            FAKE_NOTIFY_TARGET: notify.id,
            // Deliberately wrong instance settings must be replaced by daemon identity.
            HAMLET_TOKEN: "incorrect-instance-token",
            DETACHED_TOKEN: "incorrect-instance-token",
            DOORBELL_TOKEN: "must-not-inherit",
            DOORBELL_URL: "http://127.0.0.1:1/api/ring",
          },
        });
        for (const prompt of ["CHECK_CLI", "CHECK_CLI followup"]) {
          const m = s.ring(OWNER, { target: a.id, prompt });
          await until(
            () =>
              ["done", "failed"].includes(
                s.store.get("messages", m.id)?.status,
              ),
            20000,
          );
          assert.equal(
            s.store.get("messages", m.id)?.status,
            "done",
            JSON.stringify(
              s.store.get("runs", s.store.get("messages", m.id)?.run_id),
            ),
          );
        }
        const reports = readFileSync(report, "utf8")
          .trim()
          .split("\n")
          .map((line) => JSON.parse(line));
        assert.equal(reports.length, 2);
        for (const r of reports) {
          assert.equal(r.me.value.id, `${a.id}-1`);
          assert.equal(r.me.value.identity, identity);
          assert.equal(r.me.value.group, g.id);
          assert.ok(r.members.ok && r.own.ok && r.notification.ok);
          assert.ok(r.members.value.every((x: any) => x.group_id === g.id));
          for (const key of ["owner", "escalation", "other", "crossMessage"])
            assert.equal(r[key].ok, false, key);
          assert.equal(r.inheritedDoorbell, false);
          assert.equal(r.manage.ok, identity === "admin");
          if (identity === "admin") assert.equal(r.cleanup.ok, true);
        }
        const run = s.store
          .all("runs")
          .find((r) => r.instance_id === `${a.id}-1`)!;
        assert.equal(
          run.prompt.includes("需要查询或管理同组 Agent"),
          identity === "admin",
        );
        assert.ok(!run.prompt.includes(s.auth.ownerToken));
        assert.ok(
          !readFileSync(run.events_path, "utf8").includes(s.auth.ownerToken),
        );
        assert.equal(
          s.store.get("instances", `${a.id}-1`)?.config_snapshot.harness,
          harness,
        );
      }
      const env = {
        ...childEnvironment(),
        HAMLET_URL: s.url,
        HAMLET_TOKEN: s.auth.ownerToken,
      };
      const file = join(dir, "query.json");
      // Reuse a real JSON file as --file input, then compare the public result.
      const { writeFileSync } = await import("node:fs");
      writeFileSync(file, JSON.stringify({ group: g.id }));
      const queried = await exec(
        process.execPath,
        [cli, "call", "agent_list", "--file", file],
        { env },
      );
      assert.equal(JSON.parse(queried.stdout).length, 3);
    } finally {
      await d.close();
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
