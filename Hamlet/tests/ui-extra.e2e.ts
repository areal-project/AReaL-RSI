import { test, expect } from "@playwright/test";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import { Controlled, until } from "./helpers.js";
import { OWNER } from "../packages/core/src/index.js";

test("Web UI 非 git 迁移、冲突应用、配置编辑、脚本编辑和失败消息重试", async ({
  page,
}) => {
  const dir = mkdtempSync(join(tmpdir(), "detached-ui-extra-")),
    h = new Controlled(),
    d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    const url = await d.listen(),
      s = d.service,
      g = await s.call("group_create", { name: "测试组" }, OWNER);
    const work = join(dir, "plain");
    mkdirSync(work);
    writeFileSync(join(work, "file.txt"), "base");
    await page.goto(url + "/?token=" + s.auth.ownerToken);
    await page.getByRole("button", { name: "＋ 新建 Agent" }).click();
    await page.getByLabel("角色名").fill("审查者");
    await page.getByLabel("工作目录（绝对路径）").fill(work);
    await page.getByLabel("新目录初始化 Git").selectOption("false");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "创建 Agent", exact: true }).click();
    await expect(page.getByRole("heading", { name: "审查者" })).toBeVisible();
    const l = s.store.all("logical_agents")[0];
    expect(readFileSync(join(work, "main/file.txt"), "utf8")).toBe("base");
    await page.getByLabel("模型", { exact: true }).fill("next-model");
    await page.getByRole("button", { name: "保存配置" }).click();
    await expect(page.getByRole("status")).toContainText("已完成");
    await until(
      () => s.store.get("logical_agents", l.id)?.model === "next-model",
    );
    page.once("dialog", (dialog) => dialog.accept("work"));
    await page.getByRole("button", { name: "发送消息 / 触发" }).click();
    await until(() => h.calls.length === 1);
    const i = s.store.all("instances")[0];
    writeFileSync(join(i.worktree_path, "file.txt"), "agent");
    writeFileSync(join(work, "main/file.txt"), "main");
    h.calls[0].finish();
    await until(() => s.active.size === 0);
    await page.getByRole("button", { name: "实例", exact: true }).click();
    await page.getByRole("link", { name: "审查者#1" }).click();
    await page.getByRole("button", { name: "Diff", exact: true }).click();
    await expect(page.getByText("main 已修改 · 冲突")).toBeVisible();
    await page.getByLabel("选择 file.txt").check();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "应用所选到 main" }).click();
    await until(
      () => readFileSync(join(work, "main/file.txt"), "utf8") === "agent",
    );
    await page.getByRole("button", { name: "◇　审查者" }).click();
    await page.getByRole("button", { name: "Doorbell", exact: true }).click();
    await page
      .getByLabel("Doorbell 脚本")
      .fill("#!/bin/sh\necho bell-ready\nsleep 60\n");
    await page.getByRole("button", { name: "保存并重启" }).click();
    await expect(page.locator("pre")).toContainText("bell-ready");
    await page.getByRole("button", { name: "stop", exact: true }).click();
    await expect(page.getByText("stopped", { exact: true })).toBeVisible();
    await s.call(
      "agent_update",
      { id: l.id, prompt_tpl: "{{missing}}" },
      OWNER,
    );
    const bad = s.ring(OWNER, { target: l.id });
    await until(() => s.store.get("messages", bad.id)?.status === "failed");
    await page.getByRole("button", { name: /消息队列/ }).click();
    await expect(page.getByText("failed", { exact: true })).toBeVisible();
    await s.call("agent_update", { id: l.id, prompt_tpl: "fixed" }, OWNER);
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await until(() => h.calls.length === 2);
    h.calls[1].finish();
    await expect(page.getByText("done", { exact: true })).toHaveCount(2);
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
