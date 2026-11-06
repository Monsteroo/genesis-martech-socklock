import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import llm
from engine.openai_api import OpenAIBackend


class DemoBackendTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ping.json").write_text("ok\n", encoding="utf-8")

    def test_reads_answer_by_task_name(self):
        self.assertEqual(llm.DemoBackend(self.dir).run("### TASK: ping\nanything"), "ok")

    def test_requires_task_line(self):
        with self.assertRaises(llm.LLMError):
            llm.DemoBackend(self.dir).run("no task line")

    def test_unknown_task_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.DemoBackend(self.dir).run("### TASK: nope")


class GetBackendTests(unittest.TestCase):
    def test_unknown_backend(self):
        with self.assertRaises(llm.LLMError):
            llm.get_backend("gemini")

    def test_codex_backend_was_removed(self):
        with self.assertRaises(llm.LLMError) as ctx:
            llm.get_backend("codex")
        self.assertIn("OpenAI", str(ctx.exception))

    def test_default_is_openai(self):
        with mock.patch.dict(os.environ, {"ENGINE_BACKEND": ""}):
            self.assertIsInstance(llm.get_backend(), OpenAIBackend)

    def test_default_comes_from_env(self):
        with mock.patch.dict(os.environ, {"ENGINE_BACKEND": "demo"}):
            self.assertIsInstance(llm.get_backend(), llm.DemoBackend)

    def test_backend_options_are_passed_through(self):
        backend = llm.get_backend("openai", model="m1")
        self.assertEqual(backend.model, "m1")


if __name__ == "__main__":
    unittest.main()
