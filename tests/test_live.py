import os
import unittest

from engine import llm


@unittest.skipUnless(os.environ.get("RUN_CODEX") == "1", "RUN_CODEX=1 викликає справжній Codex (витрачає ліміт підписки)")
class LiveCodex(unittest.TestCase):
    def test_ping(self):
        answer = llm.get_backend("codex").run("### TASK: ping\nReply with exactly one word: ok")
        self.assertIn("ok", answer.lower())


if __name__ == "__main__":
    unittest.main()
