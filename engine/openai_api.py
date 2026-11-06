"""Minimal OpenAI API client on urllib.

The key comes from the environment only and never appears in output or errors. The base URL is fixed
(OPENAI_BASE_URL is deliberately ignored). There is no automatic retry."""
import json
import os
import re
import urllib.error
import urllib.request

from engine.llm import MAX_PROMPT, LLMError

API_BASE = "https://api.openai.com/v1"
TEXT_TIMEOUT = 120
_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class OpenAIError(LLMError):
    """Readable, user-facing failure. Never contains the API key."""


def redact(text, key=None):
    text = str(text)
    if key:
        text = text.replace(key, "***")
    return _KEY_LIKE.sub("sk-***", text)


def api_key():
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise OpenAIError(
            "Не задано OPENAI_API_KEY. Додайте ключ у файл .env (рядок OPENAI_API_KEY=...) або у змінні середовища."
        )
    if not re.fullmatch(r"[\x21-\x7e]+", key):
        raise OpenAIError(
            "OPENAI_API_KEY містить недопустимі символи (пробіли, переноси рядка або не-ASCII літери). "
            "Скопіюйте ключ із платформи ще раз."
        )
    return key


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: they would forward the Authorization header to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


MAX_RESPONSE_BYTES = 60_000_000


def _read_bounded(stream):
    body = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise OpenAIError("Відповідь OpenAI завелика.")
    return body


def real_transport(request):
    req = urllib.request.Request(
        request["url"], data=request.get("body"), method=request["method"], headers=request["headers"]
    )
    try:
        with _opener.open(req, timeout=request["timeout"]) as resp:
            return {"status": resp.status, "body": _read_bounded(resp)}
    except urllib.error.HTTPError as exc:
        try:
            return {"status": exc.code, "body": _read_bounded(exc)}
        except OpenAIError:
            raise
        except Exception as inner:
            raise OpenAIError(f"Немає зв'язку з OpenAI API ({type(inner).__name__}). Автоматичного повтору немає.") from None
        finally:
            exc.close()
    except OpenAIError:
        raise
    except (urllib.error.URLError, OSError) as exc:  # TimeoutError is an OSError
        raise OpenAIError(f"Немає зв'язку з OpenAI API ({type(exc).__name__}). Автоматичного повтору немає.") from None
    except Exception as exc:
        raise OpenAIError(f"Помилка запиту до OpenAI API ({type(exc).__name__}). Автоматичного повтору немає.") from None


DEFAULT_TRANSPORT = real_transport

_STATUS_TEXT = {
    401: "Ключ OpenAI не прийнято (401). Перевірте OPENAI_API_KEY.",
    403: "Доступ заборонено (403): ключ не має прав на цю модель або організацію не верифіковано.",
    404: "Модель або адресу не знайдено (404). Перевірте назву моделі (`python3 -m engine models`).",
    429: "Перевищено ліміт запитів або квоту (429). Повтору немає: зачекайте або перевірте баланс.",
}


def _api_message(data):
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return ""


def _http_error(status, data, key):
    base = _STATUS_TEXT.get(status) or (
        f"Помилка сервера OpenAI ({status})." if status >= 500 else f"Запит відхилено ({status})."
    )
    detail = redact(_api_message(data), key).strip()[:300]
    return OpenAIError(base + (f" {detail}" if detail else ""))


def request_json(method, path, payload=None, *, transport=None, timeout=TEXT_TIMEOUT):
    key = api_key()
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = {
        "method": method,
        "url": API_BASE + path,
        "headers": {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "socklock-creative-engine",
        },
        "body": body,
        "timeout": timeout,
    }
    response = (transport or DEFAULT_TRANSPORT)(request)
    try:
        data = json.loads(response["body"].decode("utf-8")) if response["body"] else {}
    except (ValueError, UnicodeDecodeError):
        data = None
    if response["status"] != 200:
        raise _http_error(response["status"], data, key)
    if not isinstance(data, dict):
        raise OpenAIError("OpenAI повернув відповідь не у форматі JSON.")
    return data


def list_models(transport=None):
    data = request_json("GET", "/models", transport=transport, timeout=30)
    items = data.get("data") if isinstance(data.get("data"), list) else []
    return sorted(m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str))


class OpenAIBackend:
    name = "openai"

    def __init__(self, model=None, transport=None, timeout=TEXT_TIMEOUT):
        self.model = model or os.environ.get("OPENAI_TEXT_MODEL") or ""
        self.transport = transport
        self.timeout = timeout

    def run(self, prompt):
        if len(prompt) > MAX_PROMPT:
            raise LLMError(f"Запит задовгий ({len(prompt)} символів, ліміт {MAX_PROMPT}).")
        model = self.model or os.environ.get("OPENAI_TEXT_MODEL") or ""
        if not model:
            raise LLMError(
                "Не задано OPENAI_TEXT_MODEL. Додайте модель у .env; перелік доступних дає `python3 -m engine models`."
            )
        data = request_json(
            "POST", "/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}]},
            transport=self.transport, timeout=self.timeout,
        )
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("OpenAI повернув відповідь без тексту.") from None
        if not isinstance(text, str) or not text.strip():
            raise LLMError("OpenAI повернув порожню відповідь.")
        return text.strip()
