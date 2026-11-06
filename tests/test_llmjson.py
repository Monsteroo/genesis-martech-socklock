import unittest

from engine.llmjson import BadAnswer, ask_json, extract_json
from tests.helpers import FakeLLM


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(extract_json('Ось:\n```json\n{"a": 1}\n```\nГотово'), {"a": 1})

    def test_json_inside_prose(self):
        self.assertEqual(extract_json('Відповідь: {"a": [1, 2]} дякую'), {"a": [1, 2]})

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            extract_json("жодного json")


class AskJsonTests(unittest.TestCase):
    def test_valid_first_time(self):
        fake = FakeLLM('{"ok": true}')
        self.assertEqual(ask_json(fake, "### TASK: t"), {"ok": True})
        self.assertEqual(len(fake.prompts), 1)

    def test_bad_json_is_retried_once(self):
        fake = FakeLLM("не json", '{"ok": true}')
        self.assertEqual(ask_json(fake, "### TASK: t"), {"ok": True})
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("некоректною", fake.prompts[1])

    def test_failed_validation_is_retried_once(self):
        def validate(data):
            if data.get("n") != 2:
                raise ValueError("n має бути 2")

        fake = FakeLLM('{"n": 1}', '{"n": 2}')
        self.assertEqual(ask_json(fake, "### TASK: t", validate), {"n": 2})
        self.assertIn("n має бути 2", fake.prompts[1])

    def test_two_failures_raise_bad_answer(self):
        fake = FakeLLM("a", "b")
        with self.assertRaises(BadAnswer):
            ask_json(fake, "### TASK: t")
        self.assertEqual(len(fake.prompts), 2)


if __name__ == "__main__":
    unittest.main()
