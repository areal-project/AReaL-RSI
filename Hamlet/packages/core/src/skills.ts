import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { join, resolve, relative, sep } from "node:path";
import { AppError } from "./types.js";
import { git } from "./workdirs.js";

export function agentSkillPath(cwd: string, harness: string) {
  return join(
    cwd,
    harness === "claude" ? ".claude" : ".agents",
    "skills",
    "hamlet-agent",
  );
}
// Only Hamlet-owned skill directories may be refreshed. Never follow a project symlink.
export function installAgentSkill(
  source: string,
  cwd: string,
  harness: string,
) {
  if (!existsSync(join(source, "SKILL.md")))
    throw new AppError("Agent skill 源目录不存在");
  const target = agentSkillPath(cwd, harness);
  let path = resolve(cwd);
  for (const part of target.slice(path.length + 1).split(sep)) {
    path = join(path, part);
    let symbolic = false;
    try {
      symbolic = lstatSync(path).isSymbolicLink();
    } catch (e: any) {
      if (e.code !== "ENOENT") throw e;
    }
    if (symbolic)
      throw new AppError("Agent skill 安装路径含符号链接，未覆盖", 409);
  }
  const marker = join(target, ".hamlet-managed");
  if (existsSync(target)) {
    const check = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.isSymbolicLink())
          throw new AppError("Agent skill 目录含符号链接，未覆盖", 409);
        if (entry.isDirectory()) check(join(dir, entry.name));
      }
    };
    check(target);
    if (
      !existsSync(marker) ||
      readFileSync(marker, "utf8") !== "hamlet-agent\n"
    )
      throw new AppError(
        "Agent skill 目录已存在且不由 Hamlet 管理，未覆盖",
        409,
      );
  }
  mkdirSync(target, { recursive: true });
  cpSync(source, target, { recursive: true });
  writeFileSync(marker, "hamlet-agent\n");
  // Generated runtime support belongs to the local checkout, not task commits.
  let exclude: string, gitRoot: string;
  try {
    gitRoot = git(cwd, "rev-parse", "--show-toplevel");
    exclude = git(
      cwd,
      "rev-parse",
      "--path-format=absolute",
      "--git-path",
      "info/exclude",
    );
  } catch {
    return target;
  } // Non-Git workspace.
  const relativePath = relative(gitRoot, target).split(sep).join("/");
  const rule = "/" + relativePath.replace(/[\\*?![\] ]/g, "\\$&") + "/";
  const existing = existsSync(exclude) ? readFileSync(exclude, "utf8") : "";
  if (!existing.split("\n").includes(rule)) {
    mkdirSync(resolve(exclude, ".."), { recursive: true });
    writeFileSync(
      exclude,
      existing + (existing.endsWith("\n") ? "" : "\n") + rule + "\n",
    );
  }
  return target;
}
