"""http_status performs a real HTTP GET, returning the status or None if down.

Exercised against a local stdlib HTTP server — no Docker needed.
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from repo_pilot.executor import http_status


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def test_returns_status_for_a_live_server(http_server):
    assert http_status(http_server, "/health") == 200
    assert http_status(http_server, "/missing") == 404


def test_returns_none_when_nothing_is_listening():
    # port 1 is not listening in the test sandbox
    assert http_status(1, "/", timeout=0.5) is None
