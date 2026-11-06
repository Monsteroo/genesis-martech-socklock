from engine import glossary as gl
from engine.library import BLOCK_TYPES, LibraryError, check_blocks
from engine.llm import LLMError
from engine.llmjson import ask_json


def _norm(s):
    return " ".join(s.split()).casefold()


def find_pains(llm, text, count=4):
    """Pains with verbatim quotes. A quote that is not literally in `text` is discarded."""
    prompt = (
        "### TASK: pains\n"
        "Ти — креативний стратег DTC-бренду. Нижче — коментарі реальних людей.\n"
        f"Знайди до {count} головних болів. Для кожного болю наведи 1–3 ДОСЛІВНІ цитати з тексту (не перефразовуй).\n"
        'Поверни ЛИШЕ JSON: {"pains":[{"pain":"...","quotes":["..."]}]}\n\n'
        "ТЕКСТ:\n" + text
    )

    def check(data):
        pains = data.get("pains") if isinstance(data, dict) else None
        if not isinstance(pains, list) or not pains:
            raise ValueError("немає списку pains")
        for p in pains:
            if not (isinstance(p, dict) and isinstance(p.get("pain"), str) and isinstance(p.get("quotes"), list)):
                raise ValueError("кожен біль потребує pain і quotes")

    data = ask_json(llm, prompt, check)
    haystack = _norm(text)
    kept, dropped = [], 0
    for p in data["pains"]:
        good = [q for q in p["quotes"] if isinstance(q, str) and q.strip() and _norm(q) in haystack]
        dropped += len(p["quotes"]) - len(good)
        if good:
            kept.append({"pain": p["pain"].strip(), "quotes": good})
    if not kept:
        raise LLMError("Жодної цитати не підтверджено текстом, відповідь відхилено.")
    return {"pains": kept, "dropped_quotes": dropped}


def make_concepts(llm, pain, count=5, language="English"):
    prompt = (
        "### TASK: concepts\n"
        f"Біль аудиторії: {pain['pain']}\nЦитати: " + " | ".join(pain["quotes"]) + "\n"
        f"Запропонуй {count} різних креативних кутів для відео-реклами. Хуки пиши мовою: {language}.\n"
        'Поверни ЛИШЕ JSON: {"concepts":[{"angle":"...","hook":"..."}]}'
    )

    def check(data):
        items = data.get("concepts") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("немає списку concepts")
        for c in items:
            ok = (isinstance(c, dict) and isinstance(c.get("angle"), str) and c["angle"].strip()
                  and isinstance(c.get("hook"), str) and c["hook"].strip())
            if not ok:
                raise ValueError("кожен концепт потребує angle і hook")

    return ask_json(llm, prompt, check)["concepts"][:count]


def write_script(llm, pain, concept, glossary, language="English"):
    prompt = (
        "### TASK: script\n"
        "Напиши сценарій відео-реклами на ~20 секунд.\n"
        f"Біль: {pain['pain']}\nКут: {concept['angle']}\nХук-ідея: {concept['hook']}\nМова креативу: {language}.\n"
        + gl.to_prompt(glossary) + "\n"
        f"Типи блоків лише з: {', '.join(BLOCK_TYPES)}; обов'язково HOOK і CTA. Також дай shot list (список кадрів).\n"
        'Поверни ЛИШЕ JSON: {"title":"...","blocks":[{"type":"HOOK","text":"..."}],"shot_list":["..."]}'
    )

    def check(data):
        if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not data["title"].strip():
            raise ValueError("немає title")
        try:
            blocks = check_blocks(data.get("blocks"))
        except LibraryError as exc:
            raise ValueError(str(exc))
        if not {"HOOK", "CTA"} <= {b["type"] for b in blocks}:
            raise ValueError("потрібні блоки HOOK і CTA")
        shots = data.get("shot_list")
        if not isinstance(shots, list) or not all(isinstance(s, str) for s in shots):
            raise ValueError("shot_list має бути списком рядків")
        for b in blocks:
            bad = gl.find_forbidden(b["text"], glossary)
            if bad:
                raise ValueError(f"заборонені слова: {bad}")

    data = ask_json(llm, prompt, check)
    return {"title": data["title"].strip(), "blocks": check_blocks(data["blocks"]), "shot_list": data["shot_list"]}
