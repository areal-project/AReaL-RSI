import { test } from "node:test";
import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  symlinkSync,
} from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { OWNER, git } from "../packages/core/src/index.js";
import { agentSkillPath } from "../packages/core/src/skills.js";
import { fixture, until } from "./helpers.js";

for (const mode of ["direct", "git", "plain"]) {
  test(`Agent skill installed at creation and fork/resume in ${mode}`, async () => {
    const f = await fixture();
    try {
      const a = await f.agent({
        worktree: mode !== "direct",
        git_init: mode === "git",
      });
      const root = mode === "direct" ? a.workdir.root : a.workdir.main_path;
      assert.ok(existsSync(join(agentSkillPath(root, "codex"), "SKILL.md")));
      if (mode === "git") assert.equal(git(root, "status", "--porcelain"), "");
      f.s.ring(OWNER, { target: a.id });
      f.s.tick();
      await until(() => f.h.calls.length === 1);
      const o = f.h.calls[0].options;
      const local = agentSkillPath(o.cwd, "codex");
      assert.equal(o.env.HAMLET_SKILL_DIR, local);
      assert.equal(
        readFileSync(join(local, "SKILL.md"), "utf8"),
        readFileSync("skills/hamlet-agent/SKILL.md", "utf8"),
      );
      assert.ok(!o.prompt.includes(join(local, "SKILL.md")));
      const cli = await promisify(execFile)(
        "bash",
        [join(local, "scripts/hamlet"), "--help"],
        {
          env: {
            ...process.env,
            HAMLET_NODE: process.execPath,
            HAMLET_CLI: resolve("bin/hamlet.js"),
          },
        },
      );
      assert.match(cli.stdout, /hamlet call/);
      if (mode === "git") assert.equal(git(o.cwd, "status", "--porcelain"), "");
      f.h.calls[0].finish();
      await until(() => f.s.active.size === 0);
      const instance = f.s.store.all("instances")[0];
      f.s.ring(OWNER, { target: instance.id });
      f.s.tick();
      await until(() => f.h.calls.length === 2);
      assert.equal(f.h.calls[1].options.env.HAMLET_SKILL_DIR, local);
      assert.equal(f.h.calls[1].kind, "resume");
    } finally {
      await f.close();
    }
  });
}

test("Claude skill uses its discoverable directory; custom files and symlinks are not overwritten", async () => {
  const f = await fixture();
  try {
    f.s.harnesses.claude = f.h;
    const a = await f.agent({ worktree: false, harness: "claude" });
    assert.ok(
      existsSync(join(a.workdir.root, ".claude/skills/hamlet-agent/SKILL.md")),
    );
    const custom = join(f.dir, "custom");
    const skill = agentSkillPath(custom, "codex");
    mkdirSync(skill, { recursive: true });
    writeFileSync(join(skill, "SKILL.md"), "user content");
    await assert.rejects(
      f.agent({ role_name: "custom", worktree: false, workdir: custom }),
      /未覆盖/,
    );
    assert.equal(readFileSync(join(skill, "SKILL.md"), "utf8"), "user content");
    const linked = join(f.dir, "linked");
    mkdirSync(linked);
    symlinkSync(join(f.dir, "missing"), join(linked, ".agents"));
    await assert.rejects(
      f.agent({ role_name: "linked", worktree: false, workdir: linked }),
      /符号链接/,
    );
    assert.equal(existsSync(join(f.dir, "missing")), false);
  } finally {
    await f.close();
  }
});

test("Generated skill in a repository subdirectory is excluded from Git changes", async () => {
  const f = await fixture();
  try {
    const repo = join(f.dir, "repo");
    mkdirSync(repo);
    git(repo, "init", "-b", "main");
    git(repo, "commit", "--allow-empty", "-m", "init");
    await f.agent({ workdir: join(repo, "nested project"), worktree: false });
    assert.equal(git(repo, "status", "--porcelain"), "");
  } finally {
    await f.close();
  }
});

test("only admins receive the skill pointer, using live identity on resume", async () => {
  const f = await fixture();
  try {
    const a = await f.agent({
      worktree: false,
      identity: "worker",
      system_prompt_tpl: "You review mathematical proofs.",
    });
    f.s.ring(OWNER, { target: a.id, prompt: "Review lemma 1." });
    f.s.tick();
    await until(() => f.h.calls.length === 1);
    assert.equal(
      f.h.calls[0].options.prompt,
      "[来自 ring 调用方 (owner)]\nReview lemma 1.",
    );
    assert.equal(
      (f.h.calls[0].options as any).systemPrompt,
      "You review mathematical proofs.",
    );
    f.h.calls[0].finish();
    await until(() => f.s.active.size === 0);
    await f.s.call("agent_update", { id: a.id, identity: "admin" }, OWNER);
    f.s.ring(OWNER, { target: `${a.id}-1`, prompt: "Review lemma 2." });
    f.s.tick();
    await until(() => f.h.calls.length === 2);
    assert.equal(f.h.calls[1].kind, "resume");
    const prompt = f.h.calls[1].options.prompt;
    assert.ok(
      prompt.includes(
        join(agentSkillPath(a.workdir.root, "codex"), "SKILL.md"),
      ),
    );
    assert.ok(!prompt.includes("HAMLET_TOKEN"));
    assert.ok(!prompt.includes("message_to"));
    f.h.calls[1].finish();
    await until(() => f.s.active.size === 0);
    await f.s.call("agent_update", { id: a.id, identity: "worker" }, OWNER);
    f.s.ring(OWNER, { target: `${a.id}-1`, prompt: "Review lemma 3." });
    f.s.tick();
    await until(() => f.h.calls.length === 3);
    assert.equal(
      f.h.calls[2].options.prompt,
      "[来自 ring 调用方 (owner)]\nReview lemma 3.",
    );
  } finally {
    await f.close();
  }
});
