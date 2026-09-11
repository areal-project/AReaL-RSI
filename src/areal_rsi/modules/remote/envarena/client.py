"""Small asynchronous OpenAPI client; no credentials in records or errors."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from areal_rsi.base.storage import json_bytes


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def reference(value: Any) -> dict[str, str]:
    if isinstance(value, str) and value.count("@") == 1:
        value = dict(zip(("key", "version"), value.split("@"), strict=True))
    if (
        not isinstance(value, dict)
        or set(value) != {"key", "version"}
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", v)
            for v in value.values()
        )
    ):
        raise ValueError("Arena 引用必须是固定的 key@version")
    if value["version"] == "latest":
        raise ValueError("remote 实验必须固定版本，不能使用 latest")
    return dict(value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(
                r"(?i)(api.?key|authorization|^auth|(?:^|_)token$|cookie|password|secret|custom.headers)",
                k,
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Tool observations may contain shell-expanded credentials as plain text,
        # where key-name-only recursive redaction is insufficient.
        value = re.sub(
            r"(?i)\b((?:OPENAI|ANTHROPIC|LLM|MODEL|ARENA|ENVARENA)[A-Z0-9_]*"
            r"(?:API_KEY|TOKEN|PASSWORD|SECRET))([ \t]*[=:][ \t]*)([^\s,;}\]]+)",
            r"\1\2[redacted]",
            value,
        )
        value = re.sub(
            r"(?i)\b(authorization[ \t]*[=:][ \t]*bearer[ \t]+)[A-Za-z0-9._~+/=-]+",
            r"\1[redacted]",
            value,
        )
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", value)
    return value


class ArenaError(RuntimeError):
    def __init__(self, status: int, method: str, path: str):
        self.status = status
        # Response bodies may contain upstream credentials; never echo them.
        super().__init__(f"Arena {method} {path.split('?')[0]} failed (HTTP {status})")


class ArenaRedirect(RuntimeError):
    """A control-plane redirect that must not receive the bearer credential."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise ArenaRedirect("Arena 控制面重定向被拒绝，请配置最终 API 地址")


@dataclass(frozen=True)
class ArenaConnection:
    base_url: str
    token: str = field(repr=False)
    timeout: float = 30

    @classmethod
    def from_environment(cls) -> ArenaConnection:
        profile = os.environ.get("ARENA_PROFILE", "prod")
        path = Path.home() / ".envarena" / "config.toml"
        saved = tomllib.loads(path.read_text()).get(profile, {}) if path.exists() else {}
        base = (
            os.environ.get("ARENA_BASE_URL")
            or saved.get("baseurl")
            or ("https://htnn-gateway.alipay.com/envarena/openapi/v1")
        )
        token = os.environ.get("ARENA_OPENAPI_TOKEN") or os.environ.get("ENVARENA_API_TOKEN")
        token = token or saved.get("token", "")
        try:
            parts = urlsplit(base)
            valid = parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0
            valid = valid and not any((parts.username, parts.password, parts.query, parts.fragment))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("ARENA_BASE_URL 必须是无凭证、无查询参数的 HTTP(S) 地址")
        if not token:
            raise ValueError("请设置 ARENA_OPENAPI_TOKEN 或先执行 arena token set")
        base = base.rstrip("/")
        if not base.endswith("/openapi/v1"):
            base += "/openapi/v1"
        return cls(base, token)


class ArenaClient:
    def __init__(self, connection: ArenaConnection):
        self.connection = connection

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        return self._send(
            method, path, json_bytes(body) if body is not None else None, "application/json"
        )

    def _send(
        self,
        method: str,
        path: str,
        data: bytes | None,
        content_type: str,
        *,
        binary: bool = False,
    ) -> Any:
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("Arena API 必须使用相对控制面路径")
        request = Request(
            self.connection.base_url + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self.connection.token}",
                "Content-Type": content_type,
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with build_opener(NoRedirect()).open(
                request, timeout=self.connection.timeout
            ) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024:
                    raise RuntimeError("Arena 响应超过 64 MiB，请分页读取")
                return raw if binary else json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise ArenaError(exc.code, method, path) from None
        except (URLError, TimeoutError, OSError):
            raise ArenaError(0, method, path) from None

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        # Writes are deliberately never automatically retried.
        return await asyncio.to_thread(self._request, method, path, body)

    async def get(self, path: str) -> Any:
        # Gateway deploys and load shedding can briefly answer with redirects or
        # transport/5xx errors. Retry the original idempotent request without ever
        # forwarding the bearer credential to the redirect target.
        for attempt in range(4):
            try:
                return await self.request("GET", path)
            except ArenaRedirect:
                if attempt == 3:
                    raise
            except ArenaError as exc:
                if attempt == 3 or exc.status not in {0, 429, 500, 502, 503, 504}:
                    raise
            await asyncio.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    async def post(self, path: str, body: Any = None) -> Any:
        return await self.request("POST", path, body if body is not None else {})

    async def job_summary(self, job_id: str) -> dict[str, Any]:
        value = await self.get(f"/jobs/{segment(job_id)}/summary")
        job = dict(value.get("job", value))
        if "task_counts" in value:
            job["task_counts"] = value["task_counts"]
        return job

    async def artifact(self, task_id: str, name: str, attempt: int) -> bytes:
        data = await asyncio.to_thread(
            self._send,
            "GET",
            f"/tasks/{segment(task_id)}/artifacts/{segment(name)}?attempt={attempt}",
            None,
            "application/json",
            binary=True,
        )
        if not isinstance(data, bytes):
            raise ValueError("Arena artifact 必须返回原始文件内容")
        return data

    async def post_file(
        self, path: str, filename: str, content: bytes, fields: dict[str, str] | None = None
    ) -> Any:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError("无效上传文件名")
        boundary = "rsi" + uuid.uuid4().hex
        chunks = []
        for name, value in (fields or {}).items():
            chunks.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode()
            )
        chunks.extend(
            [
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                ).encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        return await asyncio.to_thread(
            self._send, "POST", path, b"".join(chunks), f"multipart/form-data; boundary={boundary}"
        )

    async def upload(self, filename: str, content: bytes) -> str:
        result = await self.post_file("/uploads", filename, content)
        uri = result.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("Arena upload 未返回 uri")
        return uri

    async def import_skill(
        self, namespace: str, name: str, description: str, content: str
    ) -> dict[str, str]:
        document = (
            f"---\nname: {json.dumps(name)}\ndescription: {json.dumps(description)}"
            f"\n---\n\n{content}\n"
        )
        ref = {"key": f"rsi-{namespace}-skill-{fingerprint(document)[:20]}", "version": "1.0.0"}
        try:
            await self.version("skills", ref)
            return ref
        except ArenaError as exc:
            if exc.status != 404:
                raise
        try:
            await self.post_file(
                "/skills/import",
                "SKILL.md",
                document.encode(),
                {
                    "key": ref["key"],
                    "version": ref["version"],
                    "publish": "true",
                },
            )
        except ArenaError as exc:
            if exc.status != 409:
                raise
        await self.version("skills", ref)
        return ref

    async def version(self, kind: str, ref: Any) -> dict[str, Any]:
        ref = reference(ref)
        value = await self.get(f"/{kind}/{ref['key']}/versions/{ref['version']}")
        if not isinstance(value, dict) or value.get("status") != "PUBLISHED":
            raise ValueError(f"Arena {kind} 引用未发布")
        if value.get("key") != ref["key"] or value.get("version") != ref["version"]:
            raise ValueError("Arena registry 返回了不同版本")
        return value

    async def items(self, path: str, key: str, **params: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self.get(
                path + "?" + urlencode({**params, "limit": 100, "offset": offset})
            )
            values = page
            total = None
            if isinstance(page, dict):
                values = page.get(key, page.get("items"))
                total = page.get("total")
            if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                raise ValueError("Arena 返回了无效的分页结果")
            if total is not None and (type(total) is not int or total < offset + len(values)):
                raise ValueError("Arena 返回了无效的分页总数")
            if offset and values and values[0] in rows:
                raise ValueError("Arena 分页未前进，不能使用不完整任务集")
            rows.extend(values)
            if total is not None:
                if len(rows) >= total:
                    return rows
                if not values:
                    raise ValueError("Arena 分页未前进，不能使用不完整任务集")
            elif len(values) < 100:
                return rows
            offset += len(values)

    async def ensure_version(
        self, kind: str, namespace: str, spec: dict[str, Any]
    ) -> dict[str, str]:
        """Content-derived entity names make interrupted asset publication resumable."""
        digest = fingerprint(spec)
        spec = copy.deepcopy(spec)
        spec.setdefault("metadata", {})["rsi_spec_sha256"] = digest
        ref = {"key": f"rsi-{namespace}-{kind}-{digest[:20]}", "version": "1.0.0"}
        path = f"/{kind}/{ref['key']}"
        try:
            entity = await self.get(path)
        except ArenaError as exc:
            if exc.status != 404:
                raise
            try:
                await self.post(
                    f"/{kind}",
                    {
                        "key": ref["key"],
                        "name": ref["key"],
                        "description": f"RSI configuration sha256:{digest}",
                        "tags": ["source:areal-rsi", f"rsi-digest:{digest}"],
                    },
                )
            except ArenaError as create_error:
                if create_error.status != 409:
                    raise
            entity = await self.get(path)
        try:
            version = await self.get(path + "/versions/1.0.0")
        except ArenaError as exc:
            if exc.status != 404:
                raise
            if f"rsi-digest:{digest}" not in entity.get("tags", []):
                raise ValueError("远端资源身份冲突，拒绝创建版本") from None
            try:
                await self.post(path + "/versions", {"version": "1.0.0", "spec": spec})
            except ArenaError as create_error:
                if create_error.status != 409:
                    raise
            version = await self.get(path + "/versions/1.0.0")
        if version.get("status") == "DRAFT":
            if f"rsi-digest:{digest}" not in entity.get("tags", []):
                raise ValueError("远端资源身份冲突，拒绝发布")
            # A digest label is not proof of content: drafts can be edited in place.
            if not matching_spec(kind, version.get("spec"), spec):
                raise ValueError("远端草稿内容与请求不符，拒绝发布")
            try:
                await self.post(path + "/versions/1.0.0/publish")
            except ArenaError as exc:
                if exc.status != 409:
                    raise
        # EnvArena owns published-version immutability. Reuse requires only
        # the exact key/version and PUBLISHED status, not another spec comparison.
        await self.version(kind, ref)
        return ref


def matching_spec(kind: str, actual: Any, expected: dict[str, Any]) -> bool:
    """Compare draft content while accepting the server's optional null env_refs."""
    if not isinstance(actual, dict):
        return False
    actual, expected = dict(actual), dict(expected)
    if kind == "benchmarks":
        for value in (actual, expected):
            # Case Benchmarks may serialize their unused alternative as null.
            # Nonempty env_refs and all other differences still fail comparison.
            if value.get("case_refs") and value.get("env_refs") is None:
                value.pop("env_refs", None)
    return bool(actual == expected)


def segment(value: str) -> str:
    return quote(value, safe="")
