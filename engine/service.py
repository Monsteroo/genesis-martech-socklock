import json
from pathlib import Path

from engine import compose as composer
from engine import glossary
from engine import localize as localizer
from engine import originals as origin
from engine import variants as varier
from engine.library import Library, LibraryError


class Engine:
    """Wires the pure modules to the library and to out/ files. CLI and web are thin shells over this."""

    def __init__(self, root, llm):
        self.root = Path(root)
        self.llm = llm
        self.library = Library(self.root / "library" / "originals.json")
        self.glossary = glossary.load(self.root / "samples" / "glossary.json")
        self.out_dir = self.root / "out"

    def _save(self, name, data):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # tab 1
    def pains(self, text):
        if not isinstance(text, str) or not text.strip():
            raise LibraryError("Вставте текст з коментарями.")
        result = origin.find_pains(self.llm, text)
        self._save("pains.json", result)
        return result

    def concepts(self, pain, count=5):
        result = {"concepts": origin.make_concepts(self.llm, pain, count)}
        self._save("concepts.json", {"pain": pain, **result})
        return result

    def _load_saved(self, name, hint):
        path = self.out_dir / name
        if not path.exists():
            raise LibraryError(hint)
        return json.loads(path.read_text(encoding="utf-8"))

    def saved_pains(self):
        return self._load_saved("pains.json", "Спершу виконайте `originals pains FILE`.")

    def saved_concepts(self):
        return self._load_saved("concepts.json", "Спершу виконайте `originals concepts --pain N`.")

    def build_original(self, pain, concept, status="draft"):
        script = origin.write_script(self.llm, pain, concept, self.glossary)
        return self.library.add(
            title=script["title"], pain=pain["pain"], blocks=script["blocks"],
            shot_list=script["shot_list"], source=concept["angle"], status=status,
        )

    # tab 2
    def variants(self, original_id, instruction, count=5):
        if not isinstance(count, int) or not 1 <= count <= 20:
            raise LibraryError("Кількість варіантів має бути від 1 до 20.")
        if not isinstance(instruction, str) or not instruction.strip():
            raise LibraryError("Потрібна інструкція для варіацій.")
        record = self.library.get(original_id)
        result = varier.make_variants(self.llm, record, instruction.strip(), count, self.glossary)
        self._save(f"{original_id}_variants.json", result)
        return result

    def localize(self, original_id, markets):
        markets = list(dict.fromkeys(markets or []))
        if not markets:
            raise LibraryError("Оберіть хоча б один ринок.")
        record = self.library.get(original_id)
        results = {}
        for market in markets:
            results[market] = localizer.localize(self.llm, record, market, self.glossary)
            self._save(f"{original_id}_localize_{market}.json", results[market])
        return results

    def compose(self, pain, reuse, new_types):
        result = composer.compose(self.llm, self.library, pain, reuse, new_types, self.glossary)
        n = len(list(self.out_dir.glob("compose_*.json"))) + 1 if self.out_dir.exists() else 1
        self._save(f"compose_{n}.json", result)
        return result
