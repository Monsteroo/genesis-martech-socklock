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
                       {"prompt": "ok", "format": "huge"}, {"prompt": "ok", "style": "bogus"},
                       {"prompt": "ok", "parent": "../x"}, {"prompt": "", "parent": "g0000abcd"}):
            with self.assertRaises(LibraryError, msg=str(kwargs)):
                self.engine.quick_image(**kwargs)
        self.assertEqual(self.fake.prompts, [])

    def test_more_like_this_reuses_prompt_size_and_style_with_a_variation_hint(self):
        first = self.engine.quick_image("Socks", "wide", "minimal")
        second = self.engine.quick_image(parent=first["id"], variation=1)
        self.assertEqual(second["parent"], first["id"])
        self.assertEqual((second["prompt"], second["size"], second["style"]), ("Socks", "1536x1024", "minimal"))
        self.assertIn(visuals.VARIATIONS[1], self.fake.prompts[1])
        self.assertIn("Socks", self.fake.prompts[1])
        self.assertEqual(self.fake.sizes, ["1536x1024", "1536x1024"])

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

    def _parent_with(self, text):
        gid = "g0000abcd"
        d = self.root / "library" / "gallery"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{gid}.png").write_bytes(b"x")
        (d / f"{gid}.json").write_text(text, encoding="utf-8")
        return gid

    def test_corrupt_parent_metadata_never_calls_the_provider(self):
        good = {"id": "g0000abcd", "prompt": "p", "size": "1024x1536", "style": None}
        cases = [
            "[1, 2]",
            json.dumps({k: v for k, v in good.items() if k != "prompt"}),
            json.dumps({**good, "prompt": 5}),
            json.dumps({**good, "prompt": "x" * 5000}),
            json.dumps({**good, "size": "99x99"}),
            json.dumps({**good, "style": ["product"]}),
        ]
        for text in cases:
            gid = self._parent_with(text)
            with self.assertRaises(LibraryError, msg=text[:40]):
                self.engine.quick_image(parent=gid, variation=0)
        self.assertEqual(self.fake.prompts, [])

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

    def test_bad_variation_with_a_parent_is_rejected(self):
        first = self.engine.quick_image("Socks")
        for bad in (-1, True, "1", None, 1.5):
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.engine.quick_image(parent=first["id"], variation=bad)
        self.assertEqual(len(self.fake.prompts), 1)

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

if __name__ == "__main__":
    unittest.main()
