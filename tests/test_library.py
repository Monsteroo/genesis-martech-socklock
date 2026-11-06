import tempfile
import unittest
from pathlib import Path

from engine.library import Library, LibraryError, check_blocks, parse_script, render_script
from tests.helpers import SAMPLE_BLOCKS


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "library" / "originals.json"
        self.lib = Library(self.path)

    def add(self, **kw):
        return self.lib.add(title="Socks", pain="lost socks", blocks=SAMPLE_BLOCKS, **kw)

    def test_add_persists_across_instances(self):
        record = self.add(shot_list=["shot 1"], source="mystery")
        again = Library(self.path).get(record["id"])
        self.assertEqual(again["title"], "Socks")
        self.assertEqual(again["shot_list"], ["shot 1"])
        self.assertEqual(again["status"], "draft")
        self.assertEqual(len(again["blocks"]), 6)

    def test_ids_increase(self):
        self.assertEqual(self.add()["id"], "o001")
        self.assertEqual(self.add()["id"], "o002")

    def test_rejects_unknown_block_type(self):
        with self.assertRaises(LibraryError):
            self.lib.add("t", "p", [{"type": "BOGUS", "text": "x"}])

    def test_rejects_empty_text_and_empty_list(self):
        with self.assertRaises(LibraryError):
            self.lib.add("t", "p", [{"type": "HOOK", "text": "  "}])
        with self.assertRaises(LibraryError):
            self.lib.add("t", "p", [])

    def test_set_status_and_filter(self):
        first = self.add()
        self.add()
        self.lib.set_status(first["id"], "winner")
        self.assertEqual([r["id"] for r in self.lib.find("winner")], ["o001"])
        self.assertEqual(len(self.lib.find()), 2)
        self.assertEqual(len(self.lib.find("draft")), 1)

    def test_bad_status_rejected(self):
        first = self.add()
        with self.assertRaises(LibraryError):
            self.lib.set_status(first["id"], "great")
        with self.assertRaises(LibraryError):
            self.lib.find("great")
        with self.assertRaises(LibraryError):
            self.add(status="great")

    def test_unknown_id(self):
        with self.assertRaises(LibraryError):
            self.lib.get("o999")
        with self.assertRaises(LibraryError):
            self.lib.set_status("o999", "winner")


class ScriptTests(unittest.TestCase):
    def test_render_then_parse_roundtrip(self):
        self.assertEqual(parse_script(render_script(SAMPLE_BLOCKS)), SAMPLE_BLOCKS)

    def test_parse_joins_continuation_lines(self):
        blocks = parse_script("[HOOK] Hello\nthere\n\n[CTA] Buy")
        self.assertEqual(blocks, [{"type": "HOOK", "text": "Hello there"}, {"type": "CTA", "text": "Buy"}])

    def test_parse_without_markers_raises(self):
        with self.assertRaises(LibraryError):
            parse_script("just text")

    def test_check_blocks_strips_text(self):
        self.assertEqual(check_blocks([{"type": "HOOK", "text": " hi "}]), [{"type": "HOOK", "text": "hi"}])


if __name__ == "__main__":
    unittest.main()
