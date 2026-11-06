import json
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


def find_forbidden(text, g):
    low = text.casefold()
    return [w for w in g["forbidden"] if w.casefold() in low]


def missing_protected(source, translated, g):
    return [
        w for w in g["protected"]
        if w.casefold() in source.casefold() and w.casefold() not in translated.casefold()
    ]
