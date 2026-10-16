#!/usr/bin/env python3
"""Generic, business-neutral scenario validator for task-like state packages.

Run from a workspace with an optional implementation under package/. The
self-test is always runnable and uses a synthetic in-memory store.
"""
from __future__ import annotations
import argparse, importlib.util, json, pathlib, tempfile

class SyntheticStore:
    def __init__(self, payload=None):
        self.rows = [] if payload is None else [dict(x) for x in payload]
        self.next_id = max([int(r["id"].split("-")[-1]) for r in self.rows if str(r.get("id", "")).startswith("id-") and str(r["id"]).split("-")[-1].isdigit()] or [0]) + 1
    def add(self, title):
        title = title.strip()
        if not title: return None
        row = {"id": "id-" + str(self.next_id), "title": title, "completed": False}
        self.next_id += 1
        self.rows.append(row); return row["id"]
    def toggle(self, ident):
        self._row(ident)["completed"] = not self._row(ident)["completed"]
    def edit(self, ident, title):
        title = title.strip()
        if not title: return False
        self._row(ident)["title"] = title; return True
    def delete(self, ident): self.rows = [r for r in self.rows if r["id"] != ident]
    def filter(self, kind):
        return [r for r in self.rows if kind == "all" or (kind == "active" and not r["completed"]) or (kind == "completed" and r["completed"])]
    def dump(self): return json.loads(json.dumps(self.rows))
    def _row(self, ident): return next(r for r in self.rows if r["id"] == ident)

def load_store(package_dir):
    path = pathlib.Path(package_dir) / "task_state.py"
    if not path.exists(): return SyntheticStore()
    spec = importlib.util.spec_from_file_location("candidate_task_state", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.TaskStore()

def run(store):
    checks = []
    def check(name, condition): checks.append({"name": name, "passed": bool(condition)})
    a, b = store.add(" Alpha "), store.add("Beta")
    check("trimmed_add", store.rows[0]["title"] == "Alpha")
    before = len(store.rows); check("reject_empty", store.add("  	") is None and len(store.rows) == before)
    store.toggle(a); check("toggle_is_scoped", store._row(a)["completed"] and not store._row(b)["completed"])
    check("filter_active", [r["id"] for r in store.filter("active")] == [b])
    check("filter_completed", [r["id"] for r in store.filter("completed")] == [a])
    check("edit_is_scoped", store.edit(b, "Beta revised") and store._row(a)["title"] == "Alpha" and store._row(b)["title"] == "Beta revised")
    snapshot = store.dump(); store2 = store.__class__(snapshot)
    check("round_trip", store2.dump() == snapshot)
    store.delete(a); check("delete", len(store.rows) == 1 and store.rows[0]["id"] == b)
    # Synthetic text-safety invariant: values remain data, not executable markup.
    x = store.add("<b>literal</b>"); check("literal_text_data", store._row(x)["title"] == "<b>literal</b>")
    return checks

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--self-test", action="store_true"); ap.add_argument("--package-dir", default="package")
    args = ap.parse_args(); checks = run(load_store(args.package_dir)); failed = [c for c in checks if not c["passed"]]
    print(json.dumps({"checks": checks, "passed": not failed}, indent=2))
    return 1 if failed else 0
if __name__ == "__main__": raise SystemExit(main())
