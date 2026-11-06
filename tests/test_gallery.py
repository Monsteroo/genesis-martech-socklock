import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from engine import gallery
from engine.library import LibraryError


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "gallery"
        self.g = gallery.Gallery(self.root)

    def put(self, prompt="p", created=None, parent=None, png=True):
        gid = gallery.new_id()
        self.root.mkdir(parents=True, exist_ok=True)
        if png:
            self.g.path(gid).write_bytes(b"png-" + gid.encode())
        self.g.add(gid, prompt, "1024x1536", parent=parent, style="product", created=created)
        return gid

    def test_new_ids_have_the_right_shape_and_are_unique(self):
        ids = {gallery.new_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        for gid in ids:
            self.assertRegex(gid, r"^g[0-9a-f]{8}$")

    def test_bad_ids_are_rejected(self):
        for bad in ("../x", "g123", "gZZZZZZZZ", "g0123456789", None, "G0123abcd", "g0123abcd\n"):
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.g.path(bad)
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.g.get(bad)

    def test_add_and_get_roundtrip(self):
        gid = self.put("socks on a floor", parent="g00000001")
        meta = self.g.get(gid)
        self.assertEqual(meta["id"], gid)
        self.assertEqual(meta["prompt"], "socks on a floor")
        self.assertEqual(meta["size"], "1024x1536")
        self.assertEqual(meta["parent"], "g00000001")
        self.assertEqual(meta["style"], "product")
        self.assertIsInstance(meta["created"], float)
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_get_unknown_raises(self):
        with self.assertRaises(LibraryError):
            self.g.get("g0000abcd")

    def test_find_is_newest_first_and_skips_missing_pngs_and_corrupt_json(self):
        old = self.put("old", created=100.0)
        new = self.put("new", created=200.0)
        self.put("no png", created=300.0, png=False)
        bad = gallery.new_id()
        self.g.path(bad).write_bytes(b"x")
        self.g.meta_path(bad).write_text("{not json", encoding="utf-8")
        self.assertEqual([m["id"] for m in self.g.find()], [new, old])
        self.assertEqual([m["id"] for m in self.g.find(limit=1)], [new])

    def test_find_on_a_missing_folder_is_empty(self):
        self.assertEqual(gallery.Gallery(self.root / "nope").find(), [])

    def test_delete_removes_files_and_counts(self):
        a, b = self.put("a"), self.put("b")
        self.assertEqual(self.g.delete([a, "g0000abcd"]), 1)
        self.assertFalse(self.g.path(a).exists())
        self.assertFalse(self.g.meta_path(a).exists())
        self.assertTrue(self.g.path(b).exists())

    def test_delete_validates_every_id_before_touching_anything(self):
        a = self.put("a")
        with self.assertRaises(LibraryError):
            self.g.delete([a, "../etc"])
        self.assertTrue(self.g.path(a).exists())

    def test_zip_contains_the_requested_images(self):
        a, b = self.put("a"), self.put("b")
        data = self.g.zip_bytes([a, b, "g0000abcd"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(sorted(z.namelist()), sorted([f"{a}.png", f"{b}.png"]))
            self.assertEqual(z.read(f"{a}.png"), b"png-" + a.encode())

    def test_zip_errors(self):
        with self.assertRaises(LibraryError):
            self.g.zip_bytes(["g0000abcd"])
        with self.assertRaises(LibraryError):
            self.g.zip_bytes([gallery.new_id() for _ in range(51)])
        with self.assertRaises(LibraryError):
            self.g.zip_bytes([])


if __name__ == "__main__":
    unittest.main()
