import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDaemon } from "../packages/daemon/dist/index.js";
const dir = mkdtempSync(join(tmpdir(), "detached-live-"));
const d = await createDaemon({ dir });
async function wait(id) {
  const start = Date.now();
  while (Date.now() - start < 180000) {
    const m = d.service.store.get("messages", id);
    if (["done", "failed"].includes(m.status)) {
      const r = d.service.store.get("runs", m.run_id);
      if (m.status !== "done")
        throw Error(JSON.stringify({ message: m, run: r }));
      return r;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw Error("Codex smoke timeout");
}
try {
  await d.listen();
  const s = d.service,
    a = { kind: "owner" },
    g = await s.call("group_create", { name: "Live smoke" }, a),
    l = await s.call(
      "agent_create",
      {
        group: g.id,
        role_name: "Smoke",
        workdir: join(dir, "workspace"),
        worktree: false,
        run_mode: "resident",
        harness_options: { model_reasoning_effort: "low" },
        system_prompt_tpl:
          "You are a bounded integration test. Do not edit files. Use the Hamlet CLI whoami command with the existing environment when asked; otherwise use no tools. Reply briefly.",
      },
      a,
    );
  const m = s.ring(a, {
    target: l.id,
    prompt:
      "Run the Hamlet CLI whoami command once using the provided HAMLET_NODE and HAMLET_CLI environment variables. Do not print credentials. Then reply HAMLET_SMOKE_FORK followed by your instance ID.",
  });
  const r = await wait(m.id);
  console.log(
    JSON.stringify({
      kind: r.kind,
      status: r.status,
      output: r.final_output,
      usage: r.usage,
    }),
  );
  if (!r.final_output.includes("HAMLET_SMOKE_FORK"))
    throw Error("Fork marker missing");
  const next = s.ring(a, {
    target: l.id,
    prompt:
      "What marker did you output in the previous turn? Include that marker and HAMLET_SMOKE_RESUME. No tools.",
  });
  const rr = await wait(next.id);
  console.log(
    JSON.stringify({
      kind: rr.kind,
      status: rr.status,
      output: rr.final_output,
      usage: rr.usage,
    }),
  );
  if (
    !rr.final_output.includes("HAMLET_SMOKE_FORK") ||
    !rr.final_output.includes("HAMLET_SMOKE_RESUME")
  )
    throw Error("Resume context missing");
  const e = await s.call("run_events", { id: r.id }, a);
  if (
    !e.some(
      (x) => x.type === "command" && String(x.command).includes("HAMLET_CLI"),
    )
  )
    throw Error("No Hamlet CLI command observed");
  console.log("Live Codex fork/resume + restricted CLI: PASS");
} finally {
  await d.close();
  rmSync(dir, { recursive: true, force: true });
}
