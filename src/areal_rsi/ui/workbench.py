"""Loopback research workbench with an async application boundary and explicit views.

The HTTP layer never opens run/session/private files. A backend returns public
research records; allow-list projection provides a second boundary before JSON
is delivered to the browser. All mutations require a same-origin bootstrap token.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import mimetypes
import re
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, unquote, urlsplit

Json = dict[str, Any]
STATIC_ROOT = Path(__file__).with_name("workbench_static")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_BODY_BYTES = 128 * 1024


@dataclass(frozen=True)
class WorkbenchDownload:
    path: Path
    filename: str
    media_type: str = "application/zip"


class WorkbenchBackend(Protocol):
    async def call(self, action: str, params: Json) -> Json | WorkbenchDownload: ...


def _fields(value: Any, names: str) -> Json:
    if not isinstance(value, dict):
        return {}
    return {name: value[name] for name in names.split() if name in value}


def _refs(value: Any) -> list[Json]:
    return [_fields(item, "uri") for item in value] if isinstance(value, list) else []


def _score(value: Any) -> Json | None:
    if not isinstance(value, dict):
        return None
    result = _fields(value, "result_id candidate_id score passed status phase simulated error")
    result["metrics"] = (
        {
            name: number
            for name, number in value.get("metrics", {}).items()
            if isinstance(number, (int, float)) and not isinstance(number, bool)
        }
        if isinstance(value.get("metrics"), dict)
        else {}
    )
    return result


def _report(value: Any) -> Json | None:
    if not isinstance(value, dict):
        return None
    result = _fields(
        value,
        "report_id iteration_id assignment_id message task_status work problems results "
        "interpretation next_steps shared_findings created_at",
    )
    result["references"] = _refs(value.get("references"))
    return result


def _evidence(value: Any) -> Json:
    result = _fields(
        value,
        "evidence_id agent_id iteration_id title summary created_at shared limitations supersedes",
    )
    result["references"] = _refs(value.get("references")) if isinstance(value, dict) else []
    return result


def _brief(value: Any) -> Json:
    if not isinstance(value, dict):
        return {}
    result = _fields(value, "objective instructions")
    result["limits"] = _fields(
        value.get("limits"),
        "max_agents max_nodes max_model_calls max_total_tokens max_wall_seconds "
        "max_verify_calls max_jobs gpu_ids allowed_paths",
    )
    verification = value.get("verification")
    result["verification"] = (
        _fields(verification, "metric direction target description")
        if isinstance(verification, dict)
        else None
    )
    return result


def public_detail(value: Json) -> Json:
    """Only named product-facing records pass; never config, sessions or raw traces."""
    result = _fields(
        value,
        "run_id objective status simulated plan started_at finished_at event_cursor "
        "worker_alive waiting_for_user budget_stop_reason execution_setup",
    )
    result["brief"] = _brief(value.get("brief"))
    result["intake"] = _fields(value.get("intake"), "status summary environment execution_required")
    result["model_settings"] = _fields(
        value.get("model_settings"), "base_url model reasoning_effort editable has_credentials"
    )
    result["pending_agents"] = [a for a in value.get("pending_agents", []) if isinstance(a, str)]
    result["execution_blocker"] = _fields(value.get("execution_blocker"), "job_id error created_at")
    result["agent_activity"] = {
        name: _fields(
            activity, "type tool updated_at attempt error_code delay_seconds next_retry_at"
        )
        for name, activity in value.get("agent_activity", {}).items()
        if isinstance(name, str) and isinstance(activity, dict)
    }
    result["waiting_for_user"] = [
        _fields(question, "message_id agent_id question_id text question created_at")
        if isinstance(question, dict)
        else question
        for question in value.get("waiting_for_user", [])
        if isinstance(question, (dict, str))
    ]
    for name in ("baseline", "best", "final"):
        result[name] = _score(value.get(name))
    result["budget"] = _fields(
        value.get("budget"),
        "model_calls api_attempts total_tokens verify_calls reserved_tokens elapsed_seconds",
    )
    result["agents"] = [
        _fields(agent, "agent_id direction state current_iteration_id wait_reason error")
        for agent in value.get("agents", [])
        if isinstance(agent, dict)
    ]
    result["nodes"] = []
    for node in value.get("nodes", []):
        if not isinstance(node, dict):
            continue
        entry = _fields(
            node,
            "iteration_id agent_id state parent_iteration_id created_at updated_at title",
        )
        entry["assignment"] = _fields(
            node.get("assignment"),
            "assignment_id instruction completion_criteria created_at reply_to_report_id",
        )
        entry["assignment"]["references"] = _refs((node.get("assignment") or {}).get("references"))
        entry["report"] = _report(node.get("report"))
        entry["evidence"] = [_evidence(item) for item in node.get("evidence", [])]
        entry["references"] = _refs(node.get("references"))
        entry["verification"] = _score(node.get("verification"))
        result["nodes"].append(entry)
    result["evidence"] = [_evidence(item) for item in value.get("evidence", [])]
    result["edges"] = [
        _fields(edge, "source target kind label")
        for edge in value.get("edges", [])
        if isinstance(edge, dict)
    ]
    result["messages"] = [
        _fields(message, "message_id sender recipient text created_at reply_to")
        for message in value.get("messages", [])
        if isinstance(message, dict)
        and (message.get("sender") == "user" or message.get("recipient") == "user")
    ]
    from areal_rsi.execution.progress import public_progress

    result["jobs"] = [
        {
            **_fields(
                job,
                "job_id agent_id status title description created_at started_at finished_at error "
                "gpu_ids progress_message agent_name iteration_id candidate_id phase "
                "verification is_baseline",
            ),
            "progress": public_progress(job.get("progress")),
            "result": _score(job.get("result")),
        }
        for job in value.get("jobs", [])
        if isinstance(job, dict)
    ]
    result["events"] = []
    for event in value.get("events", []):
        if not isinstance(event, dict):
            continue
        entry = _fields(event, "sequence kind created_at agent_id iteration_id")
        entry["data"] = _fields(
            event.get("data"),
            "reason direction title instruction completion_criteria parent_iteration_id "
            "report_id evidence_id candidate_id job_id status text message_id sender recipient "
            "reply_to resumed simulated request_id model reasoning_effort "
            "attempt error_code delay_seconds next_retry_at",
        )
        result["events"].append(entry)
    return result


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        backend: WorkbenchBackend,
        *,
        port: int = 8766,
        loop: asyncio.AbstractEventLoop | None = None,
        backend_timeout: float = 30,
    ):
        self.backend = backend
        self.backend_timeout = backend_timeout
        self.csrf_token = secrets.token_urlsafe(32)
        self._owns_loop = loop is None
        self.loop = loop or asyncio.new_event_loop()
        self._loop_thread: threading.Thread | None = None
        super().__init__(("127.0.0.1", port), WorkbenchHandler)
        if self._owns_loop:
            self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
            self._loop_thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def invoke(self, action: str, params: Json) -> Json | WorkbenchDownload:
        future = asyncio.run_coroutine_threadsafe(self.backend.call(action, params), self.loop)
        try:
            return future.result(timeout=self.backend_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("工作台请求超时，请刷新查看最新状态") from None

    def server_close(self) -> None:
        super().server_close()
        if self._owns_loop and self.loop.is_running():

            async def cancel_pending() -> None:
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            future = asyncio.run_coroutine_threadsafe(cancel_pending(), self.loop)
            try:
                future.result(timeout=5)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                if self._loop_thread is not None:
                    self._loop_thread.join(timeout=5)
                self.loop.close()


class WorkbenchHandler(BaseHTTPRequestHandler):
    server: WorkbenchServer

    def log_message(self, format: str, *args: Any) -> None:
        # HTTP paths can contain private input; application events own observability.
        pass

    def _safe_origin(self) -> bool:
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in hosts:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {"http://" + host for host in hosts}

    def _headers(self, status: int, media_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def _json(self, status: int, value: Json) -> None:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self._headers(status, "application/json; charset=utf-8", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _path(self) -> tuple[list[str], Json]:
        parsed = urlsplit(self.path)
        if parsed.netloc or parsed.scheme or parsed.fragment:
            raise ValueError("请求路径无效")
        path = unquote(parsed.path)
        if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
            raise ValueError("请求路径无效")
        return path.strip("/").split("/"), parse_qs(parsed.query)

    def do_GET(self) -> None:
        if not self._safe_origin():
            self._json(403, {"error": "仅允许本机工作台访问"})
            return
        try:
            parts, query = self._path()
            assets: dict[tuple[str, ...], str] = {
                ("",): "index.html",
                ("app.js",): "app.js",
                ("dropdown.js",): "dropdown.js",
                ("style.css",): "style.css",
            }
            asset = assets.get(tuple(parts))
            if asset is not None:
                data = (STATIC_ROOT / asset).read_bytes()
                mime = mimetypes.guess_type(asset)[0] or "application/octet-stream"
                self._headers(200, mime + "; charset=utf-8", len(data))
                self.end_headers()
                self.wfile.write(data)
                return
            if parts == ["api", "bootstrap"]:
                raw = self.server.invoke("bootstrap", {})
                if not isinstance(raw, dict):
                    raise TypeError("Invalid backend bootstrap result")
                self._json(
                    200,
                    {
                        "csrf_token": self.server.csrf_token,
                        **{
                            name: raw.get(name) is True
                            for name in ("allow_simulation", "default_simulated", "has_config")
                        },
                        "models": [_fields(model, "id efforts") for model in raw.get("models", [])],
                        "default_model_settings": _fields(
                            raw.get("default_model_settings"),
                            "base_url model reasoning_effort has_credentials",
                        ),
                    },
                )
                return
            if parts == ["api", "runs"]:
                raw = self.server.invoke("list", {})
                if not isinstance(raw, dict):
                    raise TypeError("Invalid backend list result")
                self._json(
                    200,
                    {
                        "runs": [
                            _fields(run, "run_id objective status simulated started_at")
                            for run in raw.get("runs", [])
                        ]
                    },
                )
                return
            if len(parts) == 3 and parts[:2] == ["api", "runs"] and IDENTIFIER.fullmatch(parts[2]):
                after = int(query.get("after", ["0"])[0])
                if after < 0:
                    raise ValueError("事件位置无效")
                params = {"run_id": parts[2], "after": after}
                if "event_limit" in query:
                    limit = int(query["event_limit"][0])
                    if not 1 <= limit <= 1000:
                        raise ValueError("事件数量须为 1 到 1000")
                    params["event_limit"] = limit
                raw = self.server.invoke("detail", params)
                if not isinstance(raw, dict):
                    raise TypeError("Invalid backend detail result")
                self._json(200, public_detail(raw))
                return
            self._json(404, {"error": "页面不存在"})
        except (ValueError, FileNotFoundError, KeyError) as exc:
            self._json(
                404 if isinstance(exc, (FileNotFoundError, KeyError)) else 400,
                {"error": "记录或请求无效"},
            )
        except TimeoutError as exc:
            self._json(504, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500, {"error": "无法读取研究状态，请稍后重试"})

    def do_POST(self) -> None:
        if not self._safe_origin() or not secrets.compare_digest(
            self.headers.get("X-RSI-Token", ""), self.server.csrf_token
        ):
            self._json(403, {"error": "请求校验失败，请刷新工作台"})
            return
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get_content_type() != "application/json"
            ):
                self._json(415, {"error": "请求必须为 JSON"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(413, {"error": "请求内容过大或为空"})
                return
            params = json.loads(self.rfile.read(length))
            if not isinstance(params, dict):
                raise ValueError("JSON body must be an object")
            parts, _ = self._path()
            if parts == ["api", "runs"]:
                action = "create"
                if set(params) - {"message", "message_id", "model_settings"}:
                    raise ValueError("请直接发送研究需求，由 Interaction 整理配置")
                params = {
                    **_fields(params, "message message_id model_settings"),
                    "conversation": True,
                }
            elif (
                len(parts) == 4 and parts[:2] == ["api", "runs"] and IDENTIFIER.fullmatch(parts[2])
            ):
                action = parts[3]
                allowed = {
                    "chat": "text message_id reply_to recipient",
                    "start": "",
                    "pause": "",
                    "resume": "",
                    "stop": "reason",
                    "retry": "agent_id",
                    "model-settings": "base_url model reasoning_effort api_key",
                }
                if action not in allowed:
                    self._json(404, {"error": "操作不存在"})
                    return
                params = {**_fields(params, allowed[action]), "run_id": parts[2]}
            else:
                self._json(404, {"error": "操作不存在"})
                return
            result = self.server.invoke(action, params)
            if isinstance(result, WorkbenchDownload):
                if action != "export":
                    raise TypeError("Downloads are only supported for explicit export")
                self._download(result)
            else:
                self._json(
                    200, _fields(result, "run_id message_id status candidate_id filename notice")
                )
        except (ValueError, KeyError) as exc:
            # Backend errors should be concise validation messages, never credentials/config.
            self._json(400, {"error": str(exc) if isinstance(exc, ValueError) else "缺少必要信息"})
        except FileNotFoundError:
            self._json(404, {"error": "研究或产物不存在"})
        except TimeoutError as exc:
            self._json(504, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500, {"error": "操作未完成，请刷新确认当前状态"})

    def _download(self, download: WorkbenchDownload) -> None:
        filename = Path(download.filename).name.replace("\r", "").replace("\n", "")
        with download.path.open("rb") as handle:
            self._headers(200, download.media_type, download.path.stat().st_size)
            self.send_header(
                "Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename)
            )
            self.end_headers()
            while chunk := handle.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_OPTIONS(self) -> None:
        self._json(403, {"error": "不接受跨站请求"})
