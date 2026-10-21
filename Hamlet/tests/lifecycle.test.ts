import { test } from "node:test";
import assert from "node:assert/strict";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "../packages/core/src/client.js";
import { Auth } from "../packages/core/src/auth.js";
import { until } from "./helpers.js";
const exec = promisify(execFile);

test("CLI 并发启动单 daemon，宿主退出后仍运行；硬崩溃恢复中断并保留排队消息", async () => {
  const dir = mkdtempSync(join(tmpdir(), "detached-lifecycle-"));
  const env = {
    ...process.env,
    HAMLET_HOME: dir,
    HAMLET_CODEX_BIN: resolve("tests/fixtures/fake-codex.mjs"),
  };
  let daemonPid: number | undefined, childPid: number | undefined;
  const command = async (...args: string[]) =>
    JSON.parse(
      (
        await exec(process.execPath, [resolve("bin/hamlet.js"), ...args], {
          env,
        })
      ).stdout,
    );
  try {
    const legacyStart = async () =>
      JSON.parse(
        (
          await exec(process.execPath, [resolve("bin/detached.js"), "start"], {
            env,
          })
        ).stdout,
      );
    const starts = await Promise.all([command("start"), legacyStart()]);
    assert.equal(starts[0].url, starts[1].url);
    const info = JSON.parse(readFileSync(join(dir, "daemon.json"), "utf8"));
    daemonPid = info.pid;
    const c = new Client(info.url, new Auth(dir).ownerToken);
    const g = await command("call", "group_create", '{"name":"lifecycle"}');
    const l = await command(
      "call",
      "agent_create",
      JSON.stringify({
        group: g.id,
        role_name: "detached",
        workdir: join(dir, "work"),
        run_mode: "resident",
        worktree: false,
      }),
    );
    const output = await exec(
      process.execPath,
      [resolve("bin/doorbell.js"), "ring", l.id, "--prompt", "WAIT"],
      { env },
    );
    const m = JSON.parse(output.stdout);
    let instance: any;
    await until(() => {
      try {
        process.kill(daemonPid!, 0);
        return true;
      } catch {
        return false;
      }
    });
    for (let n = 0; n < 100; n++) {
      instance = await c
        .call("instance_get", { id: l.id + "-1" })
        .catch(() => null);
      if (instance?.runs[0]?.pid && instance.harness_session_id) break;
      await new Promise((r) => setTimeout(r, 20));
    }
    assert.ok(instance?.runs[0]?.pid);
    childPid = instance.runs[0].pid;
    assert.equal(instance.runs[0].status, "running");
    const queued = await c.request("/api/ring", {
      target: l.id,
      prompt: "after crash",
    });
    process.kill(daemonPid!, "SIGKILL");
    await new Promise((r) => setTimeout(r, 100));
    const restarted = await command("start");
    const newInfo = JSON.parse(readFileSync(join(dir, "daemon.json"), "utf8"));
    assert.notEqual(newInfo.pid, daemonPid);
    daemonPid = newInfo.pid;
    const cc = new Client(restarted.url, new Auth(dir).ownerToken);
    let messages: any[] = [];
    for (let n = 0; n < 200; n++) {
      messages = await cc.call("message_list");
      if (messages.find((x) => x.id === queued.id)?.status === "done") break;
      await new Promise((r) => setTimeout(r, 25));
    }
    assert.equal(messages.find((x) => x.id === m.id)?.status, "failed");
    assert.equal(messages.find((x) => x.id === queued.id)?.status, "done");
    const recovered = await cc.call("instance_get", { id: l.id + "-1" });
    assert.equal(recovered.runs[0].status, "interrupted");
    assert.equal(recovered.runs[1].kind, "resume");
    if (process.platform === "linux" && existsSync(`/proc/${childPid}/stat`))
      assert.equal(
        readFileSync(`/proc/${childPid}/stat`, "utf8").split(" ")[2],
        "Z",
      );
    await command("stop");
    await until(() => !existsSync(join(dir, "daemon.json")));
    daemonPid = undefined;
  } finally {
    if (daemonPid) {
      try {
        process.kill(daemonPid, "SIGTERM");
      } catch {}
    }
    if (childPid) {
      try {
        process.kill(-childPid, "SIGKILL");
      } catch {}
    }
    await new Promise((r) => setTimeout(r, 100));
    rmSync(dir, { recursive: true, force: true });
  }
});
