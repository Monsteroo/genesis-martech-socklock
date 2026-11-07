"""Minimal HeyGen API client on urllib.

The key comes from the environment only (HEYGEN_API_KEY) and never appears in output or errors. The base URL is
fixed. Redirects are never followed. POST is never retried automatically. Video files are downloaded without the key
and only from https hosts under heygen.ai / heygen.com."""
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from engine.llm import LLMError
from engine.openai_api import multipart_body

API_BASE = "https://api.heygen.com"
API_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300
MAX_JSON_BYTES = 10_000_000
MAX_VIDEO_BYTES = 200 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")
_URL = re.compile(r"https?://\S+")
_HOST_SUFFIXES = (".heygen.ai", ".heygen.com")


class HeyGenError(LLMError):
    """Readable, user-facing failure. Never contains the API key. `transient` marks failures a GET may retry."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


def redact(text, key=None):
    text = str(text)
    if key:
        text = text.replace(key, "***")
    return _URL.sub(lambda m: m.group(0).split("?", 1)[0] + ("?***" if "?" in m.group(0) else ""), text)


def api_key():
    key = (os.environ.get("HEYGEN_API_KEY") or "").strip()
    if not key:
        raise HeyGenError("Не задано HEYGEN_API_KEY. Додайте ключ у файл .env (рядок HEYGEN_API_KEY=...) або у змінні середовища.")
    if not re.fullmatch(r"[\x21-\x7e]+", key):
        raise HeyGenError("HEYGEN_API_KEY містить недопустимі символи (пробіли, переноси рядка або не-ASCII літери). "
                          "Скопіюйте ключ із платформи ще раз.")
    return key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _read_bounded(stream, limit):
    body = stream.read(limit + 1)
    if len(body) > limit:
        raise HeyGenError("Відповідь HeyGen завелика.")
    return body


def real_transport(request):
    limit = request.get("max_bytes", MAX_JSON_BYTES)
    req = urllib.request.Request(request["url"], data=request.get("body"), method=request["method"], headers=request["headers"])
    try:
        with _opener.open(req, timeout=request["timeout"]) as resp:
            return {"status": resp.status, "body": _read_bounded(resp, limit)}
    except urllib.error.HTTPError as exc:
        try:
            return {"status": exc.code, "body": _read_bounded(exc, MAX_JSON_BYTES)}
        except HeyGenError:
            raise
        except Exception as inner:
            raise HeyGenError(f"Немає зв'язку з HeyGen ({type(inner).__name__}).", transient=True) from None
        finally:
            exc.close()
    except HeyGenError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise HeyGenError(f"Немає зв'язку з HeyGen ({type(exc).__name__}). Автоматичного повтору немає.", transient=True) from None
    except Exception as exc:
        raise HeyGenError(f"Помилка запиту до HeyGen ({type(exc).__name__}). Автоматичного повтору немає.") from None


DEFAULT_TRANSPORT = real_transport

_STATUS_TEXT = {
    400: "Запит відхилено HeyGen (400).",
    401: "Ключ HeyGen не прийнято (401). Перевірте HEYGEN_API_KEY.",
    402: "Недостатньо кредитів HeyGen (402). Поповніть баланс.",
    403: "Доступ заборонено (403): ключ не має потрібних прав (videos:write, assets:write).",
    404: "Ресурс HeyGen не знайдено (404).",
    409: "HeyGen уже обробляє такий запит (409). Зачекайте і перевірте результат.",
    429: "Перевищено ліміт запитів HeyGen (429). Повтору немає: зачекайте.",
}


def _api_message(data):
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return ""


def _http_error(status, data, key):
    base = _STATUS_TEXT.get(status) or (f"Помилка сервера HeyGen ({status})." if status >= 500 else f"Запит відхилено ({status}).")
    detail = redact(_api_message(data), key).strip()[:300]
    return HeyGenError(base + (f" {detail}" if detail else ""), transient=status >= 500 or status == 429)


def _call(method, path, body, content_type, ok_status, transport, extra=None):
    key = api_key()
    headers = {"x-api-key": key, "Content-Type": content_type, "Accept": "application/json",
               "User-Agent": "socklock-creative-engine", **(extra or {})}
    request = {"method": method, "url": API_BASE + path, "headers": headers, "body": body, "timeout": API_TIMEOUT}
    response = (transport or DEFAULT_TRANSPORT)(request)
    try:
        data = json.loads(response["body"].decode("utf-8")) if response["body"] else {}
    except (ValueError, UnicodeDecodeError):
        data = None
    if response["status"] not in ok_status:
        raise _http_error(response["status"], data, key)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise HeyGenError("HeyGen повернув відповідь не у очікуваному форматі.")
    return data["data"]


def check_id(value):
    if not (isinstance(value, str) and _ID.fullmatch(value)):
        raise HeyGenError("HeyGen повернув некоректний ідентифікатор.")
    return value


def create_job(payload, idempotency_key, transport=None):
    """POST /v3/models/videos -> video_id. Sent once, never retried."""
    data = _call("POST", "/v3/models/videos", json.dumps(payload).encode("utf-8"), "application/json", (200, 202), transport,
                 {"Idempotency-Key": idempotency_key})
    return check_id(data.get("video_id"))


def get_job(video_id, transport=None):
    check_id(video_id)
    return _call("GET", f"/v3/models/videos/{video_id}", None, "application/json", (200,), transport)


def upload_asset(png, transport=None):
    body, ctype = multipart_body({}, {"file": ("frame.png", png, "image/png")})
    data = _call("POST", "/v3/assets", body, ctype, (200, 201), transport)
    return check_id(data.get("asset_id"))


def check_download_url(url):
    try:
        parts = urlparse(url) if isinstance(url, str) else None
        host = parts.hostname if parts else None
        port = parts.port if parts else None
    except ValueError:
        parts = host = port = None
    ok = (parts is not None and parts.scheme == "https" and host and parts.username is None and parts.password is None
          and port in (None, 443) and any(host.endswith(s) for s in _HOST_SUFFIXES))
    if not ok:
        raise HeyGenError("HeyGen повернув неприпустиму адресу відео.")


def download(url, transport=None):
    """The signed link carries its own authorization: no API key is sent. Returns the MP4 bytes."""
    check_download_url(url)
    request = {"method": "GET", "url": url, "headers": {"User-Agent": "socklock-creative-engine"}, "body": None,
               "timeout": DOWNLOAD_TIMEOUT, "max_bytes": MAX_VIDEO_BYTES}
    response = (transport or DEFAULT_TRANSPORT)(request)
    if response["status"] != 200:
        raise HeyGenError(f"Не вдалося завантажити відео з HeyGen ({response['status']}). Посилання могло прострочитись.")
    data = response["body"]
    if len(data) > MAX_VIDEO_BYTES:
        raise HeyGenError("Відео від HeyGen завелике.")
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise HeyGenError("HeyGen повернув не MP4.")
    return data
