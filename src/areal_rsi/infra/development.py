"""Operator-owned development profiles; task implementations live outside the package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from areal_rsi.api.control import validate_record
from areal_rsi.api.execution import JobSpec
from areal_rsi.api.research import DevelopmentProfile, PreparationCheck
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.execution import JobManager
from areal_rsi.infra.storage.files import decode, encode, new_id, safe_name, write_json


def profile(root: Path, identifier: str) -> DevelopmentProfile:
    path = root / "development-profiles" / (safe_name(identifier) + ".json")
    value = json.loads(path.read_text())
    validate_record(DevelopmentProfile, value)
    result: DevelopmentProfile = decode(DevelopmentProfile, value)
    if result.profile_id != identifier or not result.prepare_command:
        raise ValueError("Invalid development profile")
    return result


def catalog(root: Path) -> list[dict[str, Any]]:
    return [
        {"profile_id": p.profile_id, "title": p.title, "description": p.description,
         "defaults": encode(p.defaults)}
        for path in sorted((root / "development-profiles").glob("*.json"))
        for p in [profile(root, path.stem)]
    ]


def default_profile(root: Path, config: ResearchConfig) -> DevelopmentProfile | None:
    """Deployment selects the workspace; never choose arbitrarily among multiple projects."""
    selected = config.metadata.get("development_profile_id")
    if selected is not None:
        if not isinstance(selected, str):
            raise ValueError("development_profile_id must be a string")
        return profile(root, selected)
    paths = list((root / "development-profiles").glob("*.json"))
    return profile(root, paths[0].stem) if len(paths) == 1 else None


def digest(config: ResearchConfig) -> str:
    return hashlib.sha256(json.dumps(config.public(), sort_keys=True).encode()).hexdigest()


def prepare(c: Coordinator, config: ResearchConfig) -> None:
    state = c.state.preparation
    if state is None or c.state.status != "planned":
        raise ValueError("只能预检尚未启动的研发任务")
    if state.status in {"running", "cancelling"}:
        return
    selected = profile(c.store.root.parent.parent, state.setup.profile_id)
    try:
        config.runtime()  # Authentication failures precede remote allocation/model spending.
    except ValueError as exc:
        state.status, state.error = "failed", str(exc)
        state.checks = [PreparationCheck("模型认证", "failed", str(exc))]
        c.save()
        return
    state.job_id = new_id("job").replace("job_", "job-")
    directory = c.store.root / "preparation" / state.job_id
    directory.mkdir(parents=True)
    request = directory / "request.json"
    write_json(request, {"run_id": c.store.run_id, "setup": encode(state.setup),
                         "config": config.public()}, immutable=True)
    write_json(directory / "profile.json", selected, immutable=True)
    replacements = {"{request}": str(request), "{output}": str(directory)}
    command = tuple(replacements.get(arg, arg) for arg in selected.prepare_command)
    state.status, state.config_digest, state.error = "running", digest(config), ""
    state.checks = [PreparationCheck("模型认证", "passed"),
                    PreparationCheck("执行环境", "running", "正在执行预检")]
    c.save()
    manager = JobManager(c.store.root, auto_monitor=False)
    try:
        manager.submit(JobSpec(command=command, cwd=str(directory),
                               timeout_seconds=selected.timeout_seconds), job_id=state.job_id)
    except Exception:
        state.status, state.error = "failed", "预检作业启动失败"
        c.save()
        raise
    finally:
        manager.close(preserve_running=True)


def cancel(c: Coordinator) -> None:
    state = c.state.preparation
    if state is None or not state.job_id or state.status not in {"running", "cancelling"}:
        raise ValueError("没有正在执行的预检")
    manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
    try:
        manager.cancel(state.job_id)
    finally:
        manager.close(preserve_running=True)
    state.status, state.error = "cancelling", "正在取消预检并释放资源"
    c.save()


def refresh(c: Coordinator, config: ResearchConfig) -> ResearchConfig:
    state = c.state.preparation
    if (state is not None and state.status == "ready" and c.state.status == "planned"
        and state.config_digest != digest(config)):
        state.status, state.error = "pending", "配置已修改，请重新预检"
        state.checks = []
        c.save()
    if state is None or state.status not in {"running", "cancelling"} or not state.job_id:
        return config
    manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
    try:
        manager.poll()
        job = manager.get(state.job_id)
    finally:
        manager.close(preserve_running=True)
    directory = c.store.root / "preparation" / state.job_id
    report = directory / "checks.json"
    if report.exists() and state.status != "cancelling":
        try:
            value = json.loads(report.read_text())
            validate_record(list[PreparationCheck], value)
            state.checks = decode(list[PreparationCheck], value)
        except (ValueError, OSError):
            pass  # A check report may be replaced while it is being read.
    if job.terminal:
        try:
            if state.status == "cancelling":
                raise ValueError("预检已取消，可重新预检")
            if job.state != "succeeded":
                raise ValueError(job.error or "执行环境预检失败，请查看检查结果")
            if digest(config) != state.config_digest:
                raise ValueError("配置已变化，请重新预检")
            if not state.checks or any(check.status != "passed" for check in state.checks):
                raise ValueError("仍有未通过的预检项")
            prepared = config_from_dict(json.loads((directory / "config.json").read_text()))
            if (prepared.model_settings() != config.model_settings()
                or prepared.mode != config.mode
                or prepared.collaboration_modules != config.collaboration_modules
                or prepared.brief.objective != config.brief.objective
                or prepared.brief.limits.max_agents != state.setup.parallelism
                or prepared.brief.limits.max_wall_seconds != state.setup.wall_seconds):
                raise ValueError("预检结果改变了用户选择的任务或执行条件")
            prepared.runtime()
            write_json(c.store.root / "coordination/config.json", prepared.public())
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", new_id("prepared"), prepared.brief)
            c.store.save_run(run)
            state.status, state.config_digest = "ready", digest(prepared)
            config = prepared
        except (ValueError, OSError, KeyError) as exc:
            state.status, state.error = "failed", str(exc)
            for check in state.checks:
                if check.status in {"pending", "running"}:
                    check.status, check.message = "failed", state.error
    c.save()
    return config
