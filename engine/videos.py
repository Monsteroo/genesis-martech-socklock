import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

from engine.library import LibraryError

_ID = re.compile(r"v[0-9a-f]{8}")
_GID = re.compile(r"g[0-9a-f]{8}")
MAX_DELETE = 200
MAX_MOTION = 600
KEYS = ("id", "parent", "root", "prompt", "motion", "duration", "aspect", "size", "bytes", "provider", "created", "trashed")


def new_id():
    return "v" + secrets.token_hex(4)


def check(vid):
    if not (isinstance(vid, str) and _ID.fullmatch(vid)):
        raise LibraryError("Некоректний id відео.")


class Videos:
    """library/videos/<id>.mp4 + <id>.json. Ids are validated before any path is built."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, vid):
        check(vid)
        return self.root / f"{vid}.mp4"

    def meta_path(self, vid):
        check(vid)
        return self.root / f"{vid}.json"

    def fresh_id(self):
        for _ in range(50):
            vid = new_id()
            if not self.path(vid).exists() and not self.meta_path(vid).exists():
                return vid
        raise LibraryError("Не вдалося створити id відео.")

    def add(self, vid, parent, root, prompt, motion, duration, aspect, size, provider, bytes=0, created=None):
        if not (isinstance(parent, str) and _GID.fullmatch(parent) and isinstance(root, str) and _GID.fullmatch(root)):
            raise LibraryError("Некоректне зображення-джерело.")
        if not (isinstance(motion, str) and len(motion) <= MAX_MOTION):
            raise LibraryError("Некоректний опис анімації.")
        if not (isinstance(duration, int) and not isinstance(duration, bool) and duration > 0):
            raise LibraryError("Некоректна тривалість.")
        meta = {"id": vid, "parent": parent, "root": root, "prompt": prompt, "motion": motion, "duration": duration,
                "aspect": aspect, "size": size, "bytes": bytes, "provider": provider,
                "created": time.time() if created is None else created}
        self._write_meta(vid, meta)
        return {**meta, "kind": "video"}

    def _write_meta(self, vid, meta):
        path = self.meta_path(vid)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, vid):
        try:
            return json.loads(self.meta_path(vid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LibraryError("Відео не знайдено.") from None

    def _clean(self, meta, vid):
        if not isinstance(meta, dict) or meta.get("id") != vid:
            return None
        try:
            created = float(meta.get("created"))
        except (TypeError, ValueError, OverflowError):
            return None
        if created != created or created in (float("inf"), float("-inf")):
            return None
        item = {k: meta.get(k) for k in KEYS}
        item["created"] = created
        for k in ("prompt", "motion", "aspect", "size", "provider", "trashed"):
            if not isinstance(item[k], str):
                item[k] = None
        if item["prompt"] is None:
            item["prompt"] = ""
        if item["motion"] is None:
            item["motion"] = ""
        for k in ("parent", "root"):
            if not (isinstance(item[k], str) and _GID.fullmatch(item[k])):
                return None
        if not (isinstance(item["duration"], int) and not isinstance(item["duration"], bool)):
            return None
        if not (isinstance(item["bytes"], int) and not isinstance(item["bytes"], bool)):
            item["bytes"] = 0
        item["kind"] = "video"
        return item

    def find(self, limit=200, parent=None, trashed=False):
        if parent is not None and not (isinstance(parent, str) and _GID.fullmatch(parent)):
            raise LibraryError("Некоректний id зображення.")
        if not self.root.is_dir():
            return []
        items = []
        for meta_file in self.root.glob("v*.json"):
            vid = meta_file.stem
            if not _ID.fullmatch(vid) or not self.path(vid).is_file():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            item = self._clean(meta, vid)
            if item is None or (item["trashed"] is not None) != bool(trashed):
                continue
            if parent is not None and item["parent"] != parent:
                continue
            items.append(item)
        items.sort(key=lambda m: (m["created"], m["id"]), reverse=True)
        return items[:limit]

    def set_trashed(self, ids, on):
        if not isinstance(ids, list):
            raise LibraryError("Очікується список id.")
        for vid in ids:
            check(vid)
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_DELETE:
            raise LibraryError(f"За раз можна обробити не більше {MAX_DELETE} відео.")
        done = 0
        for vid in ids:
            if not self.path(vid).is_file():
                continue
            try:
                meta = json.loads(self.meta_path(vid).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("id") != vid:
                continue
            was = isinstance(meta.get("trashed"), str)
            if on and not was:
                meta["trashed"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
                self._write_meta(vid, meta)
            elif not on and "trashed" in meta:
                del meta["trashed"]
                self._write_meta(vid, meta)
            done += 1
        return done

    def delete(self, ids):
        if not isinstance(ids, list):
            raise LibraryError("Очікується список id.")
        for vid in ids:
            check(vid)
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_DELETE:
            raise LibraryError(f"За раз можна видалити не більше {MAX_DELETE} відео.")
        deleted = 0
        for vid in ids:
            mp4 = self.path(vid)
            existed = mp4.exists()
            mp4.unlink(missing_ok=True)
            self.meta_path(vid).unlink(missing_ok=True)
            deleted += 1 if existed else 0
        return deleted
