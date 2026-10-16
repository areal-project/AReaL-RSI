"""本地只读研究面板；不实例化 Agent、不执行实验、不提供任意文件下载。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import mimetypes
import re
import threading
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from areal_rsi.api.base import ContentRef, ExperimentRun, SolutionVersion
from areal_rsi.base.goal_records import execution_projection, optional_object
from areal_rsi.base.storage import decode, now, safe_name, safe_relative


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
        for path in paths:
            if not path.exists():
                continue
            run = self._load(path.stem)
            runs.append(
                {
                    "run_id": run.run_id,
                    "goal": run.plan.goal,
                    "state": run.state.value,
                    "goal_state": self.goal_state(run),
                    "started_at": self.start_time(run, self.execution_events(run)),
                    "research_agent_count": run.plan.research_agent_count,
                }
            )
        return sorted(runs, key=lambda run: run["started_at"] or "", reverse=True)

    def execution_events(self, run: ExperimentRun) -> Sequence[dict[str, Any]]:
        identifier = run.plan.config.get("goal_id")
        revision = run.plan.config.get("goal_revision")
        if not isinstance(identifier, str) or type(revision) is not int:
            return []
        folder = self.root / "goal-events" / f"{safe_name(identifier)}-r{revision}"
        return [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]

    @staticmethod
    def start_time(run: ExperimentRun, events: Sequence[dict[str, Any]]) -> str | None:
        return run.started_at or next(
            (str(event["at"]) for event in events if event["type"] == "started"), None
        )

    @staticmethod
    def active_seconds(events: Sequence[dict[str, Any]], at: str) -> float | None:
        """Project wall-clock execution intervals, excluding pauses between launches."""
        until = datetime.fromisoformat(at.replace("Z", "+00:00"))
        start, total, observed = None, 0.0, False
        for event in events:
            if event["type"] not in {"started", "outcome"}:
                continue
            when = datetime.fromisoformat(event["at"].replace("Z", "+00:00"))
            if when > until:
                break
            if event["type"] == "started":
                start, observed = when, True
            elif start is not None:
                total += max(0.0, (when - start).total_seconds())
                start = None
        if start is not None:
            total += max(0.0, (until - start).total_seconds())
        return total if observed else None

    def goal_state(self, run: ExperimentRun) -> str | None:
        identifier = run.plan.config.get("goal_id")
        revision = run.plan.config.get("goal_revision")
        if not isinstance(identifier, str) or type(revision) is not int:
            return None
        current = optional_object(self.root / "goals" / f"{safe_name(identifier)}.json")
        if not current or current.get("revision") != revision:
            current = optional_object(
                self.root / "goal-revisions" / f"{safe_name(identifier)}-r{revision}.json"
            )
        state = current.get("state") if current else None
        return state if isinstance(state, str) else None

    def content(self, ref: ContentRef) -> bytes:
        digest = ref.digest.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or ref.uri != "content://" + digest:
            raise ValueError("Invalid content reference")
        data = (self.root / "content" / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Corrupted evidence")
        return data

    def screenshots(self, run: ExperimentRun, solution_id: str) -> Sequence[dict[str, Any]]:
        found = []
        for path in (self.root / "runs" / run.run_id).rglob("task-*.json"):
            data = json.loads(path.read_text())
            output = decode(ContentRef, data["result"]["output"])
            payload = json.loads(self.content(output))
            if payload.get("solution_id") == solution_id:
                for ref in data["verification"].get("attachments", []):
                    if ref.get("media_type") == "image/png":
                        found.append(
                            {
                                "digest": ref["digest"].removeprefix("sha256:"),
                                "task_id": data["task"]["task_id"],
                            }
                        )
        return found

    def preview(self, run_id: str, solution_id: str, filename: str) -> tuple[bytes, str]:
        run = self._load(run_id)
        allowed = {
            run.plan.baseline_solution_id,
            run.best_solution_id,
            *(
                step.candidate_solution_id
                for agent in run.research_agents
                for step in agent.iterations
            ),
        }
        if solution_id not in allowed:
            raise FileNotFoundError(solution_id)
        solution = decode(
            SolutionVersion,
            json.loads((self.root / "versions" / f"{safe_name(solution_id)}.json").read_text()),
        )
        if not solution.artifacts:
            raise FileNotFoundError(filename)
        name = safe_relative(filename).as_posix()
        target_name = run.plan.config.get("artifact_name")
        artifact = next(
            (a for a in solution.artifacts if a.name == target_name), solution.artifacts[0]
        )
        manifest = json.loads(self.content(artifact.content))
        if name not in manifest["files"]:
            raise FileNotFoundError(name)
        data = self.content(decode(ContentRef, manifest["files"][name]))
        return data, mimetypes.guess_type(name)[0] or "application/octet-stream"

    def detail(self, run_id: str) -> dict[str, Any]:
        run = self._load(run_id)
        execution_events = self.execution_events(run)
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
                        "selection_reason": step.selection_reason,
                        "proposal_trajectory_id": step.proposal_trajectory_id,
                        "input_solution_id": step.input_solution_id,
                        "candidate_solution_id": step.candidate_solution_id,
                        "next_solution_id": step.next_solution_id,
                        "train_score": step.train_report.score,
                        "train_solution_id": step.train_report.solution_id,
                        "validation_solution_id": step.validation_report.solution_id,
                        "validation_metrics": step.validation_report.metrics,
                        "diff": self.content(step.change.patch).decode()[:100000]
                        if hasattr(step.change, "patch")
                        else None,
                        "screenshots": self.screenshots(run, step.candidate_solution_id)
                        if run.plan.target.value == "artifact"
                        else [],
                        "preview": f"/preview/{run_id}/{step.candidate_solution_id}/index.html"
                        if run.plan.target.value == "artifact"
                        else None,
                        "validation_score": step.validation_report.score,
                        "started_at": step.started_at,
                        "finished_at": step.finished_at,
                        "elapsed_seconds": self.active_seconds(execution_events, step.finished_at),
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
        goal_view = None
        goal_id = run.plan.config.get("goal_id")
        revision = run.plan.config.get("goal_revision")
        if isinstance(goal_id, str) and type(revision) is int:
            key = f"{safe_name(goal_id)}-r{revision}"
            contract_path = self.root / "goal-contracts" / f"{key}.json"
            outcome_path = self.root / "goal-outcomes" / f"{key}.json"
            if contract_path.exists():
                contract = json.loads(contract_path.read_text())
                outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else None
                current = optional_object(self.root / "goals" / f"{safe_name(goal_id)}.json")
                if not current or current.get("revision") != revision:
                    current = optional_object(self.root / "goal-revisions" / f"{key}.json")
                state = current.get("state") if current else (outcome or contract)["state"]
                if not isinstance(state, str):
                    raise ValueError("Goal state is missing")
                lock_path = self.root / "goal-workers" / f"{key}.lock"
                active = False
                if lock_path.exists():
                    with lock_path.open("r") as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        except BlockingIOError:
                            active = True
                projection = execution_projection(self.root, key, state, run.run_id, active)
                goal_view = {
                    "goal_id": goal_id,
                    "revision": revision,
                    "acceptance": contract["spec"]["acceptance"],
                    "budget": contract["spec"]["budget"],
                    "state": state,
                    **projection,
                    "identity": contract["frozen_identity"]["digest"],
                    "researcher": contract["researcher"],
                }
        final_path = folder / "final/report.json"
        final = json.loads(final_path.read_text()) if final_path.exists() else None
        skill_evidence = []
        proposal_ids = {
            step.proposal_trajectory_id
            for agent in run.research_agents
            for step in agent.iterations
            if step.proposal_trajectory_id
        }
        if goal_view and (folder / "progress.json").exists():
            for path in (self.root / "trajectories").glob("*.json"):
                trace = json.loads(path.read_text())
                if trace.get("trajectory_id") not in proposal_ids:
                    continue
                payload = json.loads(self.content(decode(ContentRef, trace["content"])))
                evidence = payload.get("research_skills")
                if evidence and evidence.get("configured"):
                    from areal_rsi.modules.runtime_package import skill_resource_events

                    calls = skill_resource_events(payload.get("events", []))
                    skill_evidence.append(
                        {
                            "trajectory_id": trace["trajectory_id"],
                            "configured": evidence["configured"],
                            "loaded": len(evidence["loaded_events"]),
                            "resources": len(calls["reads"]) + len(calls["commands"]),
                            "resource_reads": len(calls["reads"]),
                            "resource_commands": len(calls["commands"]),
                            "resource_discovery": len(calls["discovery"]),
                            "resource_failures": len(calls["failed"]),
                            "resource_unverified": len(calls["unverified"]),
                            "semantic_effect": "unknown",
                        }
                    )
        # 显式投影：不返回 plan.config、ContentRef、Memory、gateway 或原始轨迹。
        return {
            "run_id": run.run_id,
            "goal": run.plan.goal,
            "goal_contract": goal_view,
            "final_acceptance": final,
            "final_elapsed_seconds": (
                self.active_seconds(execution_events, final["at"])
                if final and isinstance(final.get("at"), str)
                else None
            ),
            "skill_evidence": skill_evidence,
            "skill_captures": [
                json.loads(path.read_text())
                for path in (self.root / "skill-captures").glob("*.json")
                if json.loads(path.read_text()).get("goal_id") == goal_id
            ],
            "skill_lifecycle": [
                json.loads(path.read_text())
                for path in (self.root / "skill-lifecycle").glob("*.json")
            ],
            "skill_reuse": [
                json.loads(path.read_text()) for path in (self.root / "skill-reuse").glob("*.json")
            ],
            "historical_acceptance": "历史运行已完成，目标达成未验证" if not goal_view else None,
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
            "started_at": self.start_time(run, execution_events),
            "finished_at": run.finished_at,
            "error": run.error,
            "worker_alive": self.worker_alive(run.run_id),
            "observed_at": now(),
        }


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, reader: RunReader):
        self.reader = reader
        super().__init__(("127.0.0.1", 0), PreviewHandler)


class PreviewHandler(BaseHTTPRequestHandler):
    server: PreviewServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        if host not in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }:
            self.send_error(403)
            return
        try:
            parts = unquote(urlsplit(self.path).path).split("/", 3)
            if len(parts) != 4:
                raise ValueError("Invalid preview URL")
            payload, media = self.server.reader.preview(parts[1], parts[2], parts[3])
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "sandbox allow-scripts allow-same-origin; default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "connect-src 'none'; img-src 'self' data:; object-src 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)
        except (ValueError, KeyError):
            self.send_error(400)
        except OSError:
            self.send_error(404)

    def do_POST(self) -> None:
        self.send_error(405)


class ResearchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, *, port: int = 8765, run_id: str | None = None):
        self.reader = RunReader(root, run_id)
        super().__init__(("127.0.0.1", port), ResearchHandler)
        self.preview_server = PreviewServer(self.reader)
        self.preview_thread = threading.Thread(
            target=self.preview_server.serve_forever, daemon=True
        )
        self.preview_thread.start()

    def server_close(self) -> None:
        if hasattr(self, "preview_server"):
            self.preview_server.shutdown()
            self.preview_server.server_close()
            self.preview_thread.join(timeout=3)
        super().server_close()


class ResearchHandler(BaseHTTPRequestHandler):
    server: ResearchServer

    def log_message(self, format: str, *args: Any) -> None:
        # URL 中可能带用户内容，不记录访问请求。
        pass

    def respond(
        self, status: int, payload: bytes, content_type: str, *, preview: bool = False
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            (
                "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; "
                "frame-ancestors 'self'; base-uri 'none'"
            )
            if preview
            else (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                f"frame-src 'self' http://127.0.0.1:{self.server.preview_server.server_port}; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            ),
        )
        with suppress(BrokenPipeError, ConnectionResetError):
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
            elif path.startswith("/preview/"):
                parts = path.split("/", 4)
                if len(parts) != 5:
                    raise ValueError("Invalid preview")
                self.server.reader.preview(parts[2], parts[3], parts[4])
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{self.server.preview_server.server_port}"
                    + path.removeprefix("/preview"),
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path.startswith("/screenshots/"):
                parts = path.split("/")
                if len(parts) != 4:
                    raise ValueError("Invalid screenshot")
                run = self.server.reader._load(parts[2])
                digest = parts[3]
                allowed = {
                    row["digest"]
                    for agent in run.research_agents
                    for step in agent.iterations
                    for row in self.server.reader.screenshots(run, step.candidate_solution_id)
                }
                if digest not in allowed:
                    raise FileNotFoundError(digest)
                self.respond(
                    200,
                    self.server.reader.content(
                        ContentRef("content://" + digest, "sha256:" + digest)
                    ),
                    "image/png",
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
