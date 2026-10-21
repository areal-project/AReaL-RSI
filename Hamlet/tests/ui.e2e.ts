import { test, expect } from "@playwright/test";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import { CodexHarness } from "../packages/harness-codex/src/index.js";

test("Web UI 创建、触发、实时日志、运行历史、Diff 合并、队列与 doorbell", async ({
  page,
}) => {
  const dir = mkdtempSync(join(tmpdir(), "detached-ui-"));
  const d = await createDaemon({
    dir,
    harnesses: {
      codex: new CodexHarness(resolve("tests/fixtures/fake-codex.mjs")),
    },
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const url = await d.listen();
    await page.goto(url + "/?token=" + d.service.auth.ownerToken);
    await expect(
      page.getByRole("heading", { name: "Agent 工作台" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "＋ 新建坊" }).click();
    await page.getByLabel("坊名").fill("产品开发");
    await page.getByRole("button", { name: "创建", exact: true }).click();
    await expect(page.getByRole("heading", { name: "产品开发" })).toBeVisible();
    await page.getByRole("button", { name: "＋ 新建 Agent" }).click();
    await page.getByLabel("角色名").fill("实现工程师");
    await page.getByLabel("工作目录（绝对路径）").fill(join(dir, "work"));
    await page.getByLabel("额外环境变量（JSON）").fill('{"FAKE_EDIT":"1"}');
    await page.locator("[name=prompt_tpl]").fill("处理 {{task}}");
    await page.getByRole("button", { name: "创建 Agent", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "实现工程师" }),
    ).toBeVisible();
    await expect(page.locator("mark").first()).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept("完成任务"));
    await page.getByRole("button", { name: "发送消息 / 触发" }).click();
    await page.getByRole("button", { name: "实例", exact: true }).click();
    await expect(page.getByText("idle", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "实现工程师#1", exact: true }).click();
    await page.getByRole("button", { name: "运行记录", exact: true }).click();
    await expect(page.getByText("fork ·", { exact: false })).toBeVisible();
    await page.locator("summary").first().click();
    await expect(page.getByText("STARTED", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "实时日志", exact: true }).click();
    await expect(
      page.getByText("assistant_message", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Diff", exact: true }).click();
    await expect(page.locator("pre").first()).toContainText("result.txt");
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "合并到 main", exact: true })
      .click();
    await expect(page.locator("pre").first()).toContainText("没有差异");
    await page.getByRole("button", { name: /消息队列/ }).click();
    await expect(page.getByText("done", { exact: true })).toBeVisible();
    await expect(page.getByText("ui · Web UI", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /Doorbell 监控/ }).click();
    await expect(
      page.getByRole("heading", { name: "Doorbell 监控" }),
    ).toBeVisible();
    await page.getByRole("button", { name: /总览/ }).click();
    await expect(
      page.getByRole("heading", { name: "Agent 工作台" }),
    ).toBeVisible();
    mkdirSync(resolve("artifacts"), { recursive: true });
    await page.screenshot({
      path: resolve("artifacts/ui-overview.png"),
      fullPage: true,
    });
    assertNoErrors();
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
  function assertNoErrors() {
    expect(errors).toEqual([]);
  }
});
