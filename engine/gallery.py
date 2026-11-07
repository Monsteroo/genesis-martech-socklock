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
KEYS = ("id", "prompt", "size", "parent", "root", "market", "style", "created")
KINDS = ("original", "modification")
MAX_CHANGE = 300


def new_id():
    return "g" + secrets.token_hex(4)


def check(gid):
    if not (isinstance(gid, str) and _ID.fullmatch(gid)):
        raise LibraryError("Некоректний id зображення.")


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
        for _ in range(50):
            gid = new_id()
            if not self.path(gid).exists() and not self.meta_path(gid).exists():
                return gid
        raise LibraryError("Не вдалося створити id зображення.")

    def add(self, gid, prompt, size, parent=None, style=None, created=None, kind="original", change=None, root=None, market=None):
        if kind not in KINDS:
            raise LibraryError("Невідомий вид зображення.")
        if change is not None and not (isinstance(change, str) and len(change) <= MAX_CHANGE):
            raise LibraryError("Некоректний опис зміни.")
        meta = {"id": gid, "prompt": prompt, "size": size, "parent": parent, "style": style,
                "created": time.time() if created is None else created, "kind": kind, "change": change,
                "root": root, "market": market}
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

    def find(self, limit=200, kind=None, parent=None, root=None):
        if kind is not None and kind not in KINDS:
            raise LibraryError("Невідомий вид зображення.")
        if parent is not None:
            check(parent)
        if root is not None:
            check(root)
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
            except (TypeError, ValueError, OverflowError):
                continue
            if created != created or created in (float("inf"), float("-inf")):
                continue
            prompt = meta.get("prompt")
            item = {k: meta.get(k) for k in KEYS}
            item["prompt"] = prompt if isinstance(prompt, str) else ""
            if not isinstance(item["size"], str):
                continue
            for k in ("parent", "root", "market", "style"):
                if not isinstance(item[k], str):
                    item[k] = None
            item["created"] = created
            stored = meta.get("kind")
            if stored in KINDS:
                item["kind"] = stored
            else:
                item["kind"] = "modification" if _ID.fullmatch(item["parent"] or "") else "original"
            if item["kind"] == "original":
                item["root"] = None
            elif not _ID.fullmatch(item["root"] or ""):
                item["root"] = item["parent"]  # older records had a single level
            change = meta.get("change")
            item["change"] = change if isinstance(change, str) and len(change) <= MAX_CHANGE else None
            if (kind is not None and item["kind"] != kind) or (parent is not None and item["parent"] != parent) \
                    or (root is not None and item["root"] != root):
                continue
            items.append(item)
        items.sort(key=lambda m: (m["created"], m["id"]), reverse=True)
        return items[:limit]

    def delete(self, ids):
        ids = list(ids)
        for gid in ids:
            check(gid)
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_DELETE:
            raise LibraryError(f"За раз можна видалити не більше {MAX_DELETE} зображень.")
        deleted = 0
        for gid in ids:
            png = self.path(gid)
            existed = png.exists()
            png.unlink(missing_ok=True)
            self.meta_path(gid).unlink(missing_ok=True)
            deleted += 1 if existed else 0
        return deleted

    def zip_bytes(self, ids):
        ids = list(ids)
        for gid in ids:
            check(gid)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise LibraryError("Оберіть зображення для завантаження.")
        if len(ids) > MAX_ZIP:
            raise LibraryError(f"За раз можна завантажити не більше {MAX_ZIP} зображень.")
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
