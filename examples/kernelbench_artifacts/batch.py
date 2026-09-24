"""Launch a reusable experiment matrix on an existing workbench."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from demo import prepare

from areal_rsi.base.storage import new_id, now, safe_name, write_json
from areal_rsi.coordination.collaboration import build_plan, manifest, presets
from areal_rsi.coordination.config import config_from_dict
from areal_rsi.experiment_config import environment_scope
from areal_rsi.researcher_profiles import load_profiles
from areal_rsi.ui.launch import WorkbenchClient

HERE = Path(__file__).resolve().parent


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def prepare_trial(item: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    batch, row = item
    root = Path(batch["data_root"])
    path = Path(row["config_file"])
    if path.exists():
        return {"run_id": row["run_id"], "prepared": True}
    base = config_from_dict(read(root / "runtime/workbench-defaults.json"))
    with environment_scope(
        {
            "RSI_BACKEND": "codex",
            "RSI_BASE_URL": base.base_url,
            "RSI_MODEL": row["model"],
            "RSI_REASONING_EFFORT": batch["reasoning_effort"],
        }
    ):
        config = prepare(
            root,
            Path(batch["environment_file"]),
            row["run_id"],
            task=row["task"],
            name=row["name"],
            check_credentials=False,
            max_agents=batch["max_agents"],
            max_wall_seconds=None,
            max_effective_seconds=batch["max_effective_seconds"],
            collaboration_modules=tuple(row["modules"]),
            web_research=True,
            execution_interface=batch["execution_interface"],
        )
    config["credential_file"] = base.credential_file
    config["api_key_env"] = base.api_key_env
    config["brief"]["objective"] = row["name"]
    if "profile_evolution" in row["modules"]:
        config["profile_library_dir"] = str(root / "profile-library")
    elif "researcher_profiles" in row["modules"]:
        config["metadata"]["researcher_profiles"] = load_profiles(
            ("general", "kernelbench/algorithm", "kernelbench/memory", "kernelbench/execution")
        )
        if "profile_resources" in row["modules"]:
            from areal_rsi.coordination.profile_resources import load_resources

            general = (
                Path(__file__).resolve().parents[2]
                / "src/areal_rsi/agents/research/resources/general"
            )
            for profile in config["metadata"]["researcher_profiles"]:
                directory = (
                    general
                    if profile["id"] == "general"
                    else HERE / "v0.2.1.4/profiles" / profile["id"].rsplit("/", 1)[-1]
                )
                profile.update(load_resources(directory))
        if row.get("researcher_skills"):
            if "researcher_skills" not in row["modules"]:
                raise ValueError("技能配置需要 researcher_skills 模块")
            from areal_rsi.coordination.profile_resources import load_resources

            profiles = {p["id"]: p for p in config["metadata"]["researcher_profiles"]}
            for identifier, source in row["researcher_skills"].items():
                directory = HERE / source
                profiles[identifier]["resources"] = {
                    f"skills/{directory.name}/{name}": body
                    for name, body in load_resources(directory)["resources"].items()
                }
    write_json(path, config, immutable=True)
    return {"run_id": row["run_id"], "prepared": True}


def register_and_start(batch: dict[str, Any], client: WorkbenchClient) -> list[dict[str, str]]:
    root = Path(batch["data_root"])
    rows = batch["runs"]
    known = {r["run_id"] for r in client.request("/api/runs")["runs"]}
    for row in rows:
        if row["run_id"] not in known:
            client.request(
                "/api/runs",
                {
                    "run_id": row["run_id"],
                    "config": read(Path(row["config_file"])),
                },
            )
    members = [r["run_id"] for r in rows]
    records = [r for r in client.request("/api/runs")["runs"] if r["run_id"] in members]
    group = {
        "id": batch["batch_id"],
        "name": batch["group_name"],
        "run_ids": members,
        "created_at": min(r["created_at"] for r in records),
    }
    path = root / "workbench-groups.json"
    groups = read(path)["groups"] if path.exists() else []
    previous_group = next((g for g in groups if g.get("name") == group["name"]), None)
    if previous_group:
        group.update(
            id=previous_group["id"],
            created_at=previous_group["created_at"],
            run_ids=list(dict.fromkeys([*previous_group["run_ids"], *members])),
        )
    write_json(path, {"groups": [group, *(g for g in groups if g["id"] != group["id"])]})

    def start(row: dict[str, Any]) -> dict[str, str]:
        worker = WorkbenchClient(batch["port"])
        worker.token = client.token
        path = "/api/runs/" + row["run_id"]
        detail = worker.request(path)
        if detail["status"] == "planned":
            detail = worker.request(path + "/start", {})
        # A retry never restarts a stopped study or resets its budget.
        if detail["status"] == "running":
            frozen = read(root / "runs" / row["run_id"] / "coordination/collaboration.json")
            if frozen != row["collaboration"]:
                raise ValueError("运行中的协作配置与批次不一致")
        result = {
            "run_id": row["run_id"],
            "status": detail["status"],
            "url": worker.url + "/?run=" + row["run_id"],
        }
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=len(rows)) as pool:
        results = list(pool.map(start, rows))
    write_json(
        Path(batch["directory"]) / "launch-result.json",
        {
            "group": group,
            "runs": results,
            "launched_at": now(),
        },
    )
    return results


def watch(batch_file: Path) -> None:
    batch = read(batch_file)
    while True:
        states = [
            read(Path(batch["data_root"]) / "runs" / r["run_id"] / "coordination/state.json")
            for r in batch["runs"]
        ]
        if all(
            s.get("user_stopped")
            or (
                (
                    s["status"] in {"failed", "cancelled"}
                    or s.get("finalization", {}).get("status") in {"completed", "failed"}
                )
                and s.get("profile_evolution", {}).get("status")
                in {None, "completed", "conflict", "failed", "skipped"}
            )
            for s in states
        ):
            return
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(description="并行准备并启动一组 KernelBench 研究")
    parser.add_argument("recipe", nargs="?", type=Path, help="实验矩阵 JSON")
    parser.add_argument("--group", help="覆盖分组名称")
    parser.add_argument("--port", type=int, default=8198)
    parser.add_argument("--environment", type=Path, default=HERE / "et15.json")
    parser.add_argument("--resume", type=Path, help="重试 batch.json，不重复创建或重置研究")
    parser.add_argument(
        "--prepare-only", action="store_true", help="准备和校准后停止，用 --resume 启动"
    )
    parser.add_argument("--watch", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
        return
    if args.resume:
        batch = read(args.resume)
        args.port = batch["port"]
    elif not args.recipe:
        parser.error("需要实验矩阵或 --resume batch.json")
    client = WorkbenchClient(args.port)
    info = client.bootstrap()
    root = Path(info["data_root"]).resolve()
    config_from_dict(read(root / "runtime/workbench-defaults.json")).runtime()
    if args.resume:
        if str(root) != batch["data_root"]:
            raise ValueError("工作台数据目录与原批次不一致")
    else:
        recipe = read(args.recipe)
        choices = {p["id"]: p for p in presets()}
        batch_id = new_id("batch")
        directory = root / "operator" / batch_id
        batch = {
            "batch_id": batch_id,
            "directory": str(directory),
            "data_root": str(root),
            "port": args.port,
            "group_name": args.group or recipe["group_name"],
            "environment_file": str(directory / "environment.json"),
            "reasoning_effort": recipe.get("reasoning_effort", "xhigh"),
            "max_agents": recipe.get("max_agents", 4),
            "max_effective_seconds": recipe.get("max_effective_seconds", 3600),
            "execution_interface": recipe.get("execution_interface", "cli"),
            "created_at": now(),
            "runs": [],
        }
        for i, trial in enumerate(recipe["runs"], 1):
            modules = choices[trial["collaboration"]]["modules"]
            plan = build_plan("passk", tuple(modules))
            run_id = safe_name(f"research_{batch_id}_{i}_{trial['task'].lower()}")
            folder = directory / str(i)
            folder.mkdir(parents=True)
            (folder / "leader.md").write_text(plan.rsi_prompt, encoding="utf-8")
            (folder / "researcher.md").write_text(plan.research_prompt, encoding="utf-8")
            batch["runs"].append(
                {
                    **trial,
                    "run_id": run_id,
                    "modules": modules,
                    "name": f"{trial['task']} · {trial['model']} · {trial['label']}",
                    "config_file": str(folder / "config.json"),
                    "collaboration": manifest("passk", tuple(modules)),
                }
            )
        if not batch["runs"]:
            raise ValueError("实验矩阵不能为空")
        write_json(Path(batch["environment_file"]), read(args.environment), immutable=True)
        write_json(directory / "batch.json", batch, immutable=True)
    directory = Path(batch["directory"])
    batch_file = directory / "batch.json"
    for row in batch["runs"]:
        if manifest("passk", tuple(row["modules"])) != row["collaboration"]:
            raise ValueError("协作配置已变更，请恢复原版本或新建批次")
    print(f"批次：{batch_file}\n正在并行准备并校准 reference…", flush=True)
    with ProcessPoolExecutor(max_workers=len(batch["runs"])) as pool:
        results = list(pool.map(prepare_trial, [(batch, row) for row in batch["runs"]]))
    write_json(directory / "preparation.json", results)
    if args.prepare_only:
        print(f"准备完成：{batch_file}", flush=True)
        return
    results = register_and_start(batch, client)
    if sys.platform == "darwin" and any(r["status"] == "running" for r in results):
        with (directory / "sleep-guard.log").open("ab") as log:
            guard = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-i",
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--watch",
                    str(batch_file),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        write_json(directory / "sleep-guard.json", {"pid": guard.pid})


if __name__ == "__main__":
    main()
