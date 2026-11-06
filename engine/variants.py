from engine import glossary as gl
from engine.library import render_script
from engine.llmjson import ask_json

FIELDS = ("hook", "first_line", "onscreen")
MAX_ONSCREEN_WORDS = 6


def make_variants(llm, original, instruction, count, glossary, max_hook_words=14, language="English"):
    prompt = (
        "### TASK: variants\n"
        f"Ось скрипт-переможець:\n{render_script(original['blocks'])}\n\n"
        f"Завдання: {instruction}\n"
        f"Кількість варіантів: рівно {count}. Мова: {language}. Хук — не більше {max_hook_words} слів.\n"
        f"hook, first_line і onscreen мають відрізнятися між варіантами; onscreen — не більше {MAX_ONSCREEN_WORDS} слів.\n"
        + gl.to_prompt(glossary) + "\n"
        'Поверни ЛИШЕ JSON: {"variants":[{"hook":"...","first_line":"...","onscreen":"..."}]}'
    )

    def check(data):
        items = data.get("variants") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != count:
            raise ValueError(f"потрібно рівно {count} варіантів")
        for v in items:
            if not isinstance(v, dict):
                raise ValueError("варіант має бути об'єктом")
            for key in FIELDS:
                if not isinstance(v.get(key), str) or not v[key].strip():
                    raise ValueError(f"порожнє поле {key}")
            if len(v["hook"].split()) > max_hook_words:
                raise ValueError(f"хук довший за {max_hook_words} слів")
            if len(v["onscreen"].split()) > MAX_ONSCREEN_WORDS:
                raise ValueError(f"onscreen довший за {MAX_ONSCREEN_WORDS} слів")
            bad = gl.find_forbidden(" ".join(v[k] for k in FIELDS), glossary)
            if bad:
                raise ValueError(f"заборонені слова: {bad}")
        hooks = [v["hook"].strip().casefold() for v in items]
        if len(set(hooks)) != len(hooks):
            raise ValueError("хуки мають відрізнятися між варіантами")

    return ask_json(llm, prompt, check)["variants"]
