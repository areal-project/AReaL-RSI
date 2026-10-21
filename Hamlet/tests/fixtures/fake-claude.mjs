#!/usr/bin/env node
import { checkCLI } from "./hamlet-cli-check.mjs";
let input = "";
for await (const c of process.stdin) input += c;
const args = process.argv.slice(2),
  session = args.includes("--resume")
    ? args[args.indexOf("--resume") + 1]
    : "claude-session-" + process.pid;
console.log(
  JSON.stringify({ type: "system", subtype: "init", session_id: session }),
);
console.log(
  JSON.stringify({
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          name: "Bash",
          input: { command: "hamlet call whoami" },
        },
      ],
    },
  }),
);
console.log(
  JSON.stringify({
    type: "user",
    message: {
      content: [
        { type: "tool_result", content: JSON.stringify(await checkCLI(input)) },
      ],
    },
  }),
);
console.log(
  JSON.stringify({
    type: "result",
    is_error: false,
    result: input.includes("followup") ? "CLAUDE_RESUMED" : "CLAUDE_STARTED",
    usage: { input_tokens: 2, output_tokens: 1 },
  }),
);
