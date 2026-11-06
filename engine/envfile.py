"""Tiny .env loader (no dependencies). Values are never printed; OS variables that are set win."""
import os
from pathlib import Path


def parse(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key.replace("_", "").isalnum():
            values[key] = value
    return values


def load_dotenv(path, environ=None):
    """Load KEY=VALUE pairs into `environ` (default os.environ) without overriding non-empty values. Returns the names loaded."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse(path.read_text(encoding="utf-8")).items():
        if not environ.get(key):
            environ[key] = value
            loaded.append(key)
    return loaded
