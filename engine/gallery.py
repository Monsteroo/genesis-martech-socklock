import io
import json
import os
import re
import secrets
import tempfile
import time
import zipfile
from pathlib import Path

from engine.library import LibraryError

_ID = re.compile(r"g[0-9a-f]{8}")
MAX_ZIP = 50
MAX_DELETE = 200
KEYS = ("id", "prompt", "size", "parent", "style", "created")


def new_id():
    return "g" + secrets.token_hex(4)


def check(gid):
    if not (isinstance(gid, str) and _ID.fullmatch(gid)):
        raise LibraryError(f"Некоректний id зображення: {gid!r}")


class Gallery:
    """library/gallery/<id>.png + <id>.json. Ids are validated before any path is built."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, gid):
        check(gid)
        return self.root / f"{gid}.png"

    def meta_path(self, gid):
        check(gid)
        return self.root / f"{gid}.json"

    def fresh_id(self):
        while True:
            gid = new_id()
            if not self.path(gid).exists() and not self.meta_path(gid).exists():
                return gid

    def add(self, gid, prompt, size, parent=None, style=None, created=None):
        meta = {"id": gid, "prompt": prompt, "size": size, "parent": parent, "style": style,
                "created": time.time() if created is None else created}
        path = self.meta_path(gid)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return meta

    def get(self, gid):
        path = self.meta_path(gid)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LibraryError("Зображення не знайдено.") from None

    def find(self, limit=200):
        if not self.root.is_dir():
            return []
        items = []
        for meta_file in self.root.glob("g*.json"):
            gid = meta_file.stem
            if not _ID.fullmatch(gid) or not self.path(gid).is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("id") != gid:
                continue
            try:
                created = float(meta.get("created"))
            except (TypeError, ValueError):
                continue
            if created != created or created in (float("inf"), float("-inf")):
                continue
            prompt = meta.get("prompt")
            item = {k: meta.get(k) for k in KEYS}
            item["prompt"] = prompt if isinstance(prompt, str) else ""
            item["created"] = created
            items.append(item)
        items.sort(key=lambda m: (m["created"], m["id"]), reverse=True)
        return items[:limit]

    def delete(self, ids):
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_DELETE:
            raise LibraryError(f"За раз можна видалити не більше {MAX_DELETE} зображень.")
        for gid in ids:
            check(gid)
        deleted = 0
        for gid in ids:
            png = self.path(gid)
            existed = png.exists()
            png.unlink(missing_ok=True)
            self.meta_path(gid).unlink(missing_ok=True)
            deleted += 1 if existed else 0
        return deleted

    def zip_bytes(self, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise LibraryError("Оберіть зображення для завантаження.")
        if len(ids) > MAX_ZIP:
            raise LibraryError(f"За раз можна завантажити не більше {MAX_ZIP} зображень.")
        for gid in ids:
            check(gid)
        buffer = io.BytesIO()
        added = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            for gid in ids:
                png = self.path(gid)
                try:
                    archive.write(png, f"{gid}.png")
                except OSError:
                    continue
                added += 1
        if not added:
            raise LibraryError("Зображень для завантаження не знайдено.")
        return buffer.getvalue()
