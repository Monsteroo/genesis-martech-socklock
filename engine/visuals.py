"""Image generation providers.

A provider has `kind` ("image" or "video"), `name`, `available() -> (bool, reason)` and
`generate(prompt, dest) -> Path`. Built in: openai (the OpenAI Images API with the user's key),
demo (a bundled sample frame) and NoImages (nothing configured). Other providers, for example HeyGen for
video, plug in through register_provider(). There is no automatic fallback between providers."""
import base64
import math
import os
import shutil
import tempfile
import time
from pathlib import Path

from engine.llm import LLMError
from engine.openai_api import OpenAIError, request_json

MAX_IMAGE_PROMPT = 4000
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "medium"
IMAGE_API_TIMEOUT = 300
MAX_IMAGE_B64 = 40_000_000
PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])
DEMO_FRAME = Path(__file__).resolve().parent.parent / "samples" / "demo" / "frame.png"
NO_TEXT = "No text, no logos, no watermarks. Vertical 9:16 composition."


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


register_provider("demo", "image", DemoImageProvider)
register_provider("openai", "image", OpenAIImageProvider)
