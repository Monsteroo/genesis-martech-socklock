"""Image generation providers.

A provider has `kind` ("image" or "video"), `name`, `available() -> (bool, reason)` and
`generate(prompt, dest) -> Path`. Built in: codex (the user's ChatGPT subscription through the Codex CLI),
demo (a bundled sample frame) and NoImages (nothing configured). Other providers, for example HeyGen for
video, plug in through register_provider(). There is no automatic fallback between providers."""
import base64
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from engine.llm import LLMError, clean_env, codex_binary, require_login, subprocess_runner
from engine.openai_api import OpenAIError, request_json

IMAGE_TIMEOUT = 300
MAX_IMAGE_PROMPT = 4000
IMAGE_SANDBOX = "read-only"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "medium"
IMAGE_API_TIMEOUT = 300
MAX_IMAGE_B64 = 40_000_000
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])
DEMO_FRAME = Path(__file__).resolve().parent.parent / "samples" / "demo" / "frame.png"
NO_TEXT = "No text, no logos, no watermarks. Vertical 9:16 composition."
INSTRUCTION = (
    "Generate ONE image with your built-in image generation tool. "
    "Do not run any other commands. Reply only with the saved file path.\n\nImage description:\n"
)


def _atomic_copy(src, dest):
    """Copy to a unique .part file, then rename, so an interrupted copy never looks like a finished image."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def _atomic_write(dest, data):
    """Write bytes to a unique .part file, then rename, so an interrupted write never looks like a finished image."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def _env_float(name, default=0):
    try:
        value = float(os.environ.get(name) or default)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, 0), 60)


class VisualsUnavailable(LLMError):
    """Readable, user-facing failure."""


def frame_prompt(shot, original, style):
    return (
        "Storyboard frame for a social ad of a magnetic-socks brand (SockLock). "
        f"Scene: {shot}\nPain it addresses: {original['pain']}\nVisual style: {style}\n{NO_TEXT}"
    )


def variant_prompt(variant, original, style):
    return (
        "Key visual for a social ad of a magnetic-socks brand (SockLock).\n"
        f"Hook: {variant['hook']}\nOpening line: {variant['first_line']}\n"
        f"On-screen idea (do NOT render this text in the image): {variant['onscreen']}\n"
        f"Pain it addresses: {original['pain']}\nVisual style: {style}\n{NO_TEXT}"
    )


class NoImages:
    kind = "image"
    name = "none"

    def available(self):
        return False, "Провайдер зображень не налаштовано."

    def generate(self, prompt, dest):
        raise VisualsUnavailable(self.available()[1])


class DemoImageProvider:
    kind = "image"
    name = "demo"

    def __init__(self, frame=DEMO_FRAME, delay=None):
        self.frame = Path(frame)
        self.delay = delay if delay is not None else _env_float("DEMO_IMAGE_DELAY")

    def available(self):
        if self.frame.is_file():
            return True, ""
        return False, f"Немає демо-зображення ({self.frame.name})."

    def generate(self, prompt, dest):
        ok, reason = self.available()
        if not ok:
            raise VisualsUnavailable(reason)
        if self.delay > 0:
            time.sleep(self.delay)
        return _atomic_copy(self.frame, dest)


def _thread_id(stdout):
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def _last_error(stdout):
    """The last error message Codex reported as a JSONL event (`error` or `turn.failed`), or None."""
    found = None
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "error":
            message = event.get("message")
        elif event.get("type") == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else None
        else:
            continue
        if isinstance(message, str) and message.strip():
            found = message.strip()
    return found


class CodexImageProvider:
    kind = "image"
    name = "codex"

    def __init__(self, runner=subprocess_runner, binary=None, codex_home=None):
        self.runner = runner
        self.binary = binary or codex_binary()
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def available(self):
        if not self.binary:
            return False, "Codex CLI не знайдено (задайте CODEX_BIN)."
        env = clean_env()
        try:
            require_login(self.runner, self.binary, env)
        except LLMError as exc:
            return False, str(exc)
        try:
            res = self.runner([self.binary, "features", "list"], env, 30, None)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, f"Не вдалося перевірити функції Codex: {exc}"
        text = (res.stdout or "") + (res.stderr or "")
        if res.returncode == 0 and re.search(r"^image_generation\s+\S+\s+true\s*$", text, re.M):
            return True, ""
        return False, "У Codex вимкнена функція image_generation (перевірте `codex features list`)."

    def generate(self, prompt, dest):
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMError("Порожній опис зображення.")
        if len(prompt) > MAX_IMAGE_PROMPT:
            raise LLMError(f"Опис зображення задовгий ({len(prompt)} символів, ліміт {MAX_IMAGE_PROMPT}).")
        env = clean_env()
        require_login(self.runner, self.binary, env)
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "answer.txt"
            args = [
                self.binary, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--sandbox", IMAGE_SANDBOX, "--json", "--cd", tmp, "-o", str(out_file), INSTRUCTION + prompt,
            ]
            try:
                res = self.runner(args, env, IMAGE_TIMEOUT, tmp)
            except subprocess.TimeoutExpired:
                raise LLMError(
                    f"Codex не створив зображення за {IMAGE_TIMEOUT} с. "
                    "Автоматичного повтору немає: він витрачає ліміти підписки."
                )
            except OSError as exc:
                raise LLMError(f"Не вдалося запустити Codex ({self.binary}): {exc}")
            if res.returncode != 0:
                tail = _last_error(res.stdout) or (res.stderr or res.stdout or "").strip()[-300:]
                raise LLMError(f"Codex завершився з помилкою: {tail}")
        thread_id = _thread_id(res.stdout)
        if not thread_id or not re.fullmatch(r"[A-Za-z0-9-]+", thread_id):
            raise LLMError("Codex не повідомив ідентифікатор сеансу, зображення не знайдено.")
        folder = self.codex_home / "generated_images" / thread_id
        found = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
        if not found:
            message = (
                f"Codex не створив зображення (шукав у {folder}). Можливо, вичерпано ліміт підписки, "
                "Codex змінив місце збереження або вимкнено image_generation (`codex features list`)."
            )
            reported = _last_error(res.stdout)
            if reported:
                message += f" Codex повідомив: {reported}"
            raise LLMError(message)
        return _atomic_copy(found[-1], dest)


class OpenAIImageProvider:
    kind = "image"
    name = "openai"

    def __init__(self, model=None, size=None, quality=None, transport=None):
        self.model = model or os.environ.get("OPENAI_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL
        self.size = size or os.environ.get("OPENAI_IMAGE_SIZE") or DEFAULT_IMAGE_SIZE
        self.quality = quality or os.environ.get("OPENAI_IMAGE_QUALITY") or DEFAULT_IMAGE_QUALITY
        self.transport = transport

    def available(self):
        if (os.environ.get("OPENAI_API_KEY") or "").strip():
            return True, ""
        return False, "Не задано OPENAI_API_KEY (додайте ключ у файл .env)."

    def generate(self, prompt, dest):
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMError("Порожній опис зображення.")
        if len(prompt) > MAX_IMAGE_PROMPT:
            raise LLMError(f"Опис зображення задовгий ({len(prompt)} символів, ліміт {MAX_IMAGE_PROMPT}).")
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise LLMError("Не вдалося створити папку для зображення.") from None
        data = request_json(
            "POST", "/images/generations",
            {"model": self.model, "prompt": prompt, "size": self.size, "quality": self.quality,
             "n": 1, "output_format": "png"},
            transport=self.transport, timeout=IMAGE_API_TIMEOUT,
        )
        try:
            encoded = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError):
            raise OpenAIError("OpenAI не повернув зображення (у відповіді немає data[0].b64_json).") from None
        if not isinstance(encoded, str):
            raise OpenAIError("OpenAI повернув пошкоджені дані зображення.")
        if len(encoded) > MAX_IMAGE_B64:
            raise OpenAIError("OpenAI повернув надто великі дані зображення.")
        try:
            png = base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, TypeError):
            raise OpenAIError("OpenAI повернув пошкоджені дані зображення.") from None
        if not png.startswith(PNG_MAGIC):
            raise OpenAIError("OpenAI повернув не PNG.")
        return _atomic_write(dest, png)


_REGISTRY = {}


def register_provider(name, kind, factory):
    _REGISTRY[(kind, name)] = factory


def get_provider(name, kind="image", **kw):
    factory = _REGISTRY.get((kind, name))
    if factory is None:
        raise VisualsUnavailable(f"Немає провайдера {kind}/{name}.")
    return factory(**kw)


register_provider("codex", "image", CodexImageProvider)
register_provider("demo", "image", DemoImageProvider)
register_provider("openai", "image", OpenAIImageProvider)
