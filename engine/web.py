import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine.library import LibraryError
from engine.llm import LLMError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 100_000
GALLERY_PNG = re.compile(r"/api/g/(g[0-9a-f]{8})\.png")
IMAGE_ROUTE = re.compile(r"/api/image/(o[0-9]{3})/((?:shot-[0-9]{1,6}|variant-[0-9a-f]{8}))\.png")

ROUTES = {
    "/api/pains": lambda e, b: e.pains(b["text"]),
    "/api/concepts": lambda e, b: e.concepts(b["pain"]),
    "/api/build": lambda e, b: e.build_original(b["pain"], b["concept"], b.get("status", "draft")),
    "/api/status": lambda e, b: e.library.set_status(b["id"], b["status"]),
    "/api/variants": lambda e, b: e.variants(b["id"], b["instruction"], int(b.get("count", 5))),
    "/api/localize": lambda e, b: e.localize(b["id"], b["markets"]),
    "/api/compose": lambda e, b: e.compose(b["pain"], b["reuse"], b["new"]),
    "/api/image": lambda e, b: e.image(b),
    "/api/gen": lambda e, b: e.quick_image(b.get("prompt", ""), b.get("format") or "vertical", b.get("style"),
                                           b.get("parent"), b.get("variation", 0)),
    "/api/gallery/delete": lambda e, b: e.delete_gallery(b["ids"]),
}

IMAGE_ROUTES = {"/api/image", "/api/gen"}
NO_LOCK_ROUTES = {"/api/gallery/delete"}
IMAGE_QUEUE_TIMEOUT = 600


def image_workers_from_env():
    try:
        value = int(os.environ.get("IMAGE_WORKERS") or 4)
    except ValueError:
        return 4
    return max(1, min(8, value))


def _price_from_env():
    try:
        return float(os.environ.get("OPENAI_IMAGE_PRICE_USD") or "")
    except ValueError:
        return None


def make_server(engine, port=8780, image_workers=None):
    token = secrets.token_urlsafe(24)
    workers = image_workers or image_workers_from_env()
    busy = threading.Lock()  # text requests run one at a time; images run up to `workers` in parallel
    slots = threading.BoundedSemaphore(workers)
    pages = {
        "/": (WEB_DIR / "generate.html").read_text(encoding="utf-8").replace("__TOKEN__", token),
        "/workflow": (WEB_DIR / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _host_ok(self):
            host = self.headers.get("Host", "")
            port_ = self.server.server_port
            return host in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra_headers=None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, code, data, ctype, extra_headers=None, cache="no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _token_ok(self):
            sent = self.headers.get("X-Token", "").encode("utf-8", "replace")
            return secrets.compare_digest(sent, token.encode("utf-8"))

        def _json(self, code, payload):
            self._send(code, json.dumps(payload, ensure_ascii=False))

        def do_GET(self):
            try:
                self._get()
            except OSError:
                self._json(400, {"ok": False, "error": "Помилка файлової системи"})
            except Exception:
                self._json(500, {"ok": False, "error": "Внутрішня помилка сервера"})

        def _get(self):
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "Host не дозволено"})
            url = urlparse(self.path)
            if url.path in pages:
                return self._send(200, pages[url.path], "text/html; charset=utf-8", {"X-Frame-Options": "DENY"})
            if url.path == "/api/originals":
                status = parse_qs(url.query).get("status", [None])[0]
                try:
                    return self._json(200, {"ok": True, "data": engine.library.find(status or None)})
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
            if url.path == "/api/visuals":
                ok, reason = engine.images.available()
                return self._json(200, {"ok": True, "data": {"available": ok, "reason": reason}})
            if url.path == "/api/config":
                images = engine.images
                return self._json(200, {"ok": True, "data": {
                    "workers": workers,
                    "provider": getattr(images, "name", ""),
                    "image_model": getattr(images, "model", ""),
                    "price_usd": _price_from_env(),
                }})
            if url.path == "/api/images":
                original_id = parse_qs(url.query).get("id", [""])[0]
                try:
                    return self._json(200, {"ok": True, "data": engine.list_images(original_id)})
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
            match = IMAGE_ROUTE.fullmatch(url.path)
            if match:
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                try:
                    path = engine.image_store.path(match.group(1), match.group(2))
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                try:
                    data = path.read_bytes()
                except OSError:
                    return self._json(404, {"ok": False, "error": "Зображення немає"})
                return self._send_bytes(200, data, "image/png")
            if url.path == "/api/gallery":
                return self._json(200, {"ok": True, "data": engine.list_gallery()})
            match = GALLERY_PNG.fullmatch(url.path)
            if match:
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                try:
                    file = engine.gallery.path(match.group(1))
                    info = file.stat()
                    etag = f'"{info.st_mtime_ns}-{info.st_size}"'
                    if self.headers.get("If-None-Match") == etag:
                        self.send_response(304)
                        self.send_header("ETag", etag)
                        self.send_header("Cache-Control", "private, no-cache")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    data = file.read_bytes()
                except OSError:
                    return self._json(404, {"ok": False, "error": "Зображення немає"})
                return self._send_bytes(200, data, "image/png", {"ETag": etag}, cache="private, no-cache")
            if url.path == "/api/gallery/zip":
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                ids = [i for i in parse_qs(url.query).get("ids", [""])[0].split(",") if i]
                try:
                    data = engine.gallery_zip(ids)
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                return self._send_bytes(200, data, "application/zip", {
                    "Content-Disposition": 'attachment; filename="images.zip"'})
            self._json(404, {"ok": False, "error": "Не знайдено"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"ok": False, "error": "Host не дозволено"})
            if not self._token_ok():
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
            path = urlparse(self.path).path
            route = ROUTES.get(path)
            if route is None:
                return self._json(404, {"ok": False, "error": "Не знайдено"})
            is_image = path in IMAGE_ROUTES
            lock = None if path in NO_LOCK_ROUTES else (slots if is_image else busy)
            if lock is None:
                acquired = True
            elif is_image:
                acquired = lock.acquire(timeout=IMAGE_QUEUE_TIMEOUT)
            else:
                acquired = lock.acquire(blocking=False)
            if not acquired:
                message = "Усі слоти генерації зайняті, спробуйте пізніше." if is_image else "Інший запит ще виконується."
                return self._json(409, {"ok": False, "error": message})
            try:
                self._json(200, {"ok": True, "data": route(engine, body)})
            except OSError:  # never echo str(exc): it can hold file paths
                self._json(400, {"ok": False, "error": "Помилка файлової системи"})
            except (KeyError, TypeError):  # never echo str(exc): it can hold internals
                self._json(400, {"ok": False, "error": "Некоректний запит."})
            except (LLMError, LibraryError, ValueError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception:  # a bug must not kill the connection silently or hold a lock
                self._json(500, {"ok": False, "error": "Внутрішня помилка сервера"})
            finally:
                if lock is not None:
                    lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = token
    return server
