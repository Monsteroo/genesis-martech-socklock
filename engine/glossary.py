import json
import re
from pathlib import Path


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "protected": data.get("protected", []),
        "forbidden": data.get("forbidden", []),
        "notes": data.get("notes", ""),
    }


def to_prompt(g):
    lines = []
    if g["notes"]:
        lines.append(f"Продукт: {g['notes']}")
    if g["protected"]:
        lines.append("Ці слова залишай без змін: " + ", ".join(g["protected"]))
    if g["forbidden"]:
        lines.append("Заборонені слова й твердження: " + ", ".join(g["forbidden"]))
    return "\n".join(lines)


def _term_pattern(term):
    """Whole-word match: a word edge is only enforced where the term itself starts/ends with a word char."""
    before = r"(?<!\w)" if re.match(r"\w", term) else ""
    after = r"(?!\w)" if re.search(r"\w$", term) else ""
    return re.compile(before + re.escape(term) + after, re.IGNORECASE)


def find_forbidden(text, g):
    return [w for w in g["forbidden"] if _term_pattern(w).search(text)]


def missing_protected(source, translated, g):
    return [
        w for w in g["protected"]
        if w.casefold() in source.casefold() and w.casefold() not in translated.casefold()
    ]
