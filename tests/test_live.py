import os
import tempfile
import unittest
from pathlib import Path

from engine import envfile, llm, openai_api, visuals
from engine.library import parse_script
from engine.service import Engine
from tests.helpers import ROOT, make_root


@unittest.skipUnless(os.environ.get("RUN_OPENAI") == "1",
                     "RUN_OPENAI=1 викликає справжній OpenAI API (платно, з вашого ключа)")
class LiveOpenAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        envfile.load_dotenv(ROOT / ".env", allowed=envfile.ENGINE_VARS)
        if not (os.environ.get("OPENAI_API_KEY") or "").strip():
            raise unittest.SkipTest("Немає OPENAI_API_KEY (.env або середовище)")

    def engine_with_original(self):
        engine = Engine(make_root(), llm.get_backend("openai"), images=visuals.get_provider("openai"))
        script = (ROOT / "samples" / "winner_script.txt").read_text(encoding="utf-8")
        engine.library.add("Socks", "lost socks", parse_script(script), shot_list=["A pair of striped socks on a wooden floor"],
                           status="winner")
        return engine

    def test_models_are_listed(self):
        self.assertTrue(openai_api.list_models())

    def test_text_ping(self):
        if not os.environ.get("OPENAI_TEXT_MODEL"):
            self.skipTest("Немає OPENAI_TEXT_MODEL")
        answer = llm.get_backend("openai").run("### TASK: ping\nReply with exactly one word: ok")
        self.assertIn("ok", answer.lower())

    def test_variants_roundtrip(self):
        if not os.environ.get("OPENAI_TEXT_MODEL"):
            self.skipTest("Немає OPENAI_TEXT_MODEL")
        result = self.engine_with_original().variants("o001", "5 different hooks, curiosity angle", 5)
        self.assertEqual(len(result), 5)

    def test_image_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "shot-1.png"
            prompt = visuals.frame_prompt("A pair of striped socks lying flat on a wooden floor",
                                          {"pain": "Lost socks"}, "clean modern product photography")
            visuals.get_provider("openai").generate(prompt, dest)
            data = dest.read_bytes()
        self.assertTrue(data.startswith(visuals.PNG_MAGIC))
        self.assertGreater(len(data), 10_000)


if __name__ == "__main__":
    unittest.main()
