import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createDaemon } from "../packages/daemon/src/index.js";
import { childEnvironment, OWNER } from "../packages/core/src/index.js";
import { Controlled } from "./helpers.js";

for (const protocol of ["http", "https"]) {
  test(`公开 ${protocol} 地址登录、Cookie 与 API；严格限制 Host/Origin，保留内部连接`, async () => {
    const dir = mkdtempSync(join(tmpdir(), "hamlet-public-url-"));
    const origin = `${protocol}://mapped.hamlet.test:5941`;
    const host = new URL(origin).host;
    const d = await createDaemon({
      dir,
      publicUrl: origin + "/",
      harnesses: { codex: new Controlled() },
    });
    try {
      const local = await d.listen();
      const login = await d.service.call("ui_open", {}, OWNER);
      const url = new URL(login.url);
      assert.equal(url.origin, origin);
      assert.equal(d.service.url, local);
      assert.equal(
        JSON.parse(readFileSync(join(dir, "daemon.json"), "utf8")).url,
        local,
      );
      const r = await d.app.inject({
        method: "GET",
        url: url.pathname + url.search,
        headers: { host },
      });
      assert.equal(r.statusCode, 302);
      assert.equal(r.headers.location, "/");
      const cookie = String(r.headers["set-cookie"]);
      assert.match(cookie, /HttpOnly/);
      assert.match(cookie, /SameSite=Strict/);
      assert.equal(cookie.includes("Secure"), protocol === "https");
      for (const forwardedHost of [host.toUpperCase(), "127.0.0.1:34883"]) {
        const api = await d.app.inject({
          method: "POST",
          url: "/api/tools/group_list",
          headers: {
            host: forwardedHost,
            origin,
            cookie: cookie.split(";")[0],
          },
          payload: {},
        });
        assert.equal(api.statusCode, 200);
        assert.deepEqual(api.json(), []);
      }
      for (const badOrigin of [
        "http://evil.test",
        `${protocol}://mapped.hamlet.test:5942`,
        "null",
      ]) {
        for (const forwardedHost of [host, "127.0.0.1:34883"]) {
          const rejected = await d.app.inject({
            method: "POST",
            url: "/api/tools/group_list",
            headers: {
              host: forwardedHost,
              origin: badOrigin,
              cookie: cookie.split(";")[0],
            },
            payload: {},
          });
          assert.equal(rejected.statusCode, 403);
        }
      }
      const unknownHost = await d.app.inject({
        method: "GET",
        url: "/health",
        headers: {
          host: "evil.test",
          "x-forwarded-host": host,
          "x-forwarded-proto": protocol,
        },
      });
      assert.equal(unknownHost.statusCode, 403);
      const anonymous = await d.app.inject({
        method: "GET",
        url: "/api/identity",
        headers: { host },
      });
      assert.equal(anonymous.statusCode, 401);
      const invalidLogin = await d.app.inject({
        method: "GET",
        url: "/?token=invalid",
        headers: { host },
      });
      assert.equal(invalidLogin.statusCode, 401);
      const workerToken = d.service.auth.sign({
        kind: "instance",
        id: "worker-1",
      });
      const workerLogin = await d.app.inject({
        method: "GET",
        url: "/?token=" + workerToken,
        headers: { host },
      });
      assert.equal(workerLogin.statusCode, 403);
    } finally {
      await d.close();
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

test("无效公开地址在创建状态数据前报错", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-bad-public-url-"));
  try {
    for (const publicUrl of [
      "not-a-url",
      "ftp://mapped.test/",
      "https://user:password@mapped.test/",
      "https://mapped.test/proxy/",
      "https://mapped.test/?token=secret",
      "https://mapped.test/#fragment",
    ]) {
      const state = join(dir, "must-not-be-created");
      await assert.rejects(
        createDaemon({ dir: state, publicUrl }),
        /HAMLET_PUBLIC_URL/,
      );
      assert.equal(existsSync(state), false);
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("后台 CLI 启动从 HAMLET_PUBLIC_URL 读取映射地址，ui 返回公开登录入口", async () => {
  const dir = mkdtempSync(join(tmpdir(), "hamlet-public-cli-"));
  const exec = promisify(execFile);
  const env = {
    ...childEnvironment(),
    HAMLET_HOME: dir,
    HAMLET_PUBLIC_URL: "http://mapped.hamlet.test:5941/",
  };
  const cli = (...args: string[]) =>
    exec(process.execPath, [resolve("bin/hamlet.js"), ...args], { env });
  try {
    const started = JSON.parse((await cli("start")).stdout);
    assert.match(started.url, /^http:\/\/127\.0\.0\.1:/);
    const login = new URL((await cli("ui")).stdout.trim());
    assert.equal(login.origin, "http://mapped.hamlet.test:5941");
    assert.ok(login.searchParams.has("token"));
  } finally {
    if (existsSync(join(dir, "daemon.json"))) await cli("stop");
    rmSync(dir, { recursive: true, force: true });
  }
});
