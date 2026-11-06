import os
import tempfile
import unittest
from pathlib import Path

from engine import llm, visuals
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

    def test_image_provider_is_available(self):
        ok, reason = visuals.get_provider("codex").available()
        self.assertTrue(ok, reason)

    def test_image_generation_roundtrip(self):
        dest = Path(tempfile.mkdtemp()) / "shot-1.png"
        prompt = visuals.frame_prompt("A pair of striped socks lying flat on a wooden floor",
                                      {"pain": "Lost socks"}, "clean modern product photography")
        visuals.get_provider("codex").generate(prompt, dest)
        data = dest.read_bytes()
        self.assertTrue(data.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])))
        self.assertGreater(len(data), 10_000)


if __name__ == "__main__":
    unittest.main()
