from engine import glossary as gl
from engine.library import BLOCK_TYPES, LibraryError, check_blocks
from engine.llmjson import ask_json


def compose(llm, library, pain, reuse, new_types, glossary, language="English"):
    """Build a creative from proven library blocks (`reuse`: type -> original id) plus freshly written ones."""
    new_types = list(new_types)
    if not new_types:
        raise LibraryError("Вкажіть, які блоки створити заново (--new).")
    if len(set(new_types)) != len(new_types):
        raise LibraryError("Типи нових блоків не мають повторюватися.")
    for t in list(reuse) + new_types:
        if t not in BLOCK_TYPES:
            raise LibraryError(f"Невідомий тип блоку: {t}")
    overlap = set(reuse) & set(new_types)
    if overlap:
        raise LibraryError(f"Блок не може бути і взятий з бібліотеки, і новий: {sorted(overlap)}")

    merged = {}
    for btype, oid in reuse.items():
        match = [b for b in library.get(oid)["blocks"] if b["type"] == btype]
        if not match:
            raise LibraryError(f"В оригіналі {oid} немає блоку {btype}.")
        merged[btype] = {"type": btype, "text": match[0]["text"], "reused": True, "from": oid}

    context = "\n".join(f"[{b['type']}] {b['text']}" for b in merged.values()) or "(немає)"
    prompt = (
        "### TASK: compose\n"
        f"Збираємо новий креатив. Біль аудиторії: {pain}\nМова: {language}.\n"
        f"Готові перевірені блоки (не змінюй, лише узгоджуйся з ними):\n{context}\n"
        f"Напиши рівно по одному новому блоку для типів: {', '.join(new_types)}.\n"
        + gl.to_prompt(glossary) + "\n"
        'Поверни ЛИШЕ JSON: {"blocks":[{"type":"HOOK","text":"..."}]}'
    )

    def check(data):
        blocks = check_blocks(data.get("blocks")) if isinstance(data, dict) else None
        if blocks is None or sorted(b["type"] for b in blocks) != sorted(new_types):
            raise ValueError(f"потрібні блоки рівно таких типів: {new_types}")
        for b in blocks:
            bad = gl.find_forbidden(b["text"], glossary)
            if bad:
                raise ValueError(f"заборонені слова: {bad}")

    data = ask_json(llm, prompt, lambda d: _wrap(check, d))
    for b in check_blocks(data["blocks"]):
        merged[b["type"]] = {"type": b["type"], "text": b["text"], "reused": False, "from": None}

    ordered = [merged[t] for t in BLOCK_TYPES if t in merged]
    share = round(len(reuse) / len(ordered), 2)
    return {"pain": pain, "blocks": ordered, "reuse_share": share}


def _wrap(check, data):
    try:
        check(data)
    except LibraryError as exc:
        raise ValueError(str(exc))
