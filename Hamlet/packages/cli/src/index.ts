import { connect, dataDir, Client } from "../../core/src/client.js";
import { runDaemon } from "../../daemon/src/index.js";
import {
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  unlinkSync,
  cpSync,
  chmodSync,
} from "node:fs";
import { join, dirname, resolve } from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { spawn, execFileSync } from "node:child_process";
function flags(args: string[]) {
  const out: Record<string, any> = { _: [] };
  for (let n = 0; n < args.length; n++) {
    const a = args[n];
    if (!a.startsWith("--")) {
      out._.push(a);
      continue;
    }
    const key = a.slice(2).replaceAll("-", "_"),
      v = args[n + 1] && !args[n + 1].startsWith("--") ? args[++n] : true;
    if (key === "var") {
      const text = String(v),
        idx = text.indexOf("=");
      if (idx < 1) throw Error("--var 需要 key=value");
      (out.vars ??= {})[text.slice(0, idx)] = text.slice(idx + 1);
    } else out[key] = v;
  }
  return out;
}
const print = (v: unknown) =>
  console.log(typeof v === "string" ? v : JSON.stringify(v, null, 2));
async function main() {
  const [cmd, ...args] = process.argv.slice(2);
  if (!cmd || cmd === "help" || cmd === "--help") {
    print(
      `Hamlet · 坊\nHamlet — a quiet village of detached agents, woken by a doorbell.\n\nhamlet start | serve | stop | status | ui [--open]\nhamlet call <tool> '<JSON>' | --file <JSON file> | --stdin\nhamlet skill path | install --path <absolute skill directory>\nhamlet ring <ID|角色名> [--group ID] [--prompt TEXT] [--system-prompt TEXT] [--var key=value] [--dedup-key KEY] [--dedup-window 24h]\nhamlet install-service | uninstall-service\ndoorbell ring <目标> ...`,
    );
    return;
  }
  if (cmd === "skill") {
    const source = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../skills/hamlet",
    );
    if (args[0] === "path") {
      print(source);
      return;
    }
    if (
      args[0] !== "install" ||
      args[1] !== "--path" ||
      !args[2] ||
      !args[2].startsWith("/")
    )
      throw Error("用法: hamlet skill path | install --path <绝对目标目录>");
    const destination = resolve(args[2]);
    if (existsSync(destination))
      throw Error("目标已存在，未覆盖；请检查后自行更新 Skill");
    cpSync(source, destination, {
      recursive: true,
      errorOnExist: true,
      force: false,
    });
    const quote = (s: string) => "'" + s.replaceAll("'", "'\\''") + "'";
    const launcher = join(destination, "scripts/hamlet");
    mkdirSync(dirname(launcher), { recursive: true });
    const cli = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../bin/hamlet.js",
    );
    writeFileSync(
      launcher,
      `#!/usr/bin/env bash\nexec ${quote(process.execPath)} ${quote(cli)} "$@"\n`,
    );
    chmodSync(launcher, 0o755);
    print({ path: destination, installed: true, cli: launcher });
    return;
  }
  if (cmd === "serve") {
    await runDaemon();
    return;
  }
  if (cmd === "start") {
    const c = await connect();
    print({ url: c.url, status: "running" });
    return;
  }
  if (cmd === "status") {
    try {
      const c = await connect(false);
      print(await c.request("/health"));
    } catch {
      print({ status: "stopped" });
    }
    return;
  }
  if (cmd === "stop") {
    const c = await connect(false);
    print(await c.request("/api/shutdown", {}));
    return;
  }
  if (cmd === "ring") {
    const f = flags(args);
    const target = f._[0] || process.env.DOORBELL_AGENT_ID;
    if (!target) throw Error("需要指定目标");
    delete f._;
    const c =
      process.env.DOORBELL_URL && process.env.DOORBELL_TOKEN
        ? new Client(
            new URL(process.env.DOORBELL_URL).origin,
            process.env.DOORBELL_TOKEN,
          )
        : await connect();
    print(await c.request("/api/ring", { ...f, target }));
    return;
  }
  if (cmd === "call") {
    if (!args[0]) throw Error("call 需要工具名");
    let input = args[1] || "{}";
    if (args[1] === "--file") {
      if (!args[2]) throw Error("--file 需要 JSON 文件路径");
      input = readFileSync(args[2], "utf8");
    } else if (args[1] === "--stdin") {
      input = "";
      for await (const chunk of process.stdin) input += chunk;
    }
    const params = JSON.parse(input);
    if (!params || typeof params !== "object" || Array.isArray(params))
      throw Error("工具参数必须是 JSON 对象");
    const c = await connect();
    print(await c.call(args[0], params));
    return;
  }
  if (cmd === "ui") {
    const c = await connect();
    const { url } = await c.call("ui_open");
    print(url);
    if (args.includes("--open")) {
      const child = spawn(
        process.platform === "darwin" ? "open" : "xdg-open",
        [url],
        { stdio: "ignore" },
      );
      child.on("error", () => {});
      child.unref();
    }
    return;
  }
  if (cmd === "install-service" || cmd === "uninstall-service") {
    const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../.."),
      script = join(root, "packages/daemon/dist/index.js");
    const uninstall = cmd === "uninstall-service";
    if (process.platform === "linux") {
      const directory = join(homedir(), ".config/systemd/user");
      const serviceName =
        uninstall &&
        !existsSync(join(directory, "hamlet.service")) &&
        existsSync(join(directory, "detached.service"))
          ? "detached.service"
          : "hamlet.service";
      const path = join(directory, serviceName);
      if (uninstall) {
        execFileSync("systemctl", ["--user", "disable", "--now", serviceName]);
        if (existsSync(path)) unlinkSync(path);
      } else {
        mkdirSync(dirname(path), { recursive: true });
        const q = (s: string) =>
          '"' +
          s
            .replaceAll("\\", "\\\\")
            .replaceAll('"', '\\"')
            .replaceAll("%", "%%") +
          '"';
        writeFileSync(
          path,
          `[Unit]\nDescription=Hamlet daemon\n[Service]\nExecStart=${q(process.execPath)} ${q(script)} --serve\nEnvironment=${q("HAMLET_HOME=" + dataDir())}\nEnvironment=${q("PATH=" + process.env.PATH)}\nRestart=on-failure\n[Install]\nWantedBy=default.target\n`,
        );
        execFileSync("systemctl", ["--user", "daemon-reload"]);
        execFileSync("systemctl", ["--user", "enable", "--now", serviceName]);
      }
      print({ ok: true, path });
      return;
    }
    if (process.platform === "darwin") {
      const directory = join(homedir(), "Library/LaunchAgents");
      const filename =
        uninstall &&
        !existsSync(join(directory, "local.hamlet.plist")) &&
        existsSync(join(directory, "local.detached.plist"))
          ? "local.detached.plist"
          : "local.hamlet.plist";
      const path = join(directory, filename);
      const xml = (s: string) =>
        s
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;");
      if (uninstall) {
        execFileSync("launchctl", ["unload", path]);
        unlinkSync(path);
      } else {
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(
          path,
          `<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>Label</key><string>local.hamlet</string><key>ProgramArguments</key><array><string>${xml(process.execPath)}</string><string>${xml(script)}</string><string>--serve</string></array><key>EnvironmentVariables</key><dict><key>HAMLET_HOME</key><string>${xml(dataDir())}</string><key>PATH</key><string>${xml(process.env.PATH || "")}</string></dict><key>RunAtLoad</key><true/><key>KeepAlive</key><true/></dict></plist>`,
        );
        execFileSync("launchctl", ["load", path]);
      }
      print({ ok: true, path });
      return;
    }
    throw Error("服务安装仅支持 Linux/macOS");
  }
  throw Error(`未知命令: ${cmd}`);
}
main().catch((e) => {
  console.error(
    JSON.stringify({ error: e.message, details: e.details }, null, 2),
  );
  process.exitCode = 1;
});
