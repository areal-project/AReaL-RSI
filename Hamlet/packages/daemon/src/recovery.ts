import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import type { Service } from "../../core/src/index.js";
// A PID alone is insufficient evidence after a crash: match the signed identity
// environment before signalling an orphan, to avoid killing a reused PID.
export async function recoverOrphans(service: Service) {
  const candidates = [
    ...service.store
      .all("runs")
      .filter((r) => r.status === "running" && r.pid)
      .map((r) => ({
        pid: r.pid,
        markers: [`HAMLET_RUN_ID=${r.id}`, `DETACHED_RUN_ID=${r.id}`],
      })),
    ...service.store
      .all("doorbells")
      .filter((d) => d.pid)
      .map((d) => ({
        pid: d.pid,
        markers: [
          `DOORBELL_TOKEN=${service.auth.sign({ kind: "doorbell", id: d.logical_id })}`,
        ],
      })),
  ];
  await Promise.all(
    candidates.map(async ({ pid, markers }) => {
      let environment = "";
      try {
        environment =
          process.platform === "linux"
            ? readFileSync(`/proc/${pid}/environ`, "utf8").replaceAll(
                "\0",
                "\n",
              )
            : execFileSync("ps", ["eww", "-p", String(pid), "-o", "command="], {
                encoding: "utf8",
              });
      } catch {
        return;
      }
      if (!markers.some((marker) => environment.includes(marker))) return;
      const kill = (signal: NodeJS.Signals) => {
        try {
          process.kill(-pid, signal);
        } catch {}
      };
      kill("SIGTERM");
      const start = Date.now();
      while (Date.now() - start < 10000) {
        if (process.platform === "linux") {
          try {
            if (
              readFileSync(`/proc/${pid}/stat`, "utf8")
                .split(") ")[1]
                ?.startsWith("Z")
            )
              return;
          } catch {
            return;
          }
        }
        try {
          process.kill(pid, 0);
        } catch {
          return;
        }
        await new Promise((r) => setTimeout(r, 50));
      }
      kill("SIGKILL");
    }),
  );
}
