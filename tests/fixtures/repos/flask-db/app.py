import os
import psycopg
from flask import Flask

app = Flask(__name__)


@app.get("/health")
def health():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "db": "up"}


@app.get("/")
def index():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
