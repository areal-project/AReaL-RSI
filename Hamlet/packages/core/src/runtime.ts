import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// The old names remain aliases so running installations and child sessions survive
// the product rename without a second database or daemon.
export function runtimeEnv(key: string, env: NodeJS.ProcessEnv = process.env) {
  return env[`HAMLET_${key}`] || env[`DETACHED_${key}`];
}
export function dataDir(
  env: NodeJS.ProcessEnv = process.env,
  home = homedir(),
) {
  const explicit = runtimeEnv("HOME", env);
  if (explicit) return explicit;
  const current = join(home, ".hamlet"),
    legacy = join(home, ".detached");
  if (existsSync(current)) return current;
  if (existsSync(legacy)) return legacy;
  return current;
}
export function workdirMetadataDir(root: string) {
  const current = join(root, ".hamlet"),
    legacy = join(root, ".detached");
  if (existsSync(join(current, "workdir.json"))) return current;
  if (existsSync(join(legacy, "workdir.json"))) return legacy;
  return current;
}
export function instanceEnvironment(url: string, token: string, id: string) {
  return {
    HAMLET_URL: url,
    HAMLET_TOKEN: token,
    HAMLET_INSTANCE_ID: id,
    DETACHED_URL: url,
    DETACHED_TOKEN: token,
    DETACHED_INSTANCE_ID: id,
  };
}

// Do not leak the launching host's Owner/instance/doorbell credentials into
// children. The daemon supplies the child's own identity after this step.
export function childEnvironment(
  extra: NodeJS.ProcessEnv = {},
  inherited: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const env = { ...inherited, ...extra };
  for (const key of Object.keys(env))
    if (
      /^(HAMLET|DETACHED)_(URL|TOKEN|INSTANCE_ID|RUN_ID)$/.test(key) ||
      /^DOORBELL_/.test(key)
    )
      delete env[key];
  return env;
}
