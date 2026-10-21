import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import type { Row } from "./types.js";
const schemas: Record<string, string> = {
  groups:
    "id TEXT PRIMARY KEY, name TEXT NOT NULL, paused INTEGER DEFAULT 0, created_at TEXT",
  logical_agents:
    "id TEXT PRIMARY KEY, group_id TEXT, role_name TEXT, identity TEXT, harness TEXT, model TEXT, harness_options TEXT, extra_env TEXT, system_prompt_tpl TEXT, prompt_tpl TEXT, run_mode TEXT, resident_instance_id TEXT, workdir_id TEXT, doorbell_script TEXT, doorbell_enabled INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, deleted_at TEXT",
  workdirs:
    "id TEXT PRIMARY KEY, root TEXT UNIQUE, mode TEXT, main_path TEXT, base_path TEXT, shared_path TEXT, base_ref TEXT",
  instances:
    "id TEXT PRIMARY KEY, logical_id TEXT, seq INTEGER, harness_session_id TEXT, worktree_path TEXT, branch TEXT, status TEXT, config_snapshot TEXT, created_at TEXT, last_active_at TEXT, archived_at TEXT",
  messages:
    "id TEXT PRIMARY KEY, target_id TEXT, from_id TEXT, from_role TEXT, source TEXT, prompt TEXT, system_prompt TEXT, vars TEXT, dedup_key TEXT, status TEXT, resolved_instance_id TEXT, run_id TEXT, error TEXT, created_at TEXT, dedup_until TEXT, force_fork INTEGER DEFAULT 0, queued_at TEXT",
  runs: "id TEXT PRIMARY KEY, instance_id TEXT, message_id TEXT, kind TEXT, status TEXT, started_at TEXT, ended_at TEXT, final_output TEXT, usage TEXT, events_path TEXT, prompt TEXT, system_prompt TEXT, error TEXT, pid INTEGER, trigger_snapshot TEXT, queue_wait_ms INTEGER",
  doorbells:
    "logical_id TEXT PRIMARY KEY, pid INTEGER, status TEXT, restart_count INTEGER DEFAULT 0, last_ring_at TEXT, log_path TEXT, error TEXT",
};
const json = new Set([
  "harness_options",
  "extra_env",
  "config_snapshot",
  "vars",
  "usage",
  "trigger_snapshot",
]);
export class Store {
  db: Database.Database;
  constructor(public dir: string) {
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    this.db = new Database(join(dir, "state.db"));
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("busy_timeout = 5000");
    for (const [t, s] of Object.entries(schemas))
      this.db.exec(`CREATE TABLE IF NOT EXISTS ${t} (${s})`);
    if (
      !(this.db.pragma("table_info(messages)") as { name: string }[]).some(
        (c) => c.name === "force_fork",
      )
    )
      this.db.exec(
        "ALTER TABLE messages ADD COLUMN force_fork INTEGER DEFAULT 0",
      );
    for (const [table, columns] of Object.entries({
      messages: { queued_at: "TEXT" },
      runs: { trigger_snapshot: "TEXT", queue_wait_ms: "INTEGER" },
    })) {
      const present = new Set(
        (this.db.pragma(`table_info(${table})`) as { name: string }[]).map(
          (c) => c.name,
        ),
      );
      for (const [name, type] of Object.entries(columns))
        if (!present.has(name))
          this.db.exec(`ALTER TABLE ${table} ADD COLUMN ${name} ${type}`);
    }
    this.db.exec(
      "CREATE UNIQUE INDEX IF NOT EXISTS roles ON logical_agents(group_id, role_name) WHERE deleted_at IS NULL; CREATE INDEX IF NOT EXISTS message_queue ON messages(status,created_at); CREATE INDEX IF NOT EXISTS message_dedup ON messages(target_id,dedup_key,dedup_until); CREATE UNIQUE INDEX IF NOT EXISTS instance_seq ON instances(logical_id,seq)",
    );
  }
  private table(t: string) {
    if (!(t in schemas)) throw Error("Invalid table");
    return t;
  }
  all(t: string): Row[] {
    return (
      this.db
        .prepare(`SELECT * FROM ${this.table(t)} ORDER BY rowid`)
        .all() as Row[]
    ).map((r) => this.decode(r));
  }
  get(t: string, id: string): Row | undefined {
    const r = this.db
      .prepare(
        `SELECT * FROM ${this.table(t)} WHERE ${t === "doorbells" ? "logical_id" : "id"}=?`,
      )
      .get(id) as Row | undefined;
    return r && this.decode(r);
  }
  insert(t: string, row: Row) {
    const keys = Object.keys(row);
    this.db
      .prepare(
        `INSERT INTO ${this.table(t)} (${keys.join(",")}) VALUES (${keys.map(() => "?").join(",")})`,
      )
      .run(...keys.map((k) => this.encode(k, row[k])));
    return row;
  }
  update(t: string, id: string, row: Row) {
    const allowed = schemas[this.table(t)]
      .split(",")
      .map((x) => x.trim().split(" ")[0]);
    const keys = Object.keys(row).filter(
      (k) => allowed.includes(k) && k !== "id" && k !== "logical_id",
    );
    if (!keys.length) return;
    this.db
      .prepare(
        `UPDATE ${t} SET ${keys.map((k) => `${k}=?`).join(",")} WHERE ${t === "doorbells" ? "logical_id" : "id"}=?`,
      )
      .run(...keys.map((k) => this.encode(k, row[k])), id);
  }
  private encode(k: string, v: any) {
    return v == null
      ? null
      : json.has(k)
        ? JSON.stringify(v)
        : typeof v === "boolean"
          ? Number(v)
          : v;
  }
  private decode(row: Row) {
    for (const k of Object.keys(row))
      if (json.has(k)) row[k] = row[k] ? JSON.parse(row[k]) : {};
    return row;
  }
  close() {
    this.db.close();
  }
}
