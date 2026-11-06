import json
import unittest

from engine.library import LibraryError
from engine.service import Engine
from tests.helpers import SAMPLE_BLOCKS, FakeLLM, make_root, segments_answer

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


if __name__ == "__main__":
    unittest.main()
