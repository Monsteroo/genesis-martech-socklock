import tempfile
import unittest
from pathlib import Path

from engine.compose import compose
from engine.library import Library, LibraryError
from tests.helpers import GLOSSARY, SAMPLE_BLOCKS, FakeLLM

NEW = {"blocks": [
    {"type": "HOOK", "text": "Dear washing machine, give back my socks."},
    {"type": "PAIN", "text": "Fifteen minutes every Sunday matching socks."},
]}
REUSE = {"DEMO": "o001", "PROOF": "o001", "CTA": "o001"}


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.lib = Library(Path(tempfile.mkdtemp()) / "originals.json")
        self.lib.add("Socks", "lost", SAMPLE_BLOCKS)

    def test_mixes_reused_and_new_blocks_in_canonical_order(self):
        fake = FakeLLM(NEW)
        result = compose(fake, self.lib, "Matching wastes time", REUSE, ["HOOK", "PAIN"], GLOSSARY)
        self.assertEqual([b["type"] for b in result["blocks"]], ["HOOK", "PAIN", "DEMO", "PROOF", "CTA"])
        self.assertEqual([b["reused"] for b in result["blocks"]], [False, False, True, True, True])
        self.assertEqual(result["blocks"][2]["from"], "o001")
        self.assertEqual(result["blocks"][2]["text"], SAMPLE_BLOCKS[2]["text"])
        self.assertEqual(result["reuse_share"], 0.6)
        self.assertIn(SAMPLE_BLOCKS[2]["text"], fake.prompts[0])  # reused blocks are given as context

    def test_missing_block_in_source_raises(self):
        lib = Library(Path(tempfile.mkdtemp()) / "o.json")
        lib.add("Short", "p", [{"type": "HOOK", "text": "Hi"}, {"type": "CTA", "text": "Buy"}])
        with self.assertRaises(LibraryError):
            compose(FakeLLM(), lib, "p", {"PROOF": "o001"}, ["HOOK"], GLOSSARY)

    def test_overlap_between_reuse_and_new_raises(self):
        with self.assertRaises(LibraryError):
            compose(FakeLLM(), self.lib, "p", {"HOOK": "o001"}, ["HOOK"], GLOSSARY)

    def test_needs_at_least_one_new_block(self):
        with self.assertRaises(LibraryError):
            compose(FakeLLM(), self.lib, "p", REUSE, [], GLOSSARY)

    def test_unknown_type_raises(self):
        with self.assertRaises(LibraryError):
            compose(FakeLLM(), self.lib, "p", {"BOGUS": "o001"}, ["HOOK"], GLOSSARY)

    def test_wrong_types_from_model_are_retried(self):
        wrong = {"blocks": [{"type": "CTA", "text": "Buy"}]}
        fake = FakeLLM(wrong, {"blocks": [NEW["blocks"][0]]})
        result = compose(fake, self.lib, "p", {"DEMO": "o001"}, ["HOOK"], GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)
        self.assertEqual(result["reuse_share"], 0.5)


if __name__ == "__main__":
    unittest.main()
