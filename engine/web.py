import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import captions, visuals
from engine.library import LibraryError
from engine.llm import LLMError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 100_000
GALLERY_PNG = re.compile(r"/api/g/(g[0-9a-f]{8})\.png")
VIDEO_MP4 = re.compile(r"/api/v/(v[0-9a-f]{8})\.mp4")
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
STREAM_CHUNK = 1 << 20

ROUTES = {
    "/api/gen": lambda e, b: e.quick_image(b.get("prompt", ""), b.get("format") or "vertical", b.get("style")),
    "/api/modify": lambda e, b: e.modify_image(b.get("parent"), b.get("change", ""), b.get("variation", 0), b.get("format")),
    "/api/localize": lambda e, b: e.localize_image(b.get("parent"), b.get("market"), b.get("format"), b.get("no_text")),
    "/api/animate": lambda e, b: e.animate(b.get("parent"), b.get("motion", ""), b.get("duration", 5), b.get("format")),
    "/api/videos/trash": lambda e, b: e.trash_videos(b["ids"]),
    "/api/videos/restore": lambda e, b: e.restore_videos(b["ids"]),
    "/api/videos/delete": lambda e, b: e.delete_videos(b["ids"]),
    "/api/gallery/delete": lambda e, b: e.delete_gallery(b["ids"]),
    "/api/gallery/trash": lambda e, b: e.trash_gallery(b["ids"]),
    "/api/gallery/restore": lambda e, b: e.restore_gallery(b["ids"]),
    "/api/captions/translate": lambda e, b: e.captions.translate(b.get("ids"), b.get("locales")),
}
PUT_ROUTES = {
    "/api/captions": lambda e, b: e.captions.put(b.get("locale"), b.get("messages"), b.get("status"), b.get("delete")),
}
VENDOR = {"lingui-core.mjs": "text/javascript; charset=utf-8"}   # exact whitelist for /vendor/<file>
OVERLAY_PATH = "/api/gallery/overlay"
MAX_OVERLAY = 12 * 1024 * 1024

IMAGE_ROUTES = {"/api/gen", "/api/modify", "/api/localize"}
NO_LOCK_ROUTES = {"/api/gallery/delete"}
IMAGE_QUEUE_TIMEOUT = 600
VIDEO_ROUTES = {"/api/animate"}
VIDEO_QUEUE_TIMEOUT = 900


def video_workers_from_env():
    try:
        value = int(os.environ.get("VIDEO_WORKERS") or 2)
    except ValueError:
        return 2
    return max(1, min(4, value))


def _clip_price_from_env():
    try:
        price = float(os.environ.get("HEYGEN_CLIP_PRICE_USD") or "")
    except ValueError:
        return None
    return price if price == price and 0 <= price < 1e6 else None


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


def make_server(engine, port=8780, image_workers=None, video_workers=None):
    token = secrets.token_urlsafe(24)
    workers = image_workers or image_workers_from_env()
    slots = threading.BoundedSemaphore(workers)
    vworkers = video_workers or video_workers_from_env()
    vslots = threading.BoundedSemaphore(vworkers)
    pages = {"/": (WEB_DIR / "generate.html").read_text(encoding="utf-8").replace("__TOKEN__", token)}

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
            if url.path.startswith("/vendor/"):
                name = url.path[len("/vendor/"):]
                if name not in VENDOR:
                    return self._json(404, {"ok": False, "error": "Не знайдено"})
                try:
                    data = (WEB_DIR / "vendor" / name).read_bytes()
                except OSError:
                    return self._json(404, {"ok": False, "error": "Не знайдено"})
                return self._send_bytes(200, data, VENDOR[name], cache="no-cache")
            if url.path in ("/api/captions", "/api/captions.po"):
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                try:
                    if url.path == "/api/captions":
                        return self._json(200, {"ok": True, "data": engine.captions.get_all()})
                    locale = parse_qs(url.query).get("locale", [""])[0]
                    text = engine.captions.po(locale)
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                return self._send(200, text, "text/plain; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{locale}.po"',
                                   "X-Content-Type-Options": "nosniff"})
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
                    "video": {**{k: v for k, v in engine.video_info().items() if k != "reason"}, "workers": vworkers,
                              "price_usd": _clip_price_from_env()},
                    "markets": [{"key": k, **{f: m[f] for f in ("name_uk", "name", "lang", "status")},
                                 "catalog_locale": captions.locale_for_market(k)}
                                for k, m in visuals.MARKETS.items()],
                }})
            if url.path == "/api/gallery":
                query = parse_qs(url.query)
                flag = query.get("trashed", ["0"])[0]
                try:
                    if flag not in ("0", "1"):
                        raise LibraryError("Некоректний параметр trashed.")
                    data = engine.list_gallery(query.get("kind", [None])[0], query.get("parent", [None])[0],
                                               query.get("root", [None])[0], flag == "1")
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                return self._json(200, {"ok": True, "data": data})
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
            if url.path == "/api/videos":
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                query = parse_qs(url.query)
                flag = query.get("trashed", ["0"])[0]
                try:
                    if flag not in ("0", "1"):
                        raise LibraryError("Некоректний параметр trashed.")
                    data = engine.list_videos(query.get("parent", [None])[0], flag == "1")
                except LibraryError as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})
                return self._json(200, {"ok": True, "data": data})
            match = VIDEO_MP4.fullmatch(url.path)
            if match:
                if not self._token_ok():
                    return self._json(403, {"ok": False, "error": "Невірний токен"})
                return self._video(match.group(1))
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

        def _video(self, vid):
            try:
                file = engine.videos.path(vid)
                info = file.stat()
                etag = f'"{info.st_mtime_ns}-{info.st_size}"'
                size = info.st_size
                start, end, code = 0, size - 1, 200
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "private, no-cache")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                rng = RANGE.fullmatch(self.headers.get("Range", "").strip())
                if rng and (rng.group(1) or rng.group(2)):   # a single range only; anything else gets the whole file
                    if rng.group(1):
                        start = int(rng.group(1))
                        end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                    else:
                        start, end = max(0, size - int(rng.group(2))), size - 1
                    if start >= size or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    code = 206
                handle = file.open("rb")
            except OSError:
                return self._json(404, {"ok": False, "error": "Відео немає"})
            with handle:
                self.send_response(code)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, no-cache")
                self.send_header("ETag", etag)
                self.send_header("Accept-Ranges", "bytes")
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                try:
                    handle.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = handle.read(min(STREAM_CHUNK, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except OSError:
                    self.close_connection = True   # the client went away; headers are already sent

        def do_POST(self):
            self._write("POST")

        def do_PUT(self):
            self._write("PUT")

        def _write(self, method):
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
            path = urlparse(self.path).path
            if method == "POST" and path == OVERLAY_PATH:
                return self._overlay(length)
            if length > MAX_BODY:
                return self._json(413, {"ok": False, "error": "Запит завеликий"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"ok": False, "error": "Некоректний JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "Очікується JSON-об'єкт"})
            route = (ROUTES if method == "POST" else PUT_ROUTES).get(path)
            if route is None:
                return self._json(404, {"ok": False, "error": "Не знайдено"})
            lock = slots if path in IMAGE_ROUTES else vslots if path in VIDEO_ROUTES else None
            wait = VIDEO_QUEUE_TIMEOUT if path in VIDEO_ROUTES else IMAGE_QUEUE_TIMEOUT
            if lock is not None and not lock.acquire(timeout=wait):
                return self._json(409, {"ok": False, "error": "Усі слоти генерації зайняті, спробуйте пізніше."})
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

        def _overlay(self, length):
            """A finished PNG as the raw body; parent, market and caption travel in the query string."""
            if length > MAX_OVERLAY:
                return self._json(413, {"ok": False, "error": "Зображення завелике (до 12 МБ)"})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "image/png":
                return self._json(400, {"ok": False, "error": "Очікується image/png"})
            query = parse_qs(urlparse(self.path).query)
            one = lambda key: (query.get(key) or [None])[0]
            try:
                data = self.rfile.read(length)
                meta = engine.overlay_image(data, one("parent"), one("market"), one("caption"))
            except OSError:
                return self._json(400, {"ok": False, "error": "Помилка файлової системи"})
            except (LibraryError, ValueError) as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            except Exception:
                return self._json(500, {"ok": False, "error": "Внутрішня помилка сервера"})
            self._json(200, {"ok": True, "data": meta})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = token
    return server
