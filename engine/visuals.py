"""Image generation providers.

A provider has `kind` ("image" or "video"), `name`, `available() -> (bool, reason)` and
`generate(prompt, dest) -> Path`. Built in: openai (the OpenAI Images API with the user's key),
demo (a bundled sample frame) and NoImages (nothing configured). Other providers, for example HeyGen for
video, plug in through register_provider(). There is no automatic fallback between providers."""
import base64
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from engine.llm import LLMError
from engine.openai_api import OpenAIError, request_json, request_multipart

MAX_IMAGE_PROMPT = 4000
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_IMAGE_QUALITY = "medium"
IMAGE_API_TIMEOUT = 300
MAX_IMAGE_B64 = 40_000_000
MAX_EDIT_SOURCE = 20_000_000
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


SIZES = {"vertical": "1024x1536", "square": "1024x1024", "wide": "1536x1024"}
STYLES = {
    "product": "clean modern product photography, soft natural light",
    "lifestyle": "candid lifestyle photo, natural light, real-life setting",
    "illustration": "flat vector illustration, bold colors, simple shapes",
    "minimal": "minimalist composition, plenty of negative space, plain studio background",
}
VARIATIONS = (
    "from a different camera angle",
    "with different lighting",
    "as a closer crop",
    "as a wider shot",
    "with a different background",
    "with a different color palette",
    "at a different time of day",
    "with a different composition",
    "from a low angle",
    "from a high angle, looking down",
    "with a shallow depth of field",
    "with soft window light",
    "with warm golden-hour light",
    "with cool morning light",
    "in a different season",
    "with a calmer mood",
    "with a more playful mood",
    "with a few different props nearby",
    "in a different room",
    "on a different surface",
    "with a minimal clean background",
    "with a more cluttered, lived-in setting",
    "as a tight macro detail shot",
    "from the side, in profile",
)
NO_TEXT_LINE = "No text, no logos, no watermarks."
KEEP_TEXT_LINE = "Keep any text that is already in the image exactly as it is. Do not add new text, logos or watermarks."
_QUOTED = re.compile(r"«([^»]{1,120})»|\"([^\"]{1,120})\"|“([^”]{1,120})”")
_CAPTION_WORDS = ("підпис", "напис", "текст", "слоган", "caption", "text", "slogan", "headline")


# Markets we sell in. This is the one list to edit: add, remove or re-order entries, nothing else changes.
# status "active" = selling or testing now; "planned" = a suggestion, not a commitment.
MARKETS = {
    "us": {"name_uk": "США", "name": "United States", "lang": "English", "locale": "en-US", "status": "active",
           "notes": "main market"},
    "uk": {"name_uk": "Велика Британія", "name": "United Kingdom", "lang": "English", "locale": "en-GB", "status": "active",
           "notes": "test market, British spelling"},
    "ca": {"name_uk": "Канада", "name": "Canada", "lang": "English", "locale": "en-CA", "status": "active",
           "notes": "bilingual market, Quebec uses fr-CA"},
    "au": {"name_uk": "Австралія", "name": "Australia", "lang": "English", "locale": "en-AU", "status": "active",
           "notes": "test market"},
    "de": {"name_uk": "Німеччина", "name": "Germany", "lang": "German", "locale": "de-DE", "status": "planned",
           "notes": "planned"},
    "fr": {"name_uk": "Франція", "name": "France", "lang": "French", "locale": "fr-FR", "status": "planned",
           "notes": "planned"},
    "es": {"name_uk": "Іспанія", "name": "Spain", "lang": "Spanish", "locale": "es-ES", "status": "planned",
           "notes": "planned"},
}


def localize_label(market_key):
    return "Локалізація: " + MARKETS[market_key]["name_uk"]


def quoted_texts(text):
    return [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text or "")]


def localize_change(market_key, source_text=None):
    """The edit instruction that adapts an image to a market. The source text is written without quote marks,
    so the prompt builder does not force it to be rendered as is."""
    m = MARKETS[market_key]
    text = (f"Adapt the image for the {m['name']} market: translate any caption or text in the image into "
            f"{m['lang']} ({m['locale']}) with local spelling and idioms, exactly spelled and legible, or keep the "
            f"text unchanged if it is already in that language; adapt the setting, props, clothing and interior to "
            f"feel typical for {m['name']}; keep the same subject, composition and format; no logos or watermarks")
    if source_text:
        text += f". The text to translate is: ‘{source_text}’"
    return text


def localize_change_no_text(market_key):
    """Like localize_change, but the result must carry no text at all: the caption is overlaid later from a catalog."""
    m = MARKETS[market_key]
    return (f"Adapt the image for the {m['name']} market: adapt the setting, props, clothing and interior to feel "
            f"typical for {m['name']}; keep the same subject, composition and format. The image must contain no text: "
            f"remove every caption, letter, number, sign and label, leave clean empty space where text was, "
            f"no logos or watermarks")


def _text_rule(*sources):
    """Text on the image is allowed only when the user asks for a caption."""
    joined = " ".join(s for s in sources if s)
    if any(w in joined.lower() for w in ("без підпис", "без тексту", "без напис", "no text")):
        return NO_TEXT_LINE
    quoted = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(joined)]
    if quoted:
        return ("Render this text on the image exactly as written, spelled correctly, "
                "large and legible: " + "; ".join('"%s"' % q for q in quoted)
                + ". No other text, no logos, no watermarks.")
    if any(w in joined.lower() for w in _CAPTION_WORDS):
        return ("Include the text or caption described above, spelled correctly and legible. "
                "No logos, no watermarks.")
    return NO_TEXT_LINE


_NO_TEXT_WORDS = ("без підпис", "без тексту", "без напис", "прибери текст", "прибери підпис",
                  "прибери напис", "no text", "remove text", "remove the text", "remove caption")
_TRAIL_CAPTION = re.compile(r"[\s,;:–-]*(?:підпис|напис|слоган|caption|text|slogan|headline)\w*[\s,;:–-]*$", re.I)


def _without_caption(text):
    """The original idea minus its quoted caption, so an old caption is not rendered again."""
    text = _QUOTED.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _TRAIL_CAPTION.sub("", text).strip(" ,;:") or text


def quick_prompt(prompt, style=None, variation=None):
    parts = [prompt.strip()]
    if style in STYLES:
        parts.append(STYLES[style])
    if variation is not None:
        parts.append(
            "Create a new variation of the same idea, "
            + VARIATIONS[variation % len(VARIATIONS)]
            + ", keeping the subject and style."
        )
    parts.append(_text_rule(prompt))
    return "\n".join(parts)


def modify_prompt(original_prompt, change=None, variation=0, new_size=None, parent_change=None, chained=False,
                  translate=False):
    if change and change.strip():
        action = (f"Modify the provided image: {change.strip()}. "
                  "Keep the main subject, the composition and the style unless the change says otherwise.")
    else:
        action = ("Create a new variation of the provided image, " + VARIATIONS[variation % len(VARIATIONS)]
                  + ". Keep the same subject and style.")
    if chained:
        was = parent_change.strip() if parent_change and parent_change.strip() else "a variation of the original idea"
        action = f"The provided image is already a modified version: {was}.\n{action}"
    original = original_prompt.strip()
    lowered = (change or "").lower()
    if any(w in lowered for w in _NO_TEXT_WORDS):
        rule = "Remove all existing text from the image. " + NO_TEXT_LINE
        original = _without_caption(original)
    elif change and _QUOTED.search(change):
        quoted = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(change)]
        rule = ("Remove all existing text from the image and render only this text exactly as written, "
                "spelled correctly, large and legible: " + "; ".join('"%s"' % q for q in quoted)
                + ". No other text, no logos, no watermarks.")
        original = _without_caption(original)
    elif chained or translate:
        # only the new change decides about text; the original caption may be long gone
        original = _without_caption(original)
        rule = _text_rule(change) if change and any(w in lowered for w in _CAPTION_WORDS) else KEEP_TEXT_LINE
    else:
        rule = _text_rule(change, original_prompt)
    if new_size:
        action += (f"\nRecompose the image for the new format ({new_size}), "
                   "keep the subject complete and not cropped or stretched.")
    return f"{action}\nOriginal idea: {original}\n{rule}"


class NoImages:
    kind = "image"
    name = "none"

    def available(self):
        return False, "Провайдер зображень не налаштовано."

    def generate(self, prompt, dest, size=None):
        raise VisualsUnavailable(self.available()[1])

    def edit(self, prompt, src, dest, size=None):
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

    def generate(self, prompt, dest, size=None):
        ok, reason = self.available()
        if not ok:
            raise VisualsUnavailable(reason)
        if self.delay > 0:
            time.sleep(self.delay)
        return _atomic_copy(self.frame, dest)

    def edit(self, prompt, src, dest, size=None):
        return self.generate(prompt, dest, size)


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

    @staticmethod
    def _check_prompt(prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMError("Порожній опис зображення.")
        if len(prompt) > MAX_IMAGE_PROMPT:
            raise LLMError(f"Опис зображення задовгий ({len(prompt)} символів, ліміт {MAX_IMAGE_PROMPT}).")

    @staticmethod
    def _save_png(data, dest):
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

    def generate(self, prompt, dest, size=None):
        self._check_prompt(prompt)
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise LLMError("Не вдалося створити папку для зображення.") from None
        data = request_json(
            "POST", "/images/generations",
            {"model": self.model, "prompt": prompt, "size": size or self.size, "quality": self.quality,
             "n": 1, "output_format": "png"},
            transport=self.transport, timeout=IMAGE_API_TIMEOUT,
        )
        return self._save_png(data, dest)

    def edit(self, prompt, src, dest, size=None):
        self._check_prompt(prompt)
        src = Path(src)
        try:
            if not src.is_file() or src.stat().st_size > MAX_EDIT_SOURCE:
                raise LLMError("Оригінал не знайдено або він завеликий.")
            content = src.read_bytes()
        except OSError:
            raise LLMError("Не вдалося прочитати оригінал.") from None
        if not content.startswith(PNG_MAGIC):
            raise LLMError("Оригінал має бути PNG-зображенням.")
        dest = Path(dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise LLMError("Не вдалося створити папку для зображення.") from None
        data = request_multipart(
            "/images/edits",
            {"model": self.model, "prompt": prompt, "size": size or self.size, "quality": self.quality, "n": "1"},
            {"image": ("original.png", content, "image/png")},
            transport=self.transport, timeout=IMAGE_API_TIMEOUT,
        )
        return self._save_png(data, dest)


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
