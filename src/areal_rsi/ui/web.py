"""本地只读研究面板；不实例化 Agent、不执行实验、不提供任意文件下载。"""

from __future__ import annotations

import fcntl
import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from areal_rsi.api.base import ExperimentRun, SolutionVersion
from areal_rsi.base.storage import decode, encode, now, safe_name
from areal_rsi.experiment_config import experiment_roots
from areal_rsi.modules.remote.registry import read_remote_jobs


class RunReader:
    def __init__(self, root: Path, run_id: str | None = None):
        self.root = root.resolve()
        self.run_id = safe_name(run_id) if run_id else None

    def _load(self, run_id: str) -> ExperimentRun:
        if self.run_id and run_id != self.run_id:
            raise FileNotFoundError(run_id)
        path = self.root / "run-states" / f"{safe_name(run_id)}.json"
        run: ExperimentRun = decode(ExperimentRun, json.loads(path.read_text()))
        if run.run_id != run_id:
            raise ValueError("实验记录身份与文件名不一致")
        return run

    def worker_alive(self, run_id: str) -> bool:
        for root in experiment_roots(self.root)[1:]:
            if (root / "run-states" / f"{safe_name(run_id)}.json").is_file():
                return RunReader(root, self.run_id).worker_alive(run_id)
        path = self.root / "locks" / f"{safe_name(run_id)}.lock"
        if not path.exists():
            return False
        with path.open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lock, fcntl.LOCK_UN)
        return False

    def list(self) -> list[dict[str, Any]]:
        paths = (
            [self.root / "run-states" / f"{self.run_id}.json"]
            if self.run_id
            else list((self.root / "run-states").glob("*.json"))
        )
        runs = []
        for root in experiment_roots(self.root)[1:]:
            runs.extend(RunReader(root, self.run_id).list())
        for path in paths:
            if not path.exists():
                continue
            run = self._load(path.stem)
            runs.append(
                {
                    "run_id": run.run_id,
                    "mode": run.plan.config.get("mode"),
                    "goal": run.plan.goal,
                    "state": run.state.value,
                    "started_at": run.started_at,
                    "research_agent_count": run.plan.research_agent_count,
                }
            )
        return sorted(runs, key=lambda run: run["started_at"] or "", reverse=True)

    def detail(self, run_id: str) -> dict[str, Any]:
        for root in experiment_roots(self.root)[1:]:
            if (root / "run-states" / f"{safe_name(run_id)}.json").is_file():
                return RunReader(root, self.run_id).detail(run_id)
        run = self._load(run_id)
        folder = self.root / "runs" / run.run_id
        progress_path = folder / "progress.json"
        progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
        baseline_path = folder / "baseline" / "validate" / "evaluation.json"
        baseline_score = (
            json.loads(baseline_path.read_text())["report"]["score"]
            if baseline_path.exists()
            else None
        )
        model_path = self.root / "versions" / f"{safe_name(run.plan.baseline_solution_id)}.json"
        model_name = None
        if model_path.exists():
            solution = decode(SolutionVersion, json.loads(model_path.read_text()))
            model_name = solution.model.name
        configured_models = run.plan.config.get("agent_models", {})
        model_names = (
            {
                agent_id: spec["model"]
                for agent_id, spec in configured_models.items()
                if isinstance(spec, dict) and isinstance(spec.get("model"), str)
            }
            if isinstance(configured_models, dict)
            else {}
        )
        legacy_teacher = run.plan.config.get("teacher_model_name")
        if not isinstance(legacy_teacher, str):
            legacy_teacher = None
        interaction_model = run.plan.config.get("interaction_model_name")
        agents = []
        best_score = baseline_score
        for agent in run.research_agents:
            state = progress.get(agent.agent_id, {})
            phase = state.get("phase", "waiting")
            if agent.state.value == "succeeded":
                phase = "completed"
            elif agent.state.value == "failed":
                phase = "failed"
            elif agent.state.value == "cancelled":
                phase = "pruned" if phase == "pruned" else "cancelled"
            elif agent.state.value == "running" and phase in {"failed", "cancelled"}:
                # 兼容早期运行记录：恢复后、进入首个节点前可能仍留着旧阶段。
                phase = "waiting"
            history = []
            for step in agent.iterations:
                if step.candidate_solution_id == run.best_solution_id:
                    best_score = step.validation_report.score
                history.append(
                    {
                        "iteration": step.iteration,
                        "reason": step.change.reason,
                        "input_solution_id": step.input_solution_id,
                        "candidate_solution_id": step.candidate_solution_id,
                        "next_solution_id": step.next_solution_id,
                        "train_score": step.train_report.score,
                        "validation_score": step.validation_report.score,
                        "started_at": step.started_at,
                        "finished_at": step.finished_at,
                    }
                )
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "model": model_names.get(agent.agent_id, legacy_teacher),
                    "state": agent.state.value,
                    "completed_iterations": len(history),
                    "max_iterations": run.plan.max_iterations,
                    "phase": phase,
                    "iteration": state.get("iteration"),
                    "task": state.get("task"),
                    "task_count": state.get("task_count"),
                    "current_reason": state.get("reason"),
                    "error": agent.error,
                    "updated_at": state.get("updated_at"),
                    "history": history,
                }
            )
        # 显式投影：不返回 plan.config、ContentRef、Memory、gateway 或原始轨迹。
        remote_jobs = [encode(job) for job in read_remote_jobs(self.root, run.run_id)]
        return {
            "run_id": run.run_id,
            "mode": run.plan.config.get("mode"),
            "goal": run.plan.goal,
            "state": run.state.value,
            "workflow": run.plan.workflow,
            "target": run.plan.target.value,
            "benchmark": run.plan.benchmark,
            "model": model_name,
            "target_model": model_name,
            "rsi_model": model_names.get("rsi-agent", legacy_teacher),
            "interaction_model": interaction_model if isinstance(interaction_model, str) else None,
            "teacher_model": (
                run.plan.config["teacher_model_name"]
                if isinstance(run.plan.config.get("teacher_model_name"), str)
                else None
            ),
            "baseline_solution_id": run.plan.baseline_solution_id,
            "best_solution_id": run.best_solution_id,
            "baseline_score": baseline_score,
            "baseline_progress": {
                key: progress.get("baseline", {}).get(key)
                for key in ("phase", "task", "task_count")
            },
            "best_score": best_score,
            "max_iterations": run.plan.max_iterations,
            "research_agent_count": run.plan.research_agent_count,
            "agents": agents,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "worker_alive": self.worker_alive(run.run_id),
            "observed_at": now(),
            "remote_jobs": remote_jobs,
        }


class ResearchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, *, port: int = 8765, run_id: str | None = None):
        self.reader = RunReader(root, run_id)
        super().__init__(("127.0.0.1", port), ResearchHandler)


class ResearchHandler(BaseHTTPRequestHandler):
    server: ResearchServer

    def log_message(self, format: str, *args: Any) -> None:
        # URL 中可能带用户内容，不记录访问请求。
        pass

    def respond(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def json_response(self, status: int, value: Any) -> None:
        self.respond(
            status,
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
            "application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        # 拒绝 DNS rebinding；无 CORS、无外部 Host、无外部字体或脚本。
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host", "").lower() not in hosts:
            self.json_response(403, {"error": "只允许本机访问"})
            return
        path = unquote(urlsplit(self.path).path)
        assets = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        try:
            if path in assets:
                name, media = assets[path]
                self.respond(
                    200,
                    (Path(__file__).parent / "static" / name).read_bytes(),
                    media + "; charset=utf-8",
                )
            elif path == "/api/runs":
                self.json_response(200, {"runs": self.server.reader.list()})
            elif path.startswith("/api/runs/"):
                self.json_response(200, self.server.reader.detail(path.removeprefix("/api/runs/")))
            else:
                self.json_response(404, {"error": "不存在的只读接口"})
        except FileNotFoundError:
            self.json_response(404, {"error": "未找到实验记录"})
        except (ValueError, KeyError, TypeError):
            self.json_response(400, {"error": "实验标识或数据格式无效"})
        except OSError:
            self.json_response(500, {"error": "无法读取实验记录"})

    def do_POST(self) -> None:
        self.json_response(405, {"error": "Web 面板只读，请通过 TUI/CLI 控制实验"})


def serve(root: Path, *, port: int = 8765, run_id: str | None = None) -> None:
    with ResearchServer(root, port=port, run_id=run_id) as server:
        print(f"RSI 研究面板：http://127.0.0.1:{server.server_port}/", flush=True)
        print("只读展示，关闭页面不会停止实验。Ctrl+C 停止 Web 服务。", flush=True)
        with suppress(KeyboardInterrupt):
            server.serve_forever(poll_interval=0.25)
