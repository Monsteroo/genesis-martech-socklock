"""Tiny .env loader (no dependencies). Values are never printed; OS variables that are set win."""
import os
from pathlib import Path


def parse(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == "export":
            line = parts[1]
            if "=" not in line:
                continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        closing = value.find(value[0], 1) if value and value[0] in "\"'" else -1
        if closing > 0:
            value = value[1:closing]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key.replace("_", "").isalnum():
            values[key] = value
    return values


ENGINE_VARS = ("OPENAI_", "IMAGE_WORKERS", "DEMO_IMAGE_DELAY", "DEMO_VIDEO_DELAY", "ENGINE_BACKEND",
               "HEYGEN_", "VIDEO_WORKERS")


def _allowed(key, allowed):
    return allowed is None or any(key.startswith(a) if a.endswith("_") else key == a for a in allowed)


def load_dotenv(path, environ=None, allowed=None):
    """Load KEY=VALUE pairs into `environ` (default os.environ) without overriding non-empty values. Returns the names loaded.
    `allowed` (exact names or prefixes ending in `_`) limits which keys are taken; None means all."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse(path.read_text(encoding="utf-8-sig")).items():
        if _allowed(key, allowed) and not (environ.get(key) or "").strip():
            environ[key] = value
            loaded.append(key)
    return loaded
