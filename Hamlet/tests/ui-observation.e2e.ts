import { test, expect } from "@playwright/test";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import { OWNER } from "../packages/core/src/index.js";
import { Controlled, until } from "./helpers.js";

test("Web UI 直接显示共享目录约束、阻塞实例/run 以及 Owner 来源", async ({
  page,
}) => {
  const dir = mkdtempSync(join(tmpdir(), "detached-ui-observe-")),
    h = new Controlled(),
    d = await createDaemon({ dir, harnesses: { codex: h } });
  try {
    const url = await d.listen(),
      s = d.service,
      g = await s.call("group_create", { name: "串行可观察性" }, OWNER);
    const a = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "分析者",
          workdir: join(dir, "work"),
          worktree: false,
        },
        OWNER,
      ),
      b = await s.call(
        "agent_create",
        {
          group: g.id,
          role_name: "校验者",
          workdir: join(dir, "work"),
          worktree: false,
        },
        OWNER,
      );
    await s.call("message_to", { target: a.id, prompt: "分析" }, OWNER);
    await until(() => h.calls.length === 1);
    const waiting = await s.call(
      "message_to",
      { target: b.id, prompt: "复核" },
      OWNER,
    );
    await page.goto(url + "/?token=" + s.auth.ownerToken);
    await page.getByRole("button", { name: /消息队列/ }).click();
    await expect(
      page.getByText("host · 宿主调用方", { exact: true }),
    ).toHaveCount(2);
    await expect(
      page.getByText(
        "同一 direct 工作目录全局串行；等待当前回合结束并释放目录",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(page.getByText("阻塞实例：", { exact: false })).toContainText(
      a.id + "-1",
    );
    await expect(page.getByText("阻塞实例：", { exact: false })).toContainText(
      waiting.queue_reason.blocking_run_id,
    );
    mkdirSync(resolve("artifacts"), { recursive: true });
    await page.screenshot({
      path: resolve("artifacts/queue-observation.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "◇　校验者" }).click();
    await expect(page.getByRole("heading", { name: "执行语义" })).toBeVisible();
    await expect(
      page.getByText("共用该目录：", { exact: false }),
    ).toContainText("分析者");
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
