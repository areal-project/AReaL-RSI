import { createInterface } from "node:readline";
import { execFile } from "node:child_process";
export async function fakeAppServer() {
  const lines = createInterface({ input: process.stdin });
  const reply = (id, result) => console.log(JSON.stringify({ id, result }));
  for await (const line of lines) {
    const r = JSON.parse(line);
    if (r.method === "initialize") reply(r.id, {});
    if (r.method === "command/exec" && process.env.FAKE_APP_SERVER_ERROR) {
      console.error(process.env.FAKE_APP_SERVER_ERROR);
      console.log(
        JSON.stringify({
          id: r.id,
          error: { code: -32603, message: process.env.FAKE_APP_SERVER_ERROR },
        }),
      );
      continue;
    }
    if (r.method === "command/exec") {
      execFile(
        r.params.command[0],
        r.params.command.slice(1),
        { cwd: r.params.cwd, env: process.env },
        (error, stdout, stderr) =>
          reply(r.id, { exitCode: error ? 1 : 0, stdout, stderr }),
      );
    }
  }
}
