import { build } from "esbuild";
import { execFileSync } from "node:child_process";
for (const name of [
  "core",
  "agent-tools",
  "harness-codex",
  "harness-claude",
  "daemon",
  "cli",
]) {
  await build({
    entryPoints: [`packages/${name}/src/index.ts`],
    outfile: `packages/${name}/dist/index.js`,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node20",
    packages: "external",
    sourcemap: true,
  });
}
execFileSync(
  process.execPath,
  ["node_modules/vite/bin/vite.js", "build", "packages/ui"],
  { stdio: "inherit" },
);
