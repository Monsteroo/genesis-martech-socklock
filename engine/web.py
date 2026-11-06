import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine.library import LibraryError
from engine.llm import LLMError

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"
MAX_BODY = 100_000

ROUTES = {
    "/api/pains": lambda e, b: e.pains(b["text"]),
    "/api/concepts": lambda e, b: e.concepts(b["pain"]),
    "/api/build": lambda e, b: e.build_original(b["pain"], b["concept"], b.get("status", "draft")),
    "/api/status": lambda e, b: e.library.set_status(b["id"], b["status"]),
    "/api/variants": lambda e, b: e.variants(b["id"], b["instruction"], int(b.get("count", 5))),
    "/api/localize": lambda e, b: e.localize(b["id"], b["markets"]),
    "/api/compose": lambda e, b: e.compose(b["pain"], b["reuse"], b["new"]),
}


def make_server(engine, port=8765):
    token = secrets.token_urlsafe(24)
    busy = threading.Lock()  # one model request at a time, like the Codex backend requires
    html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _host_ok(self):
            host = self.headers.get("Host", "")
            port_ = self.server.server_port
            return host in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, payload):
            self._send(code, json.dumps(payload, ensure_ascii=False))

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "Host не дозволено"})
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, html, "text/html; charset=utf-8")
            if url.path == "/api/originals":
                status = parse_qs(url.query).get("status", [None])[0]
                try:
                    return self._json(200, {"ok": True, "data": engine.library.find(status or None)})
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
            self._json(404, {"ok": False, "error": "Не знайдено"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "Host не дозволено"})
            if self.headers.get("X-Token") != token:
                return self._json(403, {"ok": False, "error": "Невірний токен"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                return self._json(400, {"ok": False, "error": "Некоректний Content-Length"})
            if length > MAX_BODY:
                return self._json(413, {"ok": False, "error": "Запит завеликий"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"ok": False, "error": "Некоректний JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "Очікується JSON-об'єкт"})
            route = ROUTES.get(urlparse(self.path).path)
            if route is None:
                return self._json(404, {"ok": False, "error": "Не знайдено"})
            if not busy.acquire(blocking=False):
                return self._json(409, {"ok": False, "error": "Інший запит ще виконується."})
            try:
                self._json(200, {"ok": True, "data": route(engine, body)})
            except (LLMError, LibraryError, KeyError, ValueError, TypeError, OSError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            finally:
                busy.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = token
    return server
