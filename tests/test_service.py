import json
import unittest

from engine.library import LibraryError
from engine.service import Engine
from tests.helpers import SAMPLE_BLOCKS, FakeLLM, make_root, segments_answer

PAIN = {"pain": "Lost socks", "quotes": ["q"]}
CONCEPT = {"angle": "Mystery", "hook": "Hook"}


def variants_answer(n=5):
    return {"variants": [{"hook": "H", "first_line": "L", "onscreen": "O"} for _ in range(n)]}


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


if __name__ == "__main__":
    unittest.main()
