import json
import os
import threading
import unittest
from unittest import mock

from engine import images as imgs
from engine import visuals
from engine.library import LibraryError
from engine.llm import LLMError
from engine.service import Engine
from engine.visuals import VisualsUnavailable
from tests.helpers import SAMPLE_BLOCKS, FakeImages, FakeLLM, make_root, segments_answer

PAIN = {"pain": "Lost socks", "quotes": ["q"]}
CONCEPT = {"angle": "Mystery", "hook": "Hook"}


def variants_answer(n=5):
    return {"variants": [{"hook": f"H{i}", "first_line": f"L{i}", "onscreen": f"O{i}"} for i in range(n)]}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()

    def engine(self, *answers):
        return Engine(self.root, FakeLLM(*answers))

    def add_original(self, engine):
        return engine.library.add("Socks", "lost", SAMPLE_BLOCKS)

    def test_build_original_saves_to_library(self):
        script = {"title": "Where socks live", "blocks": SAMPLE_BLOCKS, "shot_list": ["shot"]}
        e = self.engine(script)
        record = e.build_original(PAIN, CONCEPT, "testing")
        self.assertEqual(record["id"], "o001")
        self.assertEqual(record["status"], "testing")
        self.assertEqual(record["pain"], "Lost socks")
        self.assertEqual(record["source"], "Mystery")
        self.assertEqual(e.library.get("o001")["shot_list"], ["shot"])

    def test_variants_are_returned_and_written(self):
        e = self.engine(variants_answer(5))
        self.add_original(e)
        result = e.variants("o001", "5 hooks", 5)
        self.assertEqual(len(result), 5)
        saved = json.loads((self.root / "out" / "o001_variants.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result)

    def test_variants_reject_bad_count_and_empty_instruction(self):
        e = self.engine()
        self.add_original(e)
        for count in (0, 21):
            with self.assertRaises(LibraryError):
                e.variants("o001", "x", count)
        with self.assertRaises(LibraryError):
            e.variants("o001", "  ", 5)

    def test_variants_unknown_original(self):
        with self.assertRaises(LibraryError):
            self.engine().variants("o404", "x", 5)

    def test_localize_writes_a_file_per_market(self):
        e = self.engine(segments_answer("DE "), segments_answer("FR "))
        self.add_original(e)
        result = e.localize("o001", ["de", "fr", "de"])  # duplicate market is processed once
        self.assertEqual(sorted(result), ["de", "fr"])
        self.assertEqual(len(e.llm.prompts), 2)
        self.assertTrue((self.root / "out" / "o001_localize_de.json").exists())
        self.assertTrue((self.root / "out" / "o001_localize_fr.json").exists())

    def test_localize_needs_at_least_one_market(self):
        e = self.engine()
        self.add_original(e)
        with self.assertRaises(LibraryError):
            e.localize("o001", [])

    def test_compose_writes_numbered_file(self):
        new = {"blocks": [{"type": "HOOK", "text": "New hook"}]}
        e = self.engine(new)
        self.add_original(e)
        result = e.compose("pain", {"CTA": "o001"}, ["HOOK"])
        self.assertEqual(result["reuse_share"], 0.5)
        self.assertTrue((self.root / "out" / "compose_1.json").exists())

    def test_pains_and_concepts_are_saved(self):
        pains = {"pains": [{"pain": "Lost socks", "quotes": ["q"]}]}
        concepts = {"concepts": [{"angle": "Mystery", "hook": "Hook"}]}
        e = self.engine(pains, concepts)
        result = e.pains("I lose socks q")
        self.assertEqual(result["pains"], pains["pains"])
        self.assertEqual(e.saved_pains(), result)
        self.assertTrue((self.root / "out" / "pains.json").exists())
        e.concepts(PAIN, 1)
        self.assertEqual(e.saved_concepts(), {"pain": PAIN, "concepts": concepts["concepts"]})
        self.assertTrue((self.root / "out" / "concepts.json").exists())

    def test_compose_number_follows_highest_existing_file(self):
        e = self.engine({"blocks": [{"type": "HOOK", "text": "New hook"}]})
        self.add_original(e)
        e.out_dir.mkdir(parents=True, exist_ok=True)
        (e.out_dir / "compose_2.json").write_text("{}", encoding="utf-8")
        e.compose("pain", {"CTA": "o001"}, ["HOOK"])
        self.assertTrue((e.out_dir / "compose_3.json").exists())
        self.assertEqual((e.out_dir / "compose_2.json").read_text(), "{}")

    def test_compose_rejects_blank_pain_before_model_call(self):
        e = self.engine()
        self.add_original(e)
        for pain in ("", "   ", None):
            with self.assertRaises(LibraryError):
                e.compose(pain, {"CTA": "o001"}, ["HOOK"])
        self.assertEqual(e.llm.prompts, [])

    def test_build_original_rejects_unknown_status_before_model_call(self):
        e = self.engine()
        with self.assertRaises(LibraryError):
            e.build_original(PAIN, CONCEPT, "bogus")
        self.assertEqual(e.llm.prompts, [])

    def test_new_pains_delete_stale_concepts(self):
        pains = {"pains": [{"pain": "Lost socks", "quotes": ["q"]}]}
        concepts = {"concepts": [{"angle": "Mystery", "hook": "Hook"}]}
        e = self.engine(pains, concepts, pains)
        e.pains("I lose socks q")
        e.concepts(PAIN, 1)
        self.assertTrue((e.out_dir / "concepts.json").exists())
        e.pains("I lose socks q")
        self.assertFalse((e.out_dir / "concepts.json").exists())
        with self.assertRaises(LibraryError):
            e.saved_concepts()

    def test_saved_lists_missing(self):
        e = self.engine()
        with self.assertRaises(LibraryError) as c:
            e.saved_pains()
        self.assertIn("Спершу", str(c.exception))
        with self.assertRaises(LibraryError) as c:
            e.saved_concepts()
        self.assertIn("Спершу", str(c.exception))


SHOTS = ["Close-up of a washing drum", "A hand pulling one sock from a drawer"]


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.fake = FakeImages()
        self.engine = Engine(self.root, FakeLLM(), images=self.fake)
        self.engine.library.add("Socks", "lost socks", SAMPLE_BLOCKS, shot_list=SHOTS)

    def test_shot_image_is_generated_stored_and_cached(self):
        first = self.engine.shot_image("o001", 1)
        self.assertEqual(first, {"id": "o001", "key": "shot-1", "cached": False})
        path = self.root / "library" / "images" / "o001" / "shot-1.png"
        self.assertTrue(path.is_file())
        self.assertIn("Close-up of a washing drum", self.fake.prompts[0])
        self.assertIn("lost socks", self.fake.prompts[0])
        again = self.engine.shot_image("o001", 1)
        self.assertTrue(again["cached"])
        self.assertEqual(len(self.fake.prompts), 1)  # no second model call

    def test_prompt_uses_the_glossary_style(self):
        self.engine.shot_image("o001", 2)
        self.assertIn(self.engine.glossary["style"], self.fake.prompts[0])

    def test_shot_out_of_range_never_calls_the_provider(self):
        for n in (0, 3, "x"):
            with self.assertRaises(LibraryError):
                self.engine.shot_image("o001", n)
        self.assertEqual(self.fake.prompts, [])

    def test_variant_image_uses_the_hook_hash_key(self):
        v = {"hook": "Where do lost socks go?", "first_line": "Every wash.", "onscreen": "Lost sock mystery"}
        result = self.engine.variant_image("o001", v)
        self.assertEqual(result["key"], imgs.variant_key("Where do lost socks go?"))
        self.assertTrue((self.root / "library" / "images" / "o001" / f"{result['key']}.png").is_file())
        self.assertIn("Where do lost socks go?", self.fake.prompts[0])

    def test_variant_needs_all_fields(self):
        with self.assertRaises(LibraryError):
            self.engine.variant_image("o001", {"hook": "h", "first_line": "", "onscreen": "o"})
        with self.assertRaises(LibraryError):
            self.engine.variant_image("o001", "not a dict")
        self.assertEqual(self.fake.prompts, [])

    def test_image_dispatch_and_unknown_kind(self):
        self.assertEqual(self.engine.image({"kind": "shot", "id": "o001", "index": 2})["key"], "shot-2")
        with self.assertRaises(LibraryError):
            self.engine.image({"kind": "video", "id": "o001"})

    def test_image_index_is_validated_not_coerced(self):
        for bad in (True, 1.9, "2"):
            with self.assertRaises(LibraryError, msg=repr(bad)) as ctx:
                self.engine.image({"kind": "shot", "id": "o001", "index": bad})
            self.assertIn("Немає кадру", str(ctx.exception))
        self.assertEqual(self.fake.prompts, [])

    def test_unknown_original(self):
        with self.assertRaises(LibraryError):
            self.engine.shot_image("o404", 1)

    def test_list_images(self):
        self.assertEqual(self.engine.list_images("o001"), {"keys": []})
        self.engine.shot_image("o001", 1)
        self.assertEqual(self.engine.list_images("o001"), {"keys": ["shot-1"]})
        with self.assertRaises(LibraryError):
            self.engine.list_images("o404")

    def test_provider_failure_leaves_no_file(self):
        engine = Engine(self.root, FakeLLM(), images=FakeImages(fail=LLMError("boom")))
        with self.assertRaises(LLMError):
            engine.shot_image("o001", 1)
        self.assertEqual(engine.list_images("o001"), {"keys": []})

    def test_default_provider_is_unavailable(self):
        engine = Engine(self.root, FakeLLM())
        with self.assertRaises(VisualsUnavailable):
            engine.shot_image("o001", 1)
        self.assertEqual(engine.list_images("o001"), {"keys": []})

    def test_concurrent_requests_for_one_key_make_one_provider_call(self):
        started, release = threading.Event(), threading.Event()

        class SlowImages(FakeImages):
            def generate(self, prompt, dest):
                started.set()
                release.wait(5)
                return super().generate(prompt, dest)

        slow = SlowImages()
        engine = Engine(self.root, FakeLLM(), images=slow)
        results = []
        first = threading.Thread(target=lambda: results.append(engine.shot_image("o001", 1)))
        first.start()
        self.assertTrue(started.wait(5))
        second = threading.Thread(target=lambda: results.append(engine.shot_image("o001", 1)))
        second.start()
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(len(slow.prompts), 1)
        self.assertEqual(sorted(r["cached"] for r in results), [False, True])

    def test_different_keys_run_in_parallel(self):
        both_inside = threading.Barrier(2, timeout=5)

        class ParallelImages(FakeImages):
            def generate(self, prompt, dest):
                both_inside.wait()  # only passes if two generations are inside at the same time
                return super().generate(prompt, dest)

        engine = Engine(self.root, FakeLLM(), images=ParallelImages())
        errors = []

        def run(n):
            try:
                engine.shot_image("o001", n)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(n,)) for n in (1, 2)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(errors, [])


class VariantKeyTests(unittest.TestCase):
    def test_variants_carry_their_image_key(self):
        root = make_root()
        e = Engine(root, FakeLLM(variants_answer(5)))
        e.library.add("Socks", "lost", SAMPLE_BLOCKS)
        result = e.variants("o001", "5 hooks", 5)
        self.assertTrue(all(v["key"] == imgs.variant_key(v["hook"]) for v in result))
        saved = json.loads((root / "out" / "o001_variants.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result)


class QuickImageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.fake = FakeImages()
        self.engine = Engine(self.root, FakeLLM(), images=self.fake)

    def test_generates_stores_and_returns_the_metadata(self):
        meta = self.engine.quick_image("A pair of socks on a wooden floor", "square", "product")
        self.assertRegex(meta["id"], r"^g[0-9a-f]{8}$")
        self.assertTrue((self.root / "library" / "gallery" / f"{meta['id']}.png").is_file())
        self.assertEqual((meta["size"], meta["style"], meta["parent"]), ("1024x1024", "product", None))
        self.assertEqual(meta["prompt"], "A pair of socks on a wooden floor")
        prompt = self.fake.prompts[0]
        self.assertIn("A pair of socks", prompt)
        self.assertIn(visuals.STYLES["product"], prompt)
        self.assertIn("No text", prompt)
        self.assertEqual(self.fake.sizes, ["1024x1024"])

    def test_defaults_are_vertical_and_no_style(self):
        meta = self.engine.quick_image("Socks")
        self.assertEqual((meta["size"], meta["style"]), ("1024x1536", None))

    def test_validation_never_calls_the_provider(self):
        for kwargs in ({"prompt": ""}, {"prompt": "   "}, {"prompt": "x" * 1001},
                       {"prompt": "ok", "format": "huge"}, {"prompt": "ok", "style": "bogus"}):
            with self.assertRaises(LibraryError, msg=str(kwargs)):
                self.engine.quick_image(**kwargs)
        self.assertEqual(self.fake.prompts, [])

    def test_quick_image_no_longer_accepts_parent_or_variation(self):
        for kwargs in ({"prompt": "ok", "parent": "g0000abcd"}, {"prompt": "ok", "variation": 1}):
            with self.assertRaises(TypeError):
                self.engine.quick_image(**kwargs)

    def test_a_provider_failure_leaves_nothing_behind(self):
        folder = self.root / "library" / "gallery"
        folder.mkdir(parents=True)
        engine = Engine(self.root, FakeLLM(), images=FakeImages(fail=LLMError("boom")))
        with self.assertRaises(LLMError):
            engine.quick_image("Socks")
        self.assertEqual(engine.list_gallery(), {"items": []})
        self.assertTrue(folder.is_dir())
        self.assertEqual(os.listdir(folder), [])

    def test_an_interrupted_provider_that_wrote_the_file_leaves_no_orphan(self):
        class Interrupted(FakeImages):
            def generate(self, prompt, dest, size=None):
                super().generate(prompt, dest, size)
                raise KeyboardInterrupt()

        folder = self.root / "library" / "gallery"
        folder.mkdir(parents=True)
        engine = Engine(self.root, FakeLLM(), images=Interrupted())
        with self.assertRaises(KeyboardInterrupt):
            engine.quick_image("Socks")
        self.assertEqual(os.listdir(folder), [])

    def test_engine_delete_and_zip_reject_unhashable_ids(self):
        for bad in ([[1]], [{}], [None]):
            with self.assertRaises(LibraryError):
                self.engine.delete_gallery(bad)
            with self.assertRaises(LibraryError):
                self.engine.gallery_zip(bad)

    def test_a_failing_metadata_write_removes_the_paid_png(self):
        with mock.patch.object(self.engine.gallery, "add", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.engine.quick_image("Socks")
        self.assertEqual(list((self.root / "library" / "gallery").glob("*")), [])

    def test_non_string_format_and_style_are_library_errors(self):
        for kwargs in ({"prompt": "ok", "format": ["x"]}, {"prompt": "ok", "style": {}},
                       {"prompt": "ok", "format": {}}, {"prompt": "ok", "style": ["a"]}):
            with self.assertRaises(LibraryError, msg=str(kwargs)):
                self.engine.quick_image(**kwargs)
        self.assertEqual(self.fake.prompts, [])

    def test_errors_do_not_echo_raw_input(self):
        with self.assertRaises(LibraryError) as c:
            self.engine.quick_image("ok", format="SECRET-FMT")
        self.assertNotIn("SECRET-FMT", str(c.exception))
        with self.assertRaises(LibraryError) as c:
            self.engine.quick_image("ok", style="SECRET-STY")
        self.assertNotIn("SECRET-STY", str(c.exception))

    def test_colliding_ids_are_never_reused(self):
        first = self.engine.quick_image("Socks")
        with mock.patch("engine.gallery.new_id", side_effect=[first["id"], "g00000002"]):
            second = self.engine.quick_image("Other")
        self.assertEqual(second["id"], "g00000002")

    def test_list_zip_and_delete(self):
        a = self.engine.quick_image("A")
        b = self.engine.quick_image("B")
        ids = [m["id"] for m in self.engine.list_gallery()["items"]]
        self.assertEqual(sorted(ids), sorted([a["id"], b["id"]]))
        self.assertTrue(self.engine.gallery_zip([a["id"], b["id"]]).startswith(b"PK"))
        self.assertEqual(self.engine.delete_gallery([a["id"]]), {"deleted": 1})
        self.assertEqual([m["id"] for m in self.engine.list_gallery()["items"]], [b["id"]])

class ModifyImageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.fake = FakeImages()
        self.engine = Engine(self.root, FakeLLM(), images=self.fake)
        self.original = self.engine.quick_image("Striped socks on a floor", "wide", "minimal")

    def test_originals_have_kind_original(self):
        self.assertEqual(self.original["kind"], "original")
        self.assertIsNone(self.original["parent"])

    def test_a_modification_edits_the_parent_image(self):
        meta = self.engine.modify_image(self.original["id"], "  make it evening light ", 0)
        self.assertEqual((meta["kind"], meta["parent"], meta["change"]), ("modification", self.original["id"], "make it evening light"))
        self.assertEqual((meta["size"], meta["style"]), ("1536x1024", "minimal"))
        prompt, src, size = self.fake.edits[0]
        self.assertIn("make it evening light", prompt)
        self.assertIn("Striped socks on a floor", prompt)
        self.assertEqual(src, str(self.root / "library" / "gallery" / f"{self.original['id']}.png"))
        self.assertEqual(size, "1536x1024")
        self.assertTrue((self.root / "library" / "gallery" / f"{meta['id']}.png").is_file())
        self.assertEqual(len(self.fake.prompts), 1)  # only the original was generated from text

    def test_without_a_change_a_variation_hint_is_used(self):
        meta = self.engine.modify_image(self.original["id"], "", 2)
        self.assertIsNone(meta["change"])
        self.assertIn(visuals.VARIATIONS[2], self.fake.edits[0][0])

    def test_a_modification_can_change_the_format(self):
        meta = self.engine.modify_image(self.original["id"], "evening light", 0, "square")
        self.assertEqual(meta["size"], "1024x1024")
        prompt, _, size = self.fake.edits[0]
        self.assertEqual(size, "1024x1024")
        self.assertIn("Recompose the image for the new format (1024x1024)", prompt)
        self.assertEqual(self.engine.gallery.get(meta["id"])["size"], "1024x1024")

    def test_same_or_absent_format_keeps_the_parent_size(self):
        for fmt in (None, "", "wide"):
            meta = self.engine.modify_image(self.original["id"], "x", 0, fmt)
            self.assertEqual(meta["size"], "1536x1024")
        self.assertTrue(all("Recompose" not in e[0] for e in self.fake.edits))

    def test_an_invalid_format_never_calls_the_provider(self):
        for bad in ("huge", ["square"], 5, True, "../x"):
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.engine.modify_image(self.original["id"], "x", 0, bad)
        self.assertEqual(self.fake.edits, [])

    def test_validation_never_calls_the_provider(self):
        for kwargs in ({"parent": "../x"}, {"parent": "g0000abcd"}, {"parent": self.original["id"], "change": "x" * 301},
                       {"parent": self.original["id"], "change": ["x"]}, {"parent": self.original["id"], "variation": -1},
                       {"parent": self.original["id"], "variation": True}, {"parent": self.original["id"], "variation": "1"},
                       {"parent": self.original["id"], "variation": None}, {"parent": None}):
            with self.assertRaises(LibraryError, msg=str(kwargs)):
                self.engine.modify_image(**kwargs)
        self.assertEqual(self.fake.edits, [])

    def test_corrupt_parent_metadata_never_calls_the_provider(self):
        gid = self.original["id"]
        path = self.root / "library" / "gallery" / f"{gid}.json"
        good = json.loads(path.read_text(encoding="utf-8"))
        for bad in ([1], {**good, "prompt": 5}, {**good, "prompt": "x" * 5000}, {**good, "size": "9x9"},
                    {**good, "style": ["x"]}, {k: v for k, v in good.items() if k != "prompt"}):
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(LibraryError, msg=str(bad)[:60]):
                self.engine.modify_image(gid, "x", 0)
        self.assertEqual(self.fake.edits, [])

    def test_a_missing_parent_png_is_an_error(self):
        (self.root / "library" / "gallery" / f"{self.original['id']}.png").unlink()
        with self.assertRaises(LibraryError):
            self.engine.modify_image(self.original["id"], "x", 0)
        self.assertEqual(self.fake.edits, [])

    def test_a_symlinked_parent_png_is_refused_without_a_provider_call(self):
        folder = self.root / "library" / "gallery"
        png = folder / f"{self.original['id']}.png"
        outside = self.root / "outside.png"
        outside.write_bytes(b"secret")
        png.unlink()
        png.symlink_to(outside)
        with self.assertRaises(LibraryError) as c:
            self.engine.modify_image(self.original["id"], "x", 0)
        self.assertEqual(str(c.exception), "Оригінал не знайдено.")
        self.assertEqual(self.fake.edits, [])

    def test_the_longest_allowed_inputs_stay_within_the_prompt_limit(self):
        long_parent = self.engine.quick_image("p" * 1000, "wide")
        self.engine.modify_image(long_parent["id"], "c" * 300, 0)
        self.assertLessEqual(len(self.fake.edits[0][0]), 4000)

    def test_a_provider_failure_or_interrupt_leaves_no_files(self):
        folder = self.root / "library" / "gallery"
        before = sorted(p.name for p in folder.iterdir())
        for exc in (LLMError("boom"), KeyboardInterrupt()):
            engine = Engine(self.root, FakeLLM(), images=FakeImages(fail=exc))
            with self.assertRaises(type(exc)):
                engine.modify_image(self.original["id"], "x", 0)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), before)

    def test_list_gallery_filters(self):
        m = self.engine.modify_image(self.original["id"], "x", 0)
        self.assertEqual([i["id"] for i in self.engine.list_gallery("original")["items"]], [self.original["id"]])
        self.assertEqual([i["id"] for i in self.engine.list_gallery("modification", self.original["id"])["items"]], [m["id"]])

    def test_deleting_an_original_keeps_its_modifications(self):
        m = self.engine.modify_image(self.original["id"], "x", 0)
        self.engine.delete_gallery([self.original["id"]])
        self.assertEqual([i["id"] for i in self.engine.list_gallery("modification")["items"]], [m["id"]])

    def test_originals_have_no_root_and_children_point_to_the_original(self):
        self.assertIsNone(self.original.get("root"))
        m1 = self.engine.modify_image(self.original["id"], "evening light", 0)
        self.assertEqual(m1["root"], self.original["id"])

    def test_a_modification_of_a_modification_keeps_the_top_root(self):
        m1 = self.engine.modify_image(self.original["id"], "evening light", 0, "square")
        m2 = self.engine.modify_image(m1["id"], "добавь підпис «Hi»", 0)
        m3 = self.engine.modify_image(m2["id"], "x", 0)
        self.assertEqual((m2["parent"], m2["root"]), (m1["id"], self.original["id"]))
        self.assertEqual((m3["parent"], m3["root"]), (m2["id"], self.original["id"]))
        self.assertEqual(m2["size"], m1["size"])  # size inherited from the parent modification
        ids = [i["id"] for i in self.engine.list_gallery(root=self.original["id"])["items"]]
        self.assertEqual(sorted(ids), sorted([m1["id"], m2["id"], m3["id"]]))

    def test_chained_edit_uses_the_parent_image_and_change(self):
        m1 = self.engine.modify_image(self.original["id"], "evening light", 0)
        self.engine.modify_image(m1["id"], "bigger socks", 0)
        prompt, src, _ = self.fake.edits[1]
        self.assertTrue(src.endswith(f"{m1['id']}.png"))
        self.assertIn("already a modified version: evening light", prompt)
        self.assertIn("bigger socks", prompt)

    def test_chained_edit_does_not_resurrect_the_original_caption(self):
        orig = self.engine.quick_image('Socks, підпис «Стара назва»', "wide")
        m1 = self.engine.modify_image(orig["id"], "без підпису", 0)
        self.engine.modify_image(m1["id"], "інше світло", 0)
        self.assertNotIn("Стара назва", self.fake.edits[1][0])

    def test_chained_edit_keeps_the_safety_checks(self):
        m1 = self.engine.modify_image(self.original["id"], "x", 0)
        with self.assertRaises(LibraryError):
            self.engine.modify_image(m1["id"], "c" * 301, 0)
        (self.root / "library" / "gallery" / f"{m1['id']}.png").unlink()
        with self.assertRaises(LibraryError):
            self.engine.modify_image(m1["id"], "x", 0)

    def test_deleting_a_middle_modification_keeps_its_children(self):
        m1 = self.engine.modify_image(self.original["id"], "x", 0)
        m2 = self.engine.modify_image(m1["id"], "y", 0)
        self.engine.delete_gallery([m1["id"]])
        items = self.engine.list_gallery(root=self.original["id"])["items"]
        self.assertEqual([(i["id"], i["root"]) for i in items], [(m2["id"], self.original["id"])])


class LocalizeImageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.fake = FakeImages()
        self.engine = Engine(self.root, FakeLLM(), images=self.fake)
        self.original = self.engine.quick_image('Socks on a floor, підпис «Sale»', "wide")

    def test_localizing_an_original(self):
        meta = self.engine.localize_image(self.original["id"], "de")
        self.assertEqual((meta["kind"], meta["parent"], meta["root"], meta["market"]),
                         ("modification", self.original["id"], self.original["id"], "de"))
        self.assertEqual(meta["change"], "Локалізація: Німеччина")
        prompt, src, size = self.fake.edits[0]
        self.assertTrue(src.endswith(self.original["id"] + ".png"))
        self.assertIn("German (de-DE)", prompt)
        self.assertIn("Sale", prompt)           # the source text to translate
        self.assertNotIn("render only this text exactly", prompt)
        self.assertEqual(size, "1536x1024")

    def test_localizing_a_modification_keeps_the_root_and_the_latest_caption(self):
        m1 = self.engine.modify_image(self.original["id"], "замінити підпис на «New»", 0)
        loc = self.engine.localize_image(m1["id"], "fr", "square")
        self.assertEqual((loc["parent"], loc["root"], loc["size"]), (m1["id"], self.original["id"], "1024x1024"))
        prompt = self.fake.edits[1][0]
        self.assertIn("New", prompt)
        self.assertNotIn("Sale", prompt)
        self.assertIn("already a modified version", prompt)

    def test_a_removed_caption_is_not_resurrected(self):
        m1 = self.engine.modify_image(self.original["id"], "без підпису", 0)
        self.engine.localize_image(m1["id"], "es")
        self.assertNotIn("Sale", self.fake.edits[1][0])

    def test_bad_market_or_parent_is_rejected_without_a_provider_call(self):
        for market in ("xx", "", None, 5, ["us"], "../x"):
            with self.assertRaises(LibraryError):
                self.engine.localize_image(self.original["id"], market)
        for parent in (None, "../x", "g00000000"):
            with self.assertRaises(LibraryError):
                self.engine.localize_image(parent, "us")
        with self.assertRaises(LibraryError):
            self.engine.localize_image(self.original["id"], "us", "huge")
        self.assertEqual(self.fake.edits, [])

    def test_the_market_survives_in_the_gallery_listing(self):
        loc = self.engine.localize_image(self.original["id"], "uk")
        items = self.engine.list_gallery(root=self.original["id"])["items"]
        self.assertEqual([(i["id"], i["market"]) for i in items], [(loc["id"], "uk")])
        self.assertIsNone(self.engine.list_gallery(kind="original")["items"][0]["market"])


def real_png(w=64, h=48):
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class NoTextLocalizeAndOverlayTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.fake = FakeImages()
        self.engine = Engine(self.root, FakeLLM(), images=self.fake)
        self.original = self.engine.quick_image('Socks on a floor, підпис «Sale»', "wide")
        self.engine.captions.put("en-US", {"cap_0123abcd": "Pairs stay together"})

    def test_no_text_localization_asks_for_a_text_free_image(self):
        meta = self.engine.localize_image(self.original["id"], "de", None, True)
        prompt = self.fake.edits[0][0]
        self.assertIn("no text", prompt.lower())
        self.assertNotIn("Sale", prompt)
        self.assertIn("Germany", prompt)
        self.assertEqual((meta["market"], meta["change"]), ("de", "Локалізація: Німеччина"))

    def test_overlay_is_stored_as_a_modification_chain(self):
        base = self.engine.localize_image(self.original["id"], "de", None, True)
        png = real_png()
        meta = self.engine.overlay_image(png, base["id"], "de", "cap_0123abcd")
        self.assertEqual((meta["kind"], meta["parent"], meta["root"], meta["market"], meta["caption"], meta["locale"]),
                         ("modification", base["id"], self.original["id"], "de", "cap_0123abcd", "de"))
        self.assertEqual(meta["change"], "Локалізація: Німеччина")
        self.assertEqual(self.engine.gallery.path(meta["id"]).read_bytes(), png)
        items = self.engine.list_gallery(root=self.original["id"])["items"]
        self.assertEqual({i["id"] for i in items}, {base["id"], meta["id"]})

    def test_overlay_on_an_original_has_that_original_as_root(self):
        meta = self.engine.overlay_image(real_png(), self.original["id"], "uk", "cap_0123abcd")
        self.assertEqual((meta["parent"], meta["root"], meta["locale"]), (self.original["id"], self.original["id"], "en-GB"))

    def test_overlay_rejects_bad_input_and_writes_nothing(self):
        o = self.original["id"]
        bad = [(b"", o, "de", "cap_0123abcd"), (b"not a png" * 10, o, "de", "cap_0123abcd"),
               (real_png()[:20], o, "de", "cap_0123abcd"), (real_png() + b"x" * (12 * 1024 * 1024), o, "de", "cap_0123abcd"),
               (real_png(), "../x", "de", "cap_0123abcd"), (real_png(), "g00000000", "de", "cap_0123abcd"),
               (real_png(), o, "xx", "cap_0123abcd"), (real_png(), o, None, "cap_0123abcd"),
               (real_png(), o, "de", "cap_zzzzzzzz"), (real_png(), o, "de", "cap_00000000"), (real_png(), o, "de", None),
               (real_png(100000, 1), o, "de", "cap_0123abcd")]
        for png, parent, market, cap in bad:
            with self.subTest(parent=parent, market=market, cap=cap, n=len(png)):
                with self.assertRaises(LibraryError):
                    self.engine.overlay_image(png, parent, market, cap)
        self.assertEqual(len(self.engine.list_gallery()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
