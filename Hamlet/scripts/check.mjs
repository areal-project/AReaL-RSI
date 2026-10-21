import { execFileSync } from "node:child_process";
// Use the invoking package manager, including `corepack pnpm` without global shims.
const manager = process.env.npm_execpath;
if (!manager)
  throw new Error("Run this check with corepack pnpm check or npm run check");
for (const task of ["typecheck", "build", "test", "test:python", "test:ui"])
  execFileSync(process.execPath, [manager, "run", task], { stdio: "inherit" });
