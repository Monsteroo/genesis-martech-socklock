import json

from engine import glossary as gl
from engine.library import LibraryError
from engine.llmjson import ask_json

MARKETS = {
    "de": "німецька (Німеччина)",
    "fr": "французька (Франція)",
    "es": "іспанська (Іспанія)",
    "it": "італійська (Італія)",
    "pl": "польська (Польща)",
}
MAX_GROWTH = 1.2  # on-screen text may be at most 120% of the source length


def to_segments(original):
    return [{"id": f"s{i}", "type": b["type"], "text": b["text"]} for i, b in enumerate(original["blocks"], 1)]


def localize(llm, original, market, glossary):
    """Adapt every block for a market in ONE request, keyed by stable segment ids (the LinguiJS idea)."""
    if market not in MARKETS:
        raise LibraryError(f"Невідомий ринок: {market}. Доступні: {', '.join(MARKETS)}")
    segs = to_segments(original)
    ids = [s["id"] for s in segs]
    payload = json.dumps({s["id"]: s["text"] for s in segs}, ensure_ascii=False)
    prompt = (
        f"### TASK: localize_{market}\n"
        f"Адаптуй рекламні тексти під ринок: {MARKETS[market]}. Це адаптація для аудиторії, а не дослівний переклад.\n"
        "Текст на екрані (ONSCREEN) має лишатися коротким.\n"
        + gl.to_prompt(glossary) + "\n"
        'Поверни ЛИШЕ JSON: {"segments":[{"id":"s1","text":"..."}]} з усіма id з входу.\n\n'
        "ВХІД:\n" + payload
    )

    def check(data):
        items = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError("немає segments")
        got = [str(i.get("id")) if isinstance(i, dict) else "None" for i in items]
        if sorted(got) != sorted(ids):
            raise ValueError(f"id не збігаються: очікувалось {ids}, отримано {got}")
        for i in items:
            if not isinstance(i.get("text"), str) or not i["text"].strip():
                raise ValueError(f"порожній текст у {i['id']}")

    data = ask_json(llm, prompt, check)
    by_id = {i["id"]: i["text"].strip() for i in data["segments"]}
    out = []
    for s in segs:
        text = by_id[s["id"]]
        flags = []
        if s["type"] == "ONSCREEN" and len(text) > len(s["text"]) * MAX_GROWTH:
            flags.append("текст на екрані задовгий (>120% оригіналу)")
        for term in gl.missing_protected(s["text"], text, glossary):
            flags.append(f"термін не збережено: {term}")
        for word in gl.find_forbidden(text, glossary):
            flags.append(f"заборонене слово: {word}")
        out.append({"id": s["id"], "type": s["type"], "source": s["text"], "text": text, "flags": flags})
    return {"market": market, "needs_review": any(x["flags"] for x in out), "segments": out}
