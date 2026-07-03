"""A minimal server with no recognizable manifest/framework — deterministic
rules can't plan it, so the plan agent must explore and propose."""

import http.server
import socketserver

with socketserver.TCPServer(("", 8000), http.server.SimpleHTTPRequestHandler) as httpd:
    httpd.serve_forever()
