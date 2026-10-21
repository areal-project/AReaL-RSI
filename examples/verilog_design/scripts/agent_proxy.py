"""Restrict Agent egress to model hosts, forwarding through the user's HTTP proxy."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import select
import socket
import socketserver
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset({"chatgpt.com", "api.openai.com", "auth.openai.com", "ab.chatgpt.com"})


@dataclass(frozen=True)
class ProxyEndpoint:
    host: str
    port: int
    token: str

    @classmethod
    def read(cls, path: Path) -> ProxyEndpoint:
        return cls(**json.loads(path.read_text()))


def pipe(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], 300)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return


def header(connection: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    connection.settimeout(30)
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk or len(data) + len(chunk) > 65536:
            raise ValueError("Invalid proxy header")
        data += chunk
    first, remainder = data.split(b"\r\n\r\n", 1)
    return first, remainder


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def start_unix(path: Path, endpoint: ProxyEndpoint) -> UnixServer:
    """Expose one authenticated proxy route on a task-specific Unix socket."""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            try:
                first, remainder = header(self.request)
                authorization = base64.b64encode(endpoint.token.encode())
                first += b"\r\nProxy-Authorization: Basic " + authorization
                with socket.create_connection((endpoint.host, endpoint.port), timeout=30) as remote:
                    remote.sendall(first + b"\r\n\r\n" + remainder)
                    pipe(self.request, remote)
            except (OSError, ValueError):
                return

    server = UnixServer(str(path), Handler)
    path.chmod(0o666)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", default="http://127.0.0.1:1081")
    args = parser.parse_args()
    upstream = urlsplit(args.upstream)
    if upstream.scheme != "http" or not upstream.hostname or not upstream.port:
        parser.error("--upstream must be http://host:port")
    token = secrets.token_urlsafe(32)
    expected = b"Basic " + base64.b64encode(token.encode())

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            try:
                first, remainder = header(self.request)
                lines = first.split(b"\r\n")
                method, destination, _ = lines[0].decode("ascii").split()
                supplied = next(
                    (
                        line.split(b":", 1)[1].strip()
                        for line in lines[1:]
                        if line.lower().startswith(b"proxy-authorization:")
                    ),
                    b"",
                )
                host, separator, port = destination.rpartition(":")
                allowed = (
                    method == "CONNECT"
                    and separator
                    and port == "443"
                    and host.lower() in ALLOWED_HOSTS
                    and secrets.compare_digest(supplied, expected)
                )
                if not allowed:
                    self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    return
                # Strip relay authentication before forwarding to the user's proxy.
                lines = [
                    line for line in lines if not line.lower().startswith(b"proxy-authorization:")
                ]
                with socket.create_connection(
                    (upstream.hostname, upstream.port), timeout=30
                ) as remote:
                    remote.sendall(b"\r\n".join(lines) + b"\r\n\r\n" + remainder)
                    pipe(self.request, remote)
            except (OSError, ValueError, UnicodeError):
                return

    with TCPServer((args.listen, 0), Handler) as server:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as file:
            args.output.chmod(0o600)
            json.dump(asdict(ProxyEndpoint(args.listen, server.server_address[1], token)), file)
        print(f"Model proxy ready on {args.listen}:{server.server_address[1]}", flush=True)
        try:
            server.serve_forever()
        finally:
            args.output.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
