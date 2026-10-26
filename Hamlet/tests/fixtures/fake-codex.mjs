#!/usr/bin/env node
import { readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { fakeAppServer } from "./fake-app-server.mjs";
import { checkCLI } from "./hamlet-cli-check.mjs";
import { join } from "node:path";
if (process.argv[2] === "app-server") {
  await fakeAppServer();
  process.exit(0);
}
if (process.argv[2] !== "exec") throw new Error("unsupported fixture command");
let input = "";
for await (const c of process.stdin) input += c;
if (process.env.FAKE_INVOCATIONS)
  appendFileSync(
    process.env.FAKE_INVOCATIONS,
    JSON.stringify({
      args: process.argv.slice(2),
      prompt: input,
      cwd: process.cwd(),
      token: !!process.env.HAMLET_TOKEN,
    }) + "\n",
  );
if (input.includes("NO_SESSION")) {
  console.error("session unavailable");
  process.exit(1);
}
const resume = process.argv.includes("resume");
const session = resume
  ? process.argv[process.argv.indexOf("resume") + 1]
  : "session-" + process.pid;
console.log(JSON.stringify({ type: "thread.started", thread_id: session }));
console.log(
  JSON.stringify({
    type: "item.started",
    item: { type: "command_execution", command: "example" },
  }),
);
if (input.includes("WAIT")) await new Promise((r) => setTimeout(r, 30000));
if (process.env.FAKE_EDIT)
  writeFileSync(join(process.cwd(), "result.txt"), input);
const identity = await checkCLI(input);
console.log(
  JSON.stringify({
    type: "item.completed",
    item: {
      type: "command_execution",
      command: "hamlet call whoami '{}'",
      aggregated_output: JSON.stringify(identity),
      exit_code: 0,
    },
  }),
);
console.log(
  JSON.stringify({
    type: "item.completed",
    item: { type: "agent_message", text: resume ? "RESUMED" : "STARTED" },
  }),
);
console.log(
  JSON.stringify({
    type: "turn.completed",
    usage: { input_tokens: 12, output_tokens: 3 },
  }),
);
if (input.includes("FAIL")) {
  console.error("simulated failure");
  process.exit(1);
}
