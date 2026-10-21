#!/usr/bin/env doorbell-python
"""Local webhook -> ring. Configure WEBHOOK_PORT and your agent's prompt template."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from doorbell import ring

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1048576:
                self.send_error(413)
                return
            event = json.loads(self.rfile.read(length))
            result = ring(vars=event, dedup_key=self.headers.get("X-Event-ID"))
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        except Exception as error:
            self.send_error(400, str(error))

HTTPServer(("127.0.0.1", int(os.environ.get("WEBHOOK_PORT", "9876"))), Handler).serve_forever()
