import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
  renameSync,
  cpSync,
  rmSync,
  lstatSync,
  realpathSync,
  readlinkSync,
  symlinkSync,
  unlinkSync,
} from "node:fs";
import { join, resolve, dirname, relative, isAbsolute } from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { AppError, type Row } from "./types.js";
import { workdirMetadataDir } from "./runtime.js";
export function git(cwd: string, ...args: string[]): string {
  return execFileSync(
    "git",
    ["-c", "user.name=Hamlet", "-c", "user.email=hamlet@localhost", ...args],
    { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  ).trim();
}
function copy(src: string, dst: string) {
  mkdirSync(dst, { recursive: true });
  const args =
    process.platform === "darwin"
      ? ["-cR", src + "/.", dst]
      : ["-a", "--reflink=auto", src + "/.", dst];
  const r = spawnSync("cp", args);
  if (r.status !== 0) cpSync(src, dst, { recursive: true });
}
export function canonical(path: string) {
  let p = resolve(path),
    tail: string[] = [];
  while (!existsSync(p)) {
    tail.unshift(p.split("/").pop()!);
    p = dirname(p);
  }
  return resolve(realpathSync(p), ...tail);
}
export function setupWorkdir(input: Row): Row {
  if (typeof input.workdir !== "string" || !isAbsolute(input.workdir))
    throw new AppError("workdir 必须是绝对路径");
  const root = canonical(input.workdir);
  if (input.worktree === false)
    return { id: randomUUID(), root, mode: "direct" };
  const marker = join(workdirMetadataDir(root), "workdir.json");
  if (existsSync(marker)) return JSON.parse(readFileSync(marker, "utf8"));
  const entries = existsSync(root) ? readdirSync(root) : [];
  if (entries.length && input.migrate !== true)
    throw new AppError("needs_migration", 409, {
      needs_migration: true,
      root,
      entries: entries.slice(0, 30),
      count: entries.length,
    });
  mkdirSync(root, { recursive: true });
  if (entries.length) {
    const stage = join(root, ".migration-" + randomUUID());
    mkdirSync(stage);
    const moved: string[] = [];
    try {
      for (const e of entries) {
        renameSync(join(root, e), join(stage, e));
        moved.push(e);
      }
      renameSync(stage, join(root, "main"));
    } catch (e) {
      for (const name of moved.reverse())
        renameSync(join(stage, name), join(root, name));
      rmSync(stage, { recursive: true, force: true });
      throw e;
    }
  } else mkdirSync(join(root, "main"));
  const main_path = join(root, "main"),
    base_path = join(root, "base"),
    shared_path = join(root, "shared");
  if (!entries.length && input.git_init !== false) {
    git(main_path, "init", "-b", "main");
    git(
      main_path,
      "commit",
      "--allow-empty",
      "-m",
      "Initialize Hamlet workspace",
    );
  }
  const mode = existsSync(join(main_path, ".git")) ? "git" : "plain";
  if (mode === "git") {
    try {
      git(main_path, "rev-parse", "HEAD");
    } catch {
      git(
        main_path,
        "commit",
        "--allow-empty",
        "-m",
        "Initialize Hamlet workspace",
      );
    }
  }
  mkdirSync(join(root, "worktrees"), { recursive: true });
  mkdirSync(shared_path, { recursive: true });
  if (mode === "plain") copy(main_path, base_path);
  const row = {
    id: randomUUID(),
    root,
    mode,
    main_path,
    base_path: mode === "plain" ? base_path : null,
    shared_path,
    base_ref: input.base_ref || null,
  };
  mkdirSync(dirname(marker), { recursive: true });
  writeFileSync(marker, JSON.stringify(row, null, 2));
  return row;
}
export function prepare(w: Row, id: string) {
  if (w.mode === "direct") {
    mkdirSync(w.root, { recursive: true });
    return w.root;
  }
  const path = join(w.root, "worktrees", id);
  if (existsSync(path)) throw new AppError("目标 worktree 目录已存在，未覆盖");
  if (w.mode === "git" && git(w.main_path, "branch", "--list", `agent/${id}`))
    throw new AppError("目标 Agent 分支已存在，未覆盖");
  try {
    if (w.mode === "git")
      git(
        w.main_path,
        "worktree",
        "add",
        "-b",
        `agent/${id}`,
        path,
        w.base_ref || "HEAD",
      );
    else {
      copy(w.base_path, path);
      copy(w.base_path, join(workdirMetadataDir(w.root), "bases", id));
    }
    return path;
  } catch (e) {
    rmSync(path, { recursive: true, force: true });
    rmSync(join(workdirMetadataDir(w.root), "bases", id), {
      recursive: true,
      force: true,
    });
    if (w.mode === "git") {
      try {
        git(w.main_path, "worktree", "prune");
        git(w.main_path, "branch", "-D", `agent/${id}`);
      } catch {}
    }
    throw e;
  }
}
export function commit(w: Row, i: Row, run: string) {
  if (w.mode === "git" && git(i.worktree_path, "status", "--porcelain")) {
    git(i.worktree_path, "add", "-A");
    git(i.worktree_path, "commit", "-m", `Hamlet run ${run}`);
  }
}
export function cleanup(w: Row, i: Row) {
  if (w.mode === "git" && existsSync(i.worktree_path))
    git(w.main_path, "worktree", "remove", "--force", i.worktree_path);
  else if (w.mode === "plain") {
    rmSync(i.worktree_path, { recursive: true, force: true });
    rmSync(join(workdirMetadataDir(w.root), "bases", i.id), {
      recursive: true,
      force: true,
    });
  }
}
function files(root: string, prefix = ""): string[] {
  if (!existsSync(root)) return [];
  return readdirSync(join(root, prefix)).flatMap((n) => {
    const p = join(prefix, n),
      s = lstatSync(join(root, p));
    return s.isDirectory() ? files(root, p) : [p];
  });
}
function stat(path: string) {
  try {
    return lstatSync(path);
  } catch (e: any) {
    if (["ENOENT", "ENOTDIR"].includes(e.code)) return null;
    throw e;
  }
}
function hash(path: string) {
  const s = stat(path);
  if (!s || s.isDirectory()) return null;
  if (s.isSymbolicLink()) return "symlink:" + readlinkSync(path);
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}
function safe(root: string, file: string) {
  const p = resolve(root, file);
  if (
    !file ||
    relative(root, p).startsWith("..") ||
    isAbsolute(relative(root, p))
  )
    throw new AppError("无效文件路径");
  let d = dirname(p);
  while (d !== root) {
    if (stat(d)?.isSymbolicLink()) throw new AppError("路径包含符号链接");
    d = dirname(d);
  }
  return p;
}
export function diff(w: Row, i: Row): Row {
  if (w.mode === "direct") return { mode: "direct", files: [], diff: "" };
  if (w.mode === "git") {
    const branch = i.branch;
    const changes = git(w.main_path, "diff", "--name-only", "HEAD", branch);
    return {
      mode: "git",
      files: changes
        .split("\n")
        .filter(Boolean)
        .map((path) => ({ path })),
      diff: git(w.main_path, "diff", "HEAD", branch),
      dirty_main: !!git(w.main_path, "status", "--porcelain"),
      uncommitted:
        !i.archived_at && !!git(i.worktree_path, "status", "--porcelain"),
      conflicts: git(w.main_path, "diff", "--name-only", "--diff-filter=U")
        .split("\n")
        .filter(Boolean),
    };
  }
  if (i.archived_at) throw new AppError("已归档的非 git worktree 已清理");
  const paths = [
    ...new Set([...files(w.base_path), ...files(i.worktree_path)]),
  ];
  const changed = paths.filter(
    (p) => hash(safe(w.base_path, p)) !== hash(safe(i.worktree_path, p)),
  );
  return {
    mode: "plain",
    files: changed.map((path) => {
      const base = safe(w.base_path, path),
        work = safe(i.worktree_path, path),
        main = safe(w.main_path, path);
      const baseStat = stat(base),
        workStat = stat(work);
      if (baseStat?.isSymbolicLink() || workStat?.isSymbolicLink())
        return {
          path,
          conflict: hash(main) !== hash(base),
          deleted: !workStat,
          diff: `符号链接: ${baseStat?.isSymbolicLink() ? readlinkSync(base) : "(无链接)"} → ${workStat?.isSymbolicLink() ? readlinkSync(work) : "(无链接)"}`,
        };
      const r = spawnSync(
        "diff",
        [
          "-u",
          existsSync(base) ? base : "/dev/null",
          existsSync(work) ? work : "/dev/null",
        ],
        { encoding: "utf8" },
      );
      return {
        path,
        conflict: hash(main) !== hash(base),
        deleted: !existsSync(work),
        diff: r.stdout || `文件${existsSync(work) ? "新增" : "删除"}: ${path}`,
        content: existsSync(work) ? readFileSync(work, "utf8") : null,
      };
    }),
  };
}
export function merge(w: Row, i: Row, opts: Row) {
  if (w.mode === "git") {
    try {
      return { output: git(w.main_path, "merge", "--no-edit", i.branch) };
    } catch (e) {
      throw new AppError("合并失败，请在 main 中解决冲突", 409, {
        conflicts: git(w.main_path, "diff", "--name-only", "--diff-filter=U")
          .split("\n")
          .filter(Boolean),
        error: String(e),
      });
    }
  }
  if (w.mode !== "plain")
    throw new AppError("direct 模式没有可合并的 worktree");
  const d = diff(w, i);
  const selected = opts.files as string[];
  if (!Array.isArray(selected)) throw new AppError("请选择文件");
  for (const file of selected) {
    const f = d.files.find((x: Row) => x.path === file);
    if (!f) throw new AppError("文件不在 diff 中");
    if (f.conflict && opts.confirm_overwrite !== true)
      throw new AppError("文件有冲突，需确认覆盖", 409, { file });
  }
  for (const file of selected) {
    const src = safe(i.worktree_path, file),
      dst = safe(w.main_path, file);
    if (stat(src)) {
      mkdirSync(dirname(dst), { recursive: true });
      if (stat(dst)?.isSymbolicLink()) unlinkSync(dst);
      if (stat(src)?.isSymbolicLink()) {
        rmSync(dst, { force: true });
        symlinkSync(readlinkSync(src), dst);
      } else cpSync(src, dst);
    } else if (stat(dst)?.isSymbolicLink()) unlinkSync(dst);
    else rmSync(dst, { force: true });
  }
  return { applied: selected };
}
export function refreshBase(w: Row) {
  if (w.mode !== "plain") throw new AppError("仅非 git 模式支持刷新 base");
  rmSync(w.base_path, { recursive: true, force: true });
  copy(w.main_path, w.base_path);
}
