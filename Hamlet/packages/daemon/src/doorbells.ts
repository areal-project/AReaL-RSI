import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, openSync, closeSync } from "node:fs";
import { join } from "node:path";
import { Service, now, childEnvironment } from "../../core/src/index.js";
export class Doorbells {
  children = new Map<string, ChildProcess>();
  timers = new Map<string, NodeJS.Timeout>();
  crashes = new Map<string, number[]>();
  closing = false;
  constructor(
    public service: Service,
    public binDir: string,
  ) {
    service.onDoorbellChange = (id, a) => this.control(id, a);
  }
  async restore() {
    for (const l of this.service.store
      .all("logical_agents")
      .filter((l) => !l.deleted_at && l.doorbell_enabled))
      await this.control(l.id, "start", false);
  }
  async control(id: string, action: string, persist = true) {
    await this.stop(id);
    const l = this.service.store.get("logical_agents", id)!;
    if (persist)
      this.service.store.update("logical_agents", id, {
        doorbell_enabled: action !== "stop",
      });
    if (action === "stop") {
      this.service.store.update("doorbells", id, {
        status: "stopped",
        pid: null,
      });
      this.service.changed();
      return { ok: true };
    }
    this.crashes.delete(id);
    this.start(id);
    return { ok: true };
  }
  start(id: string) {
    try {
      this.launch(id);
    } catch (e) {
      this.service.store.update("doorbells", id, {
        status: "failed",
        pid: null,
        error: String(e),
      });
      this.service.changed();
    }
  }
  private launch(id: string) {
    if (this.closing) return;
    const s = this.service,
      l = s.store.get("logical_agents", id);
    if (!l || l.deleted_at || !l.doorbell_enabled) return;
    const dir = join(s.dir, "doorbells", id),
      state = join(dir, "state");
    mkdirSync(state, { recursive: true });
    const log = join(dir, "output.log");
    const old = s.store.get("doorbells", id);
    if (!old)
      s.store.insert("doorbells", {
        logical_id: id,
        status: "stopped",
        restart_count: 0,
        log_path: log,
      });
    if (!l.doorbell_script || !existsSync(l.doorbell_script)) {
      s.store.update("doorbells", id, {
        status: "failed",
        error: "doorbell 脚本不存在",
      });
      s.changed();
      return;
    }
    const fd = openSync(log, "a", 0o600);
    const child = spawn(l.doorbell_script, [], {
      detached: true,
      stdio: ["ignore", fd, fd],
      env: {
        ...childEnvironment(l.extra_env),
        PATH: `${this.binDir}:${l.extra_env.PATH || process.env.PATH}`,
        DOORBELL_AGENT_ID: id,
        DOORBELL_GROUP_ID: l.group_id,
        DOORBELL_URL: s.url + "/api/ring",
        DOORBELL_TOKEN: s.auth.sign({ kind: "doorbell", id }),
        DOORBELL_STATE_DIR: state,
      },
    });
    closeSync(fd);
    this.children.set(id, child);
    s.store.update("doorbells", id, {
      status: "running",
      pid: child.pid || null,
      error: null,
    });
    s.changed();
    let handled = false;
    const crashed = (error?: string) => {
      if (handled || this.children.get(id) !== child) return;
      handled = true;
      this.children.delete(id);
      const times = (this.crashes.get(id) || []).filter(
        (t) => Date.now() - t < 600000,
      );
      times.push(Date.now());
      this.crashes.set(id, times);
      const failed = times.length > 5;
      s.store.update("doorbells", id, {
        status: failed ? "failed" : "crashed",
        pid: null,
        restart_count: (s.store.get("doorbells", id)?.restart_count || 0) + 1,
        error: error || "脚本已退出",
      });
      s.changed();
      if (!failed && !this.closing) {
        const t = setTimeout(
          () => {
            this.timers.delete(id);
            this.start(id);
          },
          Math.min(60000, 1000 * 2 ** (times.length - 1)),
        );
        this.timers.set(id, t);
      }
    };
    child.on("error", (e) => crashed(e.message));
    child.on("exit", () => crashed());
  }
  async stop(id: string) {
    const t = this.timers.get(id);
    if (t) clearTimeout(t);
    this.timers.delete(id);
    const c = this.children.get(id);
    this.children.delete(id);
    if (!c) return;
    await new Promise<void>((resolve) => {
      let timer: NodeJS.Timeout;
      const done = () => {
        clearTimeout(timer);
        resolve();
      };
      c.once("close", done);
      try {
        process.kill(-c.pid!, "SIGTERM");
      } catch {
        c.kill("SIGTERM");
      }
      timer = setTimeout(() => {
        try {
          process.kill(-c.pid!, "SIGKILL");
        } catch {}
        resolve();
      }, 10000);
    });
  }
  async close() {
    this.closing = true;
    await Promise.all(
      [...this.children.keys(), ...this.timers.keys()].map((id) =>
        this.stop(id),
      ),
    );
  }
}
