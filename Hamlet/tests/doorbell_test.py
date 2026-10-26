import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/py-doorbell'))
from doorbell import ring, state, every

class DoorbellTest(unittest.TestCase):
    def test_state_and_ring(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"id":"message-id"}')
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory:
            os.environ['DOORBELL_STATE_DIR'] = directory
            self.assertEqual(state.get('cursor', 0), 0)
            state.set('cursor', 42)
            self.assertEqual(state.get('cursor'), 42)
            self.assertEqual(json.loads(Path(directory, 'state.json').read_text())['cursor'], 42)
            server = HTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                os.environ.update(DOORBELL_URL=f'http://127.0.0.1:{server.server_port}/api/ring', DOORBELL_TOKEN='signed', DOORBELL_AGENT_ID='abcdef')
                self.assertEqual(ring(prompt='你好', vars={'n': 1}, dedup_key='k')['id'], 'message-id')
                self.assertEqual(received[0][0], 'Bearer signed')
                self.assertEqual(received[0][1]['target'], 'abcdef')
                self.assertEqual(received[0][1]['vars'], {'n': 1})
            finally:
                server.shutdown()
                worker.join()
                server.server_close()
    def test_invalid_interval(self):
        with self.assertRaises(ValueError):
            every(seconds=0)
