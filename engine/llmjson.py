import json
import re

from engine.llm import LLMError


class BadAnswer(LLMError):
    """The model returned something we cannot use, twice."""


def extract_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("у відповіді немає JSON")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[min(starts): end + 1])


def ask_json(llm, prompt, validate=None):
    """One call plus exactly one retry when the answer is not valid JSON or fails validate()."""
    last = None
    for attempt in range(2):
        text = llm.run(
            prompt
            if attempt == 0
            else prompt + f"\n\nПопередня відповідь була некоректною ({last}). Поверни ЛИШЕ валідний JSON за схемою."
        )
        try:
            data = extract_json(text)
            if validate:
                validate(data)
            return data
        except ValueError as exc:  # JSONDecodeError is a ValueError
            last = str(exc)
    raise BadAnswer(f"Модель двічі повернула некоректну відповідь: {last}")
