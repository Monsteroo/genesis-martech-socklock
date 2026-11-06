import json
import re
import time
from pathlib import Path

BLOCK_TYPES = ("HOOK", "PAIN", "DEMO", "PROOF", "ONSCREEN", "CTA")
STATUSES = ("draft", "testing", "winner")
_MARK = re.compile(r"^\[(" + "|".join(BLOCK_TYPES) + r")\]\s*(.*)$")


class LibraryError(Exception):
    """Readable, user-facing failure."""


def check_blocks(blocks):
    if not isinstance(blocks, list) or not blocks:
        raise LibraryError("Потрібен непорожній список блоків.")
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            raise LibraryError(f"Невідомий тип блоку: {b!r}")
        if not isinstance(b.get("text"), str) or not b["text"].strip():
            raise LibraryError(f"Порожній текст у блоці {b.get('type')}.")
    return [{"type": b["type"], "text": b["text"].strip()} for b in blocks]


def parse_script(text):
    blocks, current = [], None
    for line in text.splitlines():
        match = _MARK.match(line.strip())
        if match:
            current = {"type": match.group(1), "text": match.group(2).strip()}
            blocks.append(current)
        elif current is not None and line.strip():
            current["text"] = (current["text"] + " " + line.strip()).strip()
    if not blocks:
        raise LibraryError("У скрипті немає міток блоків ([HOOK], [PAIN], …).")
    return blocks


def render_script(blocks):
    return "\n".join(f"[{b['type']}] {b['text']}" for b in blocks)


def _check_status(status):
    if status not in STATUSES:
        raise LibraryError(f"Невідомий статус: {status}. Доступні: {', '.join(STATUSES)}")


class Library:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding="utf-8")).get("originals", [])

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"originals": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, title, pain, blocks, shot_list=None, source="", status="draft"):
        _check_status(status)
        items = self._load()
        next_n = max((int(i["id"][1:]) for i in items), default=0) + 1
        record = {
            "id": f"o{next_n:03d}",
            "title": title,
            "status": status,
            "pain": pain,
            "source": source,
            "blocks": check_blocks(blocks),
            "shot_list": list(shot_list or []),
            "created": time.strftime("%Y-%m-%d"),
        }
        items.append(record)
        self._save(items)
        return record

    def get(self, original_id):
        for item in self._load():
            if item["id"] == original_id:
                return item
        raise LibraryError(f"Оригінал {original_id} не знайдено.")

    def find(self, status=None):
        if status:
            _check_status(status)
        return [i for i in self._load() if not status or i["status"] == status]

    def set_status(self, original_id, status):
        _check_status(status)
        items = self._load()
        for item in items:
            if item["id"] == original_id:
                item["status"] = status
                self._save(items)
                return item
        raise LibraryError(f"Оригінал {original_id} не знайдено.")
