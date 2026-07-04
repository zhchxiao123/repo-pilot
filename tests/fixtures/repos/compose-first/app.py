import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                conn.execute("SELECT 1")
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
