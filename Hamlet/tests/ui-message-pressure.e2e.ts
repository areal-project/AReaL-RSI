import { test, expect } from "@playwright/test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDaemon } from "../packages/daemon/src/index.js";
import { OWNER } from "../packages/core/src/index.js";
import { Controlled } from "./helpers.js";

test("Owner sees rejected message and confirms exactly one push", async ({
  page,
}) => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-ui-pressure-"));
  const d = await createDaemon({ dir, harnesses: { codex: new Controlled() } });
  try {
    const url = await d.listen();
    const s = d.service;
    const g = await s.call("group_create", { name: "Queue test" }, OWNER);
    await s.call("group_update", { id: g.id, paused: true }, OWNER);
    const a = await s.call(
      "agent_create",
      {
        group: g.id,
        role_name: "Target",
        worktree: false,
        workdir: join(dir, "work"),
        run_mode: "resident",
      },
      OWNER,
    );
    for (let n = 0; n < 6; n++) s.ring(OWNER, { target: a.id });
    let id = "";
    try {
      s.ring(OWNER, { target: a.id, prompt: "push me" });
    } catch (e: any) {
      id = e.details.message_id;
    }
    expect(id).not.toBe("");
    await page.goto(url + "/?token=" + s.auth.ownerToken);
    await page.getByRole("button", { name: /消息队列/ }).click();
    await expect(page.getByText("rejected", { exact: true })).toBeVisible();
    await expect(
      page
        .locator("small.conflict")
        .filter({ hasText: "目标 Agent 已有 6 条排队消息" }),
    ).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "确认推送", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "确认推送", exact: true }),
    ).toHaveCount(0);
    await expect.poll(() => s.store.get("messages", id)!.status).toBe("queued");
    expect(s.store.all("messages")).toHaveLength(7);
    let discarded = "";
    try {
      s.ring(OWNER, { target: a.id, prompt: "discard me" });
    } catch (e: any) {
      discarded = e.details.message_id;
    }
    expect(discarded).not.toBe("");
    const row = page.getByRole("row").filter({ hasText: "discard me" });
    await row.getByRole("button", { name: "取消", exact: true }).click();
    await expect
      .poll(() => s.store.get("messages", discarded)!.status)
      .toBe("cancelled");
    await expect(
      row.getByRole("button", { name: "确认推送", exact: true }),
    ).toHaveCount(0);
  } finally {
    await d.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
