import hashlib
import re
from pathlib import Path

from engine.library import LibraryError

_ID = re.compile(r"o[0-9]{3}")
_KEY = re.compile(r"shot-[0-9]{1,6}|variant-[0-9a-f]{8}")


def variant_key(hook):
    return "variant-" + hashlib.sha1(hook.strip().casefold().encode("utf-8")).hexdigest()[:8]


def shot_key(n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise LibraryError(f"Некоректний номер кадру: {n!r}")
    return f"shot-{n}"


def check(original_id, key):
    if not (isinstance(original_id, str) and _ID.fullmatch(original_id)):
        raise LibraryError(f"Некоректний id оригіналу: {original_id!r}")
    if not (isinstance(key, str) and _KEY.fullmatch(key)):
        raise LibraryError(f"Некоректний ключ зображення: {key!r}")


class ImageStore:
    """library/images/<id>/<key>.png. Ids and keys are validated before any path is built."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, original_id, key):
        check(original_id, key)
        return self.root / original_id / f"{key}.png"

    def exists(self, original_id, key):
        return self.path(original_id, key).is_file()

    def keys(self, original_id):
        check(original_id, "shot-1")  # validates the id only
        folder = self.root / original_id
        if not folder.is_dir():
            return []
        stems = [p.stem for p in folder.glob("*.png") if _KEY.fullmatch(p.stem)]
        return sorted(stems, key=lambda k: (0, int(k[5:]), "") if k.startswith("shot-") else (1, 0, k))
