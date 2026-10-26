import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { appendFileSync } from "node:fs";
const exec = promisify(execFile);
export async function checkCLI(input) {
  const request = async (name, params) => {
    try {
      const r = await exec(
        process.env.HAMLET_NODE,
        [process.env.HAMLET_CLI, "call", name, JSON.stringify(params)],
        { timeout: 10000 },
      );
      return { ok: true, value: JSON.parse(r.stdout) };
    } catch (e) {
      return { ok: false, error: JSON.parse(e.stderr) };
    }
  };
  const me = await request("whoami", {});
  if (!me.ok) throw Error(JSON.stringify(me));
  if (input.includes("CHECK_CLI")) {
    const report = {
      me,
      members: await request("group_agents", {}),
      own: await request("instance_get", { id: me.value.id }),
      owner: await request("group_create", { name: "must-deny" }),
      escalation: await request("agent_create", {
        group: me.value.group,
        role_name: "must-deny",
        identity: "admin",
        workdir: process.cwd(),
        worktree: false,
      }),
      other: await request("instance_get", {
        id: process.env.FAKE_OTHER_INSTANCE,
      }),
      crossMessage: await request("message_to", {
        target: process.env.FAKE_OTHER_INSTANCE,
        prompt: "must-deny",
      }),
      inheritedDoorbell: !!process.env.DOORBELL_TOKEN,
      notification: await request("message_to", {
        target: process.env.FAKE_NOTIFY_TARGET,
        prompt: "CLI_NOTICE",
        dedup_key: `check-${me.value.id}-${input.includes("followup") ? 2 : 1}`,
      }),
    };
    report.manage = await request("agent_create", {
      group: me.value.group,
      role_name: "cli-managed-worker",
      identity: "worker",
      harness: process.env.FAKE_TEST_HARNESS,
      model: null,
      harness_options: {},
      run_mode: "fork",
      workdir: process.cwd(),
      worktree: false,
      prompt_tpl: "",
      system_prompt_tpl: "",
      extra_env: {},
      doorbell_script: "",
      doorbell_enabled: false,
    });
    if (report.manage.ok)
      report.cleanup = await request("agent_delete", {
        id: report.manage.value.id,
      });
    appendFileSync(process.env.FAKE_CLI_REPORT, JSON.stringify(report) + "\n");
  }
  return me.value;
}
