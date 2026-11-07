"""Caption catalogs for LinguiJS: library/captions/<locale>.json.

Each file is {"messages": {id: ICU string}, "status": {id: "machine"|"approved"}, "source": {id: hash of the en-US
text it was translated from}}. The messages map is exactly what `i18n.load(locale, messages)` takes. en-US is the
source language, edited by the user (always "approved"). Other locales are filled by the AI job (`translate`),
checked here (ICU placeholders, protected and forbidden terms) and never overwrite an `approved` entry."""
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from engine import glossary as gl
from engine import visuals
from engine.library import LibraryError
from engine.llmjson import ask_json

SOURCE = "en-US"
LOCALES = ("en-US", "en-GB", "en-CA", "en-AU", "de", "fr", "es")
STATUSES = ("machine", "approved")
ID = re.compile(r"cap_[0-9a-f]{8}")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_TEXT = 500
MAX_CAPTIONS = 100
EXTRA_PROTECTED = ("SockLock", "magnetic cuff")   # product terms that are never translated
LANGUAGE = {
    "en-US": "American English", "en-GB": "British English (UK spelling)", "en-CA": "Canadian English",
    "en-AU": "Australian English", "de": "German (Germany)", "fr": "French (France)", "es": "Spanish (Spain)",
}
_BLOCK_TYPES = ("plural", "select", "selectordinal")


def new_id():
    return "cap_" + secrets.token_hex(4)


def locale_for_market(market):
    if not (isinstance(market, str) and market in visuals.MARKETS):
        raise LibraryError("Невідомий ринок.")
    locale = visuals.MARKETS[market]["locale"]
    return locale if locale in LOCALES else locale.split("-")[0]


def _matching(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("незбалансовані дужки {}")


def placeholders(text):
    """Names of the ICU arguments in a message, including those nested in plural/select branches."""
    names, i = set(), 0
    while i < len(text):
        ch = text[i]
        if ch == "}":
            raise ValueError("незбалансовані дужки {}")
        if ch != "{":
            i += 1
            continue
        end = _matching(text, i)
        inner = text[i + 1:end]
        head, _, rest = inner.partition(",")
        name = head.strip()
        if NAME.fullmatch(name):
            names.add(name)
        kind = rest.partition(",")[0].strip()
        if kind in _BLOCK_TYPES:
            body, j = rest.partition(",")[2], 0
            while j < len(body):   # selector {branch} selector {branch} ...
                if body[j] == "{":
                    stop = _matching(body, j)
                    names |= placeholders(body[j + 1:stop])
                    j = stop + 1
                else:
                    j += 1
        i = end + 1
    return names


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _check_locale(locale):
    if not (isinstance(locale, str) and locale in LOCALES):
        raise LibraryError(f"Невідома локаль. Доступні: {', '.join(LOCALES)}")
    return locale


def _check_ids(ids):
    if not isinstance(ids, list) or not all(isinstance(i, str) and ID.fullmatch(i) for i in ids):
        raise LibraryError("Некоректні id підписів.")
    return list(dict.fromkeys(ids))


def _check_text(text):
    if not isinstance(text, str) or not text.strip():
        raise LibraryError("Підпис не може бути порожнім.")
    if len(text) > MAX_TEXT:
        raise LibraryError(f"Підпис задовгий (до {MAX_TEXT} символів).")
    try:
        placeholders(text)
    except ValueError as exc:
        raise LibraryError(f"Некоректний ICU-текст: {exc}.") from None
    return text.strip()


def _po(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


class Captions:
    def __init__(self, directory, llm, glossary):
        self.dir = Path(directory)
        self.llm = llm
        self.glossary = glossary
        self._lock = threading.RLock()

    # storage
    def _path(self, locale):
        return self.dir / f"{_check_locale(locale)}.json"

    def _load(self, locale):
        empty = {"messages": {}, "status": {}, "source": {}}
        try:
            data = json.loads(self._path(locale).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict):
            return empty
        out = {}
        for key in empty:
            part = data.get(key)
            out[key] = ({k: v for k, v in part.items() if isinstance(k, str) and ID.fullmatch(k) and isinstance(v, str)}
                        if isinstance(part, dict) else {})
        return out

    def _store(self, locale, data):
        path = self._path(locale)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"locale": locale, **data}, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get_all(self):
        with self._lock:
            catalogs = {}
            for locale in LOCALES:
                data = self._load(locale)
                catalogs[locale] = {"messages": data["messages"], "status": data["status"]}
            return {"source": SOURCE, "locales": list(LOCALES), "catalogs": catalogs}

    def source_text(self, caption_id):
        with self._lock:
            return self._load(SOURCE)["messages"].get(caption_id)

    # edits
    def put(self, locale, messages, status=None, delete=None):
        _check_locale(locale)
        if not isinstance(messages, dict):
            raise LibraryError("Очікується об'єкт messages.")
        if status is not None and not isinstance(status, dict):
            raise LibraryError("Очікується об'єкт status.")
        status = status or {}
        ids = _check_ids(list(messages))
        _check_ids(list(status))
        gone = _check_ids(delete if delete is not None else [])
        if len(ids) > MAX_CAPTIONS:
            raise LibraryError(f"За раз можна зберегти не більше {MAX_CAPTIONS} підписів.")
        texts = {i: _check_text(messages[i]) for i in ids}
        for i, s in status.items():
            if s not in STATUSES:
                raise LibraryError("Невідомий статус підпису.")
            if i not in texts:
                raise LibraryError("Статус вказано для підпису, якого немає в запиті.")
        with self._lock:
            if gone:
                for loc in LOCALES:
                    data = self._load(loc)
                    if any(g in data["messages"] for g in gone):
                        for part in data.values():
                            for g in gone:
                                part.pop(g, None)
                        self._store(loc, data)
            if not texts:
                return self.get_all()["catalogs"][locale]
            data = self._load(locale)
            source = self._load(SOURCE)["messages"]
            if len(set(data["messages"]) | set(texts)) > MAX_CAPTIONS:
                raise LibraryError(f"У каталозі може бути не більше {MAX_CAPTIONS} підписів.")
            for i, text in texts.items():
                if locale != SOURCE:
                    if i not in source:
                        raise LibraryError("Спершу додайте цей підпис у en-US.")
                    if placeholders(text) != placeholders(source[i]):
                        names = ", ".join(sorted(placeholders(source[i]))) or "немає"
                        raise LibraryError(f"Плейсхолдери {{…}} мають збігатися з en-US ({names}).")
                changed = data["messages"].get(i) != text
                data["messages"][i] = text
                if locale == SOURCE:
                    data["status"][i] = "approved"
                else:
                    data["status"][i] = status.get(i) or ("machine" if changed else data["status"].get(i, "machine"))
                    if i in source:
                        data["source"][i] = _hash(source[i])
            self._store(locale, data)
            return {"messages": data["messages"], "status": data["status"]}

    # AI job
    def _prompt(self, locale, items):
        glossary = {**self.glossary, "protected": list(dict.fromkeys([*EXTRA_PROTECTED, *self.glossary["protected"]]))}
        return (
            f"### TASK: translate_captions_{locale}\n"
            f"Переклади короткі рекламні підписи на зображення з en-US на: {LANGUAGE[locale]}. Це адаптація, а не дослівний переклад.\n"
            "Підпис має лишатися коротким. ICU-плейсхолдери у фігурних дужках, наприклад {name} або {count, plural, ...}, "
            "залишай без змін (імена змінних не перекладай).\n"
            + gl.to_prompt(glossary) + "\n"
            'Поверни ЛИШЕ JSON: {"segments":[{"id":"cap_...","text":"..."}]} з усіма id з входу.\n\n'
            "ВХІД:\n" + json.dumps(items, ensure_ascii=False)
        )

    def _validate(self, source, text):
        if not isinstance(text, str) or not text.strip():
            return "порожній переклад"
        text = text.strip()
        if len(text) > MAX_TEXT:
            return "переклад задовгий"
        try:
            got = placeholders(text)
        except ValueError as exc:
            return f"некоректний ICU: {exc}"
        want = placeholders(source)
        if got != want:
            lost = sorted(want - got)
            extra = sorted(got - want)
            return "плейсхолдери не збігаються" + (f"; втрачено: {', '.join(lost)}" if lost else "") \
                + (f"; зайві: {', '.join(extra)}" if extra else "")
        glossary = {**self.glossary, "protected": list(dict.fromkeys([*EXTRA_PROTECTED, *self.glossary["protected"]]))}
        missing = gl.missing_protected(source, text, glossary)
        if missing:
            return "термін не збережено: " + ", ".join(missing)
        bad = gl.find_forbidden(text, glossary)
        if bad:
            return "заборонене слово: " + ", ".join(bad)
        return None

    def translate(self, ids, locales):
        ids = _check_ids(ids)
        if not ids or len(ids) > MAX_CAPTIONS:
            raise LibraryError("Оберіть підписи для перекладу.")
        if not isinstance(locales, list) or not locales:
            raise LibraryError("Оберіть хоча б одну локаль.")
        locales = list(dict.fromkeys(_check_locale(x) for x in locales))
        if SOURCE in locales:
            raise LibraryError(f"{SOURCE} — мова оригіналу, її не перекладають.")
        source = self.get_all()["catalogs"][SOURCE]["messages"]
        if any(i not in source for i in ids):
            raise LibraryError("Підпису немає в каталозі en-US.")
        results = {}
        for locale in locales:
            results[locale] = self._translate_locale(locale, ids, source)
        return {"results": results}

    def _translate_locale(self, locale, ids, source):
        out, todo = {}, []
        with self._lock:
            data = self._load(locale)
        for i in ids:
            if data["status"].get(i) == "approved" and i in data["messages"]:
                out[i] = {"status": "approved", "text": data["messages"][i]}
            elif i in data["messages"] and data["source"].get(i) == _hash(source[i]):
                out[i] = {"status": "cached", "text": data["messages"][i]}
            else:
                todo.append(i)
        if not todo:
            return out
        try:
            answer = ask_json(self.llm, self._prompt(locale, {i: source[i] for i in todo}), self._check_shape(todo))
        except Exception as exc:   # one broken locale must not stop the others
            message = str(exc) or "помилка моделі"
            for i in todo:
                out[i] = {"status": "failed", "error": message}
            return out
        by_id = {s["id"]: s["text"] for s in answer["segments"]}
        good = {}
        for i in todo:
            problem = self._validate(source[i], by_id[i])
            if problem:
                out[i] = {"status": "failed", "error": problem}
            else:
                good[i] = by_id[i].strip()
                out[i] = {"status": "machine", "text": good[i]}
        if good:
            with self._lock:
                data = self._load(locale)
                for i, text in good.items():
                    if data["status"].get(i) == "approved":   # approved meanwhile: a human edit wins
                        out[i] = {"status": "approved", "text": data["messages"][i]}
                        continue
                    data["messages"][i] = text
                    data["status"][i] = "machine"
                    data["source"][i] = _hash(source[i])
                self._store(locale, data)
        return out

    @staticmethod
    def _check_shape(todo):
        def check(data):
            items = data.get("segments") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ValueError("немає segments")
            got = [str(x.get("id")) if isinstance(x, dict) else "None" for x in items]
            if sorted(got) != sorted(todo):
                raise ValueError(f"id не збігаються: очікувалось {todo}, отримано {got}")
        return check

    # export for LinguiJS' own pipeline
    def po(self, locale):
        _check_locale(locale)
        with self._lock:
            src = self._load(SOURCE)["messages"]
            cat = self._load(locale)
        lines = [
            'msgid ""', 'msgstr ""',
            '"Project-Id-Version: creative-engine captions\\n"',
            f'"Language: {locale}\\n"',
            '"MIME-Version: 1.0\\n"',
            '"Content-Type: text/plain; charset=utf-8\\n"',
            '"Content-Transfer-Encoding: 8bit\\n"',
            '"X-Generator: creative-engine\\n"',
            "",
        ]
        for i in sorted(src):
            lines.append("#. Source (" + SOURCE + "): " + " ".join(src[i].split()))
            lines.append("#. Status: " + (cat["status"].get(i, "missing") if i in cat["messages"] else "missing"))
            lines.append("msgid " + _po(i))
            lines.append("msgstr " + _po(cat["messages"].get(i, "")))
            lines.append("")
        return "\n".join(lines)
