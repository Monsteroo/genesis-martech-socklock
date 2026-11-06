import json
import re
import threading
from pathlib import Path

from engine import compose as composer
from engine import gallery as galleries
from engine import glossary
from engine import images as imgs
from engine import localize as localizer
from engine import originals as origin
from engine import variants as varier
from engine import visuals
from engine.library import STATUSES, Library, LibraryError


MAX_QUICK_PROMPT = 1000
MAX_CHANGE = galleries.MAX_CHANGE


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
        self.gallery = galleries.Gallery(self.root / "library" / "gallery")
        self._image_locks = {}
        self._image_locks_guard = threading.Lock()

    def _save(self, name, data):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # free-form generator
    def _store(self, make, **meta):
        gid = self.gallery.fresh_id()
        dest = self.gallery.path(gid)
        try:
            make(dest)
            return self.gallery.add(gid, **meta)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

    def quick_image(self, prompt="", format="vertical", style=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise LibraryError("Опишіть, що потрібно згенерувати.")
        base_prompt = prompt.strip()
        if len(base_prompt) > MAX_QUICK_PROMPT:
            raise LibraryError(f"Опис задовгий (до {MAX_QUICK_PROMPT} символів).")
        if not isinstance(format, str) or format not in visuals.SIZES:
            raise LibraryError(f"Невідомий формат. Доступні: {', '.join(visuals.SIZES)}")
        if style in ("", None):
            style = None
        elif not isinstance(style, str) or style not in visuals.STYLES:
            raise LibraryError(f"Невідомий стиль. Доступні: {', '.join(visuals.STYLES)}")
        size = visuals.SIZES[format]
        final = visuals.quick_prompt(base_prompt, style)

        def make(dest):
            self.images.generate(final, dest, size=size)

        return self._store(make, prompt=base_prompt, size=size, style=style)

    def _parent_meta(self, parent):
        meta = self.gallery.get(parent)
        broken = LibraryError("Метадані зображення пошкоджено.")
        if not isinstance(meta, dict):
            raise broken
        base_prompt, size, style = meta.get("prompt"), meta.get("size"), meta.get("style")
        if not (isinstance(base_prompt, str) and base_prompt.strip() and len(base_prompt) <= MAX_QUICK_PROMPT):
            raise broken
        if not (isinstance(size, str) and size in visuals.SIZES.values()):
            raise broken
        if style is not None and not (isinstance(style, str) and style in visuals.STYLES):
            raise broken
        src = self.gallery.path(parent)
        missing = LibraryError("Оригінал не знайдено.")
        try:
            root = self.gallery.root.resolve()
            if src.is_symlink() or not src.is_file() or src.resolve().parent != root:
                raise missing
        except OSError:
            raise missing from None
        return base_prompt, size, style, src

    def modify_image(self, parent=None, change="", variation=0):
        base_prompt, size, style, src = self._parent_meta(parent)
        if change is None:
            change = ""
        if not isinstance(change, str) or len(change) > MAX_CHANGE:
            raise LibraryError(f"Опис зміни задовгий (до {MAX_CHANGE} символів).")
        change = change.strip() or None
        if not isinstance(variation, int) or isinstance(variation, bool) or variation < 0:
            raise LibraryError("Некоректний номер варіації.")
        final = visuals.modify_prompt(base_prompt, change, variation)

        def make(dest):
            self.images.edit(final, src, dest, size=size)

        return self._store(make, prompt=base_prompt, size=size, parent=parent, style=style,
                                kind="modification", change=change)

    def list_gallery(self, kind=None, parent=None):
        return {"items": self.gallery.find(kind=kind, parent=parent)}

    def delete_gallery(self, ids):
        if not isinstance(ids, list):
            raise LibraryError("Очікується список id.")
        return {"deleted": self.gallery.delete(ids)}

    def gallery_zip(self, ids):
        return self.gallery.zip_bytes(ids)

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
