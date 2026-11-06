import os
import unittest

from engine import llm
from engine.library import parse_script
from engine.service import Engine
from tests.helpers import ROOT, make_root


@unittest.skipUnless(os.environ.get("RUN_CODEX") == "1", "RUN_CODEX=1 викликає справжній Codex (витрачає ліміт підписки)")
class LiveCodex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = llm.get_backend("codex")

    def engine_with_original(self):
        engine = Engine(make_root(), self.backend)
        script = (ROOT / "samples" / "winner_script.txt").read_text(encoding="utf-8")
        engine.library.add("Socks", "lost socks", parse_script(script), status="winner")
        return engine

    def test_ping(self):
        answer = self.backend.run("### TASK: ping\nReply with exactly one word: ok")
        self.assertIn("ok", answer.lower())

    def test_variants_roundtrip(self):
        engine = self.engine_with_original()
        result = engine.variants("o001", "5 different hooks, curiosity angle", 5)
        self.assertEqual(len(result), 5)
        self.assertTrue(all(v["hook"].strip() for v in result))

    def test_localize_german_keeps_all_segments(self):
        engine = self.engine_with_original()
        result = engine.localize("o001", ["de"])["de"]
        self.assertEqual([s["id"] for s in result["segments"]], ["s1", "s2", "s3", "s4", "s5", "s6"])
        self.assertTrue(all(s["text"].strip() for s in result["segments"]))


if __name__ == "__main__":
    unittest.main()
