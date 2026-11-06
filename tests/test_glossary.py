import json
import tempfile
import unittest
from pathlib import Path

from engine import glossary as gl
from tests.helpers import GLOSSARY


class GlossaryTests(unittest.TestCase):
    def test_load_fills_defaults(self):
        path = Path(tempfile.mkdtemp()) / "g.json"
        path.write_text(json.dumps({"protected": ["X"]}), encoding="utf-8")
        g = gl.load(path)
        self.assertEqual(g, {"protected": ["X"], "forbidden": [], "notes": ""})

    def test_to_prompt_mentions_all_parts(self):
        text = gl.to_prompt(GLOSSARY)
        self.assertIn("SockLock", text)
        self.assertIn("guaranteed", text)
        self.assertIn("magnetic socks", text)

    def test_find_forbidden_is_case_insensitive(self):
        self.assertEqual(gl.find_forbidden("It is GUARANTEED to work", GLOSSARY), ["guaranteed"])
        self.assertEqual(gl.find_forbidden("fine text", GLOSSARY), [])

    def test_missing_protected(self):
        self.assertEqual(gl.missing_protected("Buy SockLock", "Kaufe Socken", GLOSSARY), ["SockLock"])
        self.assertEqual(gl.missing_protected("Buy SockLock", "Kaufe sockLOCK", GLOSSARY), [])
        self.assertEqual(gl.missing_protected("No brand here", "Kein Name", GLOSSARY), [])


if __name__ == "__main__":
    unittest.main()
