"""ARCA build, sandbox and file-transfer client; credentials stay outside receipts."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    tenant: str
    environment: str
    template_id: str
    control_url: str
    runtime_url: str
    api_key: str = field(repr=False)


class Arca:
    def __init__(self, config: Config):
        self.config = config
        # Internal service calls must not go through the workstation's public proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        # The stable data-plane hostname resolves through the office proxy here.
        self.runtime_http = urllib.request.build_opener()

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        sandbox_id: str | None = None,
    ) -> dict:
        if not path.startswith("/arca/") or "://" in path:
            raise ValueError("Expected an ARCA API path")
        control = not path.startswith(("/arca/api/", "/arca/openapi/v1/"))
        url = (self.config.control_url if control else self.config.runtime_url) + path
        if control:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {"arcaSiteName": "mainsite", "arcaEnvName": self.config.environment}
            )
        headers = {
            "Content-Type": "application/json",
            "x-agent-sandbox-api-key": self.config.api_key,
            "x-agent-sandbox-template-id": self.config.template_id,
        }
        if sandbox_id:
            headers["x-agent-sandbox-id"] = sandbox_id
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            opener = self.http if control else self.runtime_http
            with opener.open(request, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return json.loads(raw)
            except ValueError:
                return {"success": False, "http_status": exc.code, "message": raw[:2000]}

    @staticmethod
    def data(response: dict) -> dict:
        if response.get("success") is not True:
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        return response["data"]

    def upload(self, sandbox_id: str, source: Path, destination: str) -> dict:
        prefix = f"/arca/api/v1/sandbox/{sandbox_id}/oss"
        ticket = self.data(
            self.request("POST", prefix + "/upload/url", {"is_office": True}, sandbox_id)
        )
        # Do not forward the ARCA key or add headers to the presigned OSS PUT.
        url = urllib.parse.urlsplit(ticket["url"])
        connection_type = (
            http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_type(url.hostname, url.port, timeout=180)
        try:
            # urllib would silently add a form Content-Type, invalidating the signature.
            connection.request("PUT", url.path + "?" + url.query, body=source.read_bytes())
            response = connection.getresponse()
            response.read()
            if response.status not in (200, 201, 204):
                raise RuntimeError(f"OSS PUT failed: HTTP {response.status}")
        finally:
            connection.close()
        return self.request(
            "POST",
            prefix + "/upload/notification",
            {"id": ticket["id"], "file_path": destination},
            sandbox_id,
        )

    def download(self, sandbox_id: str, source: str, destination: Path) -> dict:
        ticket = self.data(
            self.request(
                "POST",
                f"/arca/api/v1/sandbox/{sandbox_id}/oss/download/url",
                {"file_path": source, "is_office": True, "zipped": False},
                sandbox_id,
            )
        )
        with self.http.open(ticket["url"], timeout=180) as response:
            destination.write_bytes(response.read())
        return {"success": True, "file": str(destination), "size": destination.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="Private JSON with api_key; otherwise use ARCA_API_KEY",
    )
    parser.add_argument("--output", type=Path, help="Save response without authentication headers")
    parser.add_argument("--sandbox-id")
    sub = parser.add_subparsers(dest="operation", required=True)
    request = sub.add_parser("request")
    request.add_argument("method", choices=["GET", "POST", "DELETE"])
    request.add_argument("path")
    request.add_argument("--body-file", type=Path)
    for name in ["upload", "download"]:
        transfer = sub.add_parser(name)
        transfer.add_argument("source")
        transfer.add_argument("destination")
    args = parser.parse_args()
    raw = json.loads(args.config.read_text())
    key = (
        json.loads(args.credentials_file.read_text())["api_key"]
        if args.credentials_file
        else os.environ["ARCA_API_KEY"]
    )
    client = Arca(
        Config(
            raw["tenant"],
            raw["environment"],
            raw["template_id"],
            raw["control_url"],
            raw["runtime_url"],
            key,
        )
    )
    if args.operation == "request":
        result = client.request(
            args.method,
            args.path,
            json.loads(args.body_file.read_text()) if args.body_file else None,
            args.sandbox_id,
        )
    elif args.sandbox_id is None:
        parser.error("File transfers require --sandbox-id")
    elif args.operation == "upload":
        result = client.upload(args.sandbox_id, Path(args.source), args.destination)
    else:
        result = client.download(args.sandbox_id, args.source, Path(args.destination))
    text = json.dumps(result, ensure_ascii=False, indent=2).replace(key, "[REDACTED]") + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
    raise SystemExit(0 if result.get("success") is True else 1)


if __name__ == "__main__":
    main()
