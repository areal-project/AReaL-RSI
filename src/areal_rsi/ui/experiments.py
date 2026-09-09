"""DSH 的实验后端：固定计划、明确确认、独立 worker 和可核验的停止请求。"""

from __future__ import annotations

import asyncio
import ctypes
import fcntl
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Protocol, TypeAlias

from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import ExperimentPlan, ExperimentRun, JsonObject, RunState, SolutionVersion
from areal_rsi.base.storage import FileRecords, decode, encode, new_id, now, safe_name, write_json
from areal_rsi.experiment_config import launch_digest, plan_environment
from areal_rsi.workflows.service import LocalExperimentService


class ExperimentApp(Protocol):
    baseline: SolutionVersion
    rsi: DefaultRsiAgent
    service: LocalExperimentService

    def model_plan_config(self) -> JsonObject: ...

    async def bind_plan(self, plan: ExperimentPlan) -> None: ...

    async def close(self) -> None: ...


AppFactory: TypeAlias = Callable[[Path, Callable[[str], None]], Awaitable[ExperimentApp]]


async def _configured_app(root: Path, emit: Callable[[str], None]) -> ExperimentApp:
    from areal_rsi.app import create_app

    return await create_app(root, emit)


@contextmanager
def _locked(path: Path) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield stream.fileno()


def _lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


class _DarwinProcessInfo(ctypes.Structure):
    # Darwin sys/proc_info.h 的 proc_bsdinfo；仅读取 PID 和微秒级启动时间。
    _fields_ = [
        ("ids", ctypes.c_uint32 * 12),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("stats", ctypes.c_uint32 * 6),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


def _process_identity(pid: int) -> str | None:
    """核对内核记录的启动身份；不依赖沙箱中可能被禁用的 ps 命令。"""
    if type(pid) is not int or pid < 1:
        return None
    try:
        if sys.platform == "darwin":
            library = ctypes.CDLL("/usr/lib/libproc.dylib")
            call = library.proc_pidinfo
            call.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            call.restype = ctypes.c_int
            info = _DarwinProcessInfo()
            if call(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
                return None
            if info.ids[3] != pid or info.start_seconds == 0:
                return None
            return f"darwin:{info.start_seconds}:{info.start_microseconds}"
        if sys.platform.startswith("linux"):
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return f"linux:{fields[19]}"
    except (OSError, IndexError):
        return None
    return None


class ExperimentController:
    def __init__(
        self,
        root: Path,
        *,
        app_factory: AppFactory = _configured_app,
        worker_command: Sequence[str] | None = None,
    ):
        self.root = root.resolve()
        self.app_factory = app_factory
        self.worker_command = tuple(
            worker_command or (sys.executable, "-m", "areal_rsi", "_experiment-worker")
        )
        self.runs = FileRecords(self.root / "run-states")
        self.plans = FileRecords(self.root / "experiment-plans")
        self.workers = self.root / "workers"
        self.workers.mkdir(parents=True, exist_ok=True)
        self._children: dict[str, subprocess.Popen[bytes]] = {}

    def _worker_path(self, run_id: str) -> Path:
        return self.workers / f"{safe_name(run_id)}.json"

    def _worker_lock(self, run_id: str) -> Path:
        return self.workers / f"{safe_name(run_id)}.lock"

    def _read_worker(self, run_id: str) -> dict[str, Any] | None:
        path = self._worker_path(run_id)
        return json.loads(path.read_text()) if path.exists() else None

    def _run(self, run_id: str) -> ExperimentRun:
        run = self.runs.get(safe_name(run_id), ExperimentRun)
        if run is None:
            raise KeyError(run_id)
        return run

    def _worker_summary(self, run_id: str) -> dict[str, Any]:
        child = self._children.get(run_id)
        if child is not None and child.poll() is not None:
            self._children.pop(run_id)
        worker = self._read_worker(run_id)
        log_path = str(self.workers / f"{safe_name(run_id)}.log")
        if worker is None:
            return {"pid": None, "active": False, "status": "unmanaged", "log_path": log_path}
        held = _lock_held(self._worker_lock(run_id))
        identity = _process_identity(worker["pid"]) if held else None
        matched = held and identity is not None and identity == worker["identity"]
        status = worker["status"]
        if held and not matched:
            status = "identity_mismatch"
        elif not held and status in ("launching", "running", "stop_requested"):
            status = "exited"
        return {
            "pid": worker["pid"],
            "active": bool(matched),
            "status": status,
            "log_path": log_path,
        }

    def _summary(self, run: ExperimentRun) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "state": run.state.value,
            "goal": run.plan.goal[:500],
            "workflow": run.plan.workflow,
            "target": run.plan.target.value,
            "benchmark": run.plan.benchmark,
            "mode": run.plan.config.get("mode"),
            "research_harness": run.plan.config.get("research_harness"),
            "teacher_model_name": run.plan.config.get("teacher_model_name"),
            "agent_models": run.plan.config.get("agent_models"),
            "interaction_model_name": run.plan.config.get("interaction_model_name"),
            "target_model_name": run.plan.config.get(
                "target_model_name", run.plan.config.get("model_name")
            ),
            "best_solution_id": run.best_solution_id,
            "completed_iterations": sum(len(agent.iterations) for agent in run.research_agents),
            "max_iterations": run.plan.max_iterations,
            "research_agent_count": run.plan.research_agent_count,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": "实验未完成；请查看本地 worker 日志" if run.error else None,
            "worker": self._worker_summary(run.run_id),
        }

    async def plan(self, goal: str, max_iterations: int) -> dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("goal 必须为 1 到 4000 字符的非空文本")
        if type(max_iterations) is not int or not 1 <= max_iterations <= 20:
            raise ValueError("max_iterations 必须为 1 到 20 的整数")
        app = await self.app_factory(self.root, lambda _: None)
        try:
            # 配置决定 baseline、目标和研究线；DSH 只提供用户目标及轮数，不另开聊天模型。
            plan = ExperimentPlan(
                goal.strip(),
                app.rsi.workflow_name,
                app.service.context.benchmark.name,
                app.baseline.solution_id,
                app.rsi.evolution_target,
                max_iterations,
                research_agent_count=len(app.service.context.research_agents),
                stop_when_goal_reached=False,
                config={
                    "mode": os.environ.get("RSI_MODE", "local"),
                    **app.model_plan_config(),
                    **(
                        {"launch_digest": launch_digest(self.root)}
                        if (self.root / "launch.json").exists()
                        else {}
                    ),
                },
            )
            app.service.validate(plan)
        finally:
            await app.close()
        plan_id = new_id("plan")
        record = {"plan_id": plan_id, "plan": encode(plan), "created_at": now()}
        self.plans.save(plan_id, record)
        return {**record, "confirmation_required": True}

    def _launch(self, run: ExperimentRun) -> None:
        if _lock_held(self.root / "locks" / f"{run.run_id}.lock"):
            raise ValueError("实验已由另一个进程执行")
        with self._worker_lock(run.run_id).open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("此实验已有 worker，不能重复启动") from None
            token = new_id("worker")
            command = [
                *self.worker_command,
                "--root",
                str(self.root),
                "--run-id",
                run.run_id,
                "--token",
                token,
                "--lock-fd",
                str(lock.fileno()),
            ]
            log_path = self.workers / f"{run.run_id}.log"
            with log_path.open("ab", buffering=0) as log:
                # 继承锁覆盖 spawn 到 worker 初始化的窗口；父入口退出不取消子实验。
                child = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(lock.fileno(),),
                    env={
                        **os.environ,
                        **plan_environment(self.root, run.plan.config),
                        "RSI_DATA_ROOT": str(self.root),
                    },
                )
            self._children[run.run_id] = child
            identity = _process_identity(child.pid)
            if identity is None:
                raise RuntimeError("worker 未成功启动，请查看本地日志")
            write_json(
                self._worker_path(run.run_id),
                {
                    "run_id": run.run_id,
                    "pid": child.pid,
                    "identity": identity,
                    "token": token,
                    "status": "launching",
                    "started_at": now(),
                },
            )

    async def start(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("必须先向用户展示计划并获得确认，再设置 confirmed=true")
        plan_id = safe_name(plan_id)
        with _locked(self.workers / "control.lock"):
            path = self.root / "experiment-plans" / f"{plan_id}.json"
            if not path.exists():
                raise KeyError(plan_id)
            record = json.loads(path.read_text())
            binding = self.workers / f"{plan_id}-run.json"
            if binding.exists():
                run_id = json.loads(binding.read_text())["run_id"]
            else:
                plan = decode(ExperimentPlan, record["plan"])
                plan_environment(self.root, plan.config)
                run = ExperimentRun(
                    new_id("run"),
                    plan,
                    RunState.PLANNED,
                    plan.baseline_solution_id,
                    started_at=now(),
                )
                self.runs.save(run.run_id, run)
                run_id = run.run_id
                write_json(binding, {"run_id": run_id, "plan_id": plan_id}, immutable=True)
                self._launch(run)
        return {**self._summary(self._run(run_id)), "plan_id": plan_id}

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id is not None:
            return self._summary(self._run(run_id))
        paths = sorted(
            self.runs.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
        )[:20]
        return {"runs": [self._summary(self._run(path.stem)) for path in paths]}

    async def stop(self, run_id: str) -> dict[str, Any]:
        with _locked(self.workers / "control.lock"):
            run = self._run(run_id)
            worker = self._read_worker(run_id)
            if worker is None:
                raise ValueError("不能停止未归属本控制层的实验")
            view = self._worker_summary(run_id)
            requested = bool(view["active"])
            if view["status"] == "identity_mismatch":
                raise ValueError("worker 的 PID 身份不匹配，拒绝停止")
            if requested:
                write_json(
                    self.workers / f"{run_id}-stop.json",
                    {
                        "token": worker["token"],
                        "pid": worker["pid"],
                        "identity": worker["identity"],
                        "requested_at": now(),
                    },
                )
                worker["status"] = "stop_requested"
                write_json(self._worker_path(run_id), worker)
        return {**self._summary(run), "stop_requested": requested}

    async def resume(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("恢复实验前必须获得用户确认，再设置 confirmed=true")
        with _locked(self.workers / "control.lock"):
            run = self._run(run_id)
            if run.state != RunState.SUCCEEDED and not self._worker_summary(run_id)["active"]:
                self._launch(run)
        return self._summary(self._run(run_id))


async def run_worker(
    root: Path,
    run_id: str,
    token: str,
    lock_fd: int,
    *,
    app_factory: AppFactory = _configured_app,
) -> int:
    """内部 CLI 入口；调用方必须传入控制层启动时继承的专属锁 FD。"""
    controller = ExperimentController(root, app_factory=app_factory)
    run_id, token = safe_name(run_id), safe_name(token)
    expected = controller._worker_lock(run_id).stat()
    actual = os.fstat(lock_fd)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("worker 锁身份不匹配")
    # 继承仅用于控制层移交，不把 worker 锁继续传给模型或工具子进程。
    os.set_inheritable(lock_fd, False)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with _locked(controller.workers / "control.lock"):
        worker = controller._read_worker(run_id)
        if (
            worker is None
            or worker["token"] != token
            or worker["pid"] != os.getpid()
            or worker["identity"] != _process_identity(os.getpid())
        ):
            raise ValueError("worker 启动身份不匹配")
        worker["status"] = "running"
        write_json(controller._worker_path(run_id), worker)
    current = asyncio.current_task()
    assert current is not None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, current.cancel)

    async def watch_stop() -> None:
        path = controller.workers / f"{run_id}-stop.json"
        while True:
            if path.exists():
                request = json.loads(path.read_text())
                if all(request.get(key) == worker[key] for key in ("token", "pid", "identity")):
                    current.cancel()
                    return
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(watch_stop())
    app: ExperimentApp | None = None
    run = controller._run(run_id)
    not_owner = False
    try:
        # 让启动前已经收到的停止请求优先执行，避免继续装配或提交评测。
        await asyncio.sleep(0)
        os.environ.update(plan_environment(controller.root, run.plan.config))
        app = await app_factory(controller.root, lambda text: print(text, flush=True))
        await app.bind_plan(run.plan)
        if (
            run.plan.baseline_solution_id != app.baseline.solution_id
            or run.plan.workflow != app.rsi.workflow_name
            or run.plan.target != app.rsi.evolution_target
        ):
            raise ValueError("实验装配与已确认计划不一致，请创建新计划")
        run = await app.service.resume(run_id)
        if run_id in app.service.tasks:
            run = await app.service.tasks[run_id]
    except BaseException as exc:
        if app is not None:
            await app.service.stop(run_id)
        execution_lock = controller.root / "locks" / f"{run_id}.lock"
        execution_lock.parent.mkdir(parents=True, exist_ok=True)
        with execution_lock.open("a+") as lock:
            try:
                # legacy CLI 可能在 spawn 后先恢复同一 Run；外层异常不能覆盖其运行状态。
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                not_owner = True
                run = controller._run(run_id)
                print("另一个进程持有实验执行锁；当前 worker 退出并保留 Run 状态", flush=True)
            else:
                run = controller._run(run_id)
                run.state = (
                    RunState.CANCELLED
                    if isinstance(exc, asyncio.CancelledError)
                    else RunState.FAILED
                )
                run.error, run.finished_at = f"{type(exc).__name__}: {exc}", now()
                for agent in run.research_agents:
                    if agent.state == RunState.RUNNING:
                        agent.state, agent.error = run.state, run.error
                controller.runs.save(run_id, run, immutable=False)
                print(f"实验 {run.state.value}：{run.error}", flush=True)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if app is not None:
            await app.close()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        with _locked(controller.workers / "control.lock"):
            latest = controller._read_worker(run_id)
            if latest and latest["token"] == token:
                latest.update(
                    status="not_owner" if not_owner else run.state.value, finished_at=now()
                )
                write_json(controller._worker_path(run_id), latest)
        with suppress(OSError):
            os.close(lock_fd)
    return 0 if run.state == RunState.SUCCEEDED and not not_owner else 1
