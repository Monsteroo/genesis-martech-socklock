import json
import re
import threading
from pathlib import Path

from engine import compose as composer
from engine import glossary
from engine import images as imgs
from engine import localize as localizer
from engine import originals as origin
from engine import variants as varier
from engine import visuals
from engine.library import STATUSES, Library, LibraryError


class Engine:
    """Wires the pure modules to the library and to out/ files. CLI and web are thin shells over this."""

    def __init__(self, root, llm, images=None):
        self.root = Path(root)
        self.llm = llm
        self.library = Library(self.root / "library" / "originals.json")
        self.glossary = glossary.load(self.root / "samples" / "glossary.json")
        self.out_dir = self.root / "out"
        self.images = images if images is not None else visuals.NoImages()
        self.image_store = imgs.ImageStore(self.root / "library" / "images")
        self._image_locks = {}
        self._image_locks_guard = threading.Lock()

    def _save(self, name, data):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # tab 1
    def pains(self, text):
        if not isinstance(text, str) or not text.strip():
            raise LibraryError("Вставте текст з коментарями.")
        result = origin.find_pains(self.llm, text)
        self._save("pains.json", result)
        (self.out_dir / "concepts.json").unlink(missing_ok=True)  # concepts of the old pains are stale
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
        if status not in STATUSES:
            raise LibraryError(f"Невідомий статус: {status}. Доступні: {', '.join(STATUSES)}")
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
        for v in result:
            v["key"] = imgs.variant_key(v["hook"])
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
        if not isinstance(pain, str) or not pain.strip():
            raise LibraryError("Вкажіть біль для нових блоків.")
        result = composer.compose(self.llm, self.library, pain, reuse, new_types, self.glossary)
        numbers = [int(m.group(1)) for f in self.out_dir.glob("compose_*.json")
                   if (m := re.fullmatch(r"compose_(\d+)\.json", f.name))] if self.out_dir.exists() else []
        n = max(numbers, default=0) + 1
        self._save(f"compose_{n}.json", result)
        return result

    # photos
    def _key_lock(self, original_id, key):
        with self._image_locks_guard:
            return self._image_locks.setdefault((original_id, key), threading.Lock())

    def _image(self, original_id, key, prompt):
        dest = self.image_store.path(original_id, key)
        with self._key_lock(original_id, key):
            if dest.is_file():
                return {"id": original_id, "key": key, "cached": True}
            self.images.generate(prompt, dest)
        return {"id": original_id, "key": key, "cached": False}

    def shot_image(self, original_id, n):
        record = self.library.get(original_id)
        shots = record["shot_list"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(shots):
            raise LibraryError(f"Немає кадру №{n} (доступно: 1–{len(shots)}).")
        prompt = visuals.frame_prompt(shots[n - 1], record, self.glossary["style"])
        return self._image(original_id, imgs.shot_key(n), prompt)

    def variant_image(self, original_id, variant):
        record = self.library.get(original_id)
        if not isinstance(variant, dict):
            raise LibraryError("Очікується варіація (hook, first_line, onscreen).")
        for field in ("hook", "first_line", "onscreen"):
            if not isinstance(variant.get(field), str) or not variant[field].strip():
                raise LibraryError(f"У варіації немає поля {field}.")
        prompt = visuals.variant_prompt(variant, record, self.glossary["style"])
        return self._image(original_id, imgs.variant_key(variant["hook"]), prompt)

    def image(self, body):
        kind = body.get("kind")
        if kind == "shot":
            return self.shot_image(body["id"], body["index"])
        if kind == "variant":
            return self.variant_image(body["id"], body["variant"])
        raise LibraryError("kind має бути shot або variant.")

    def list_images(self, original_id):
        self.library.get(original_id)
        return {"keys": self.image_store.keys(original_id)}
