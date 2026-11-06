import io
import json
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock
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

    def test_find_coerces_or_skips_bad_created_and_returns_only_known_keys(self):
        ok = self.put("ok", created=50.0)
        s = self.put("str", created=10.0)
        n = self.put("null", created=10.0)
        w = self.put("word", created=10.0)
        self.g.meta_path(s).write_text(json.dumps({"id": s, "prompt": "p", "size": "1024x1536", "created": "75"}), encoding="utf-8")
        self.g.meta_path(n).write_text(json.dumps({"id": n, "prompt": "p", "created": None}), encoding="utf-8")
        self.g.meta_path(w).write_text(json.dumps({"id": w, "prompt": "p", "created": "abc"}), encoding="utf-8")
        ids = [m["id"] for m in self.g.find()]
        self.assertEqual(ids[:2], [s, ok])
        self.assertNotIn(n, ids)
        self.assertNotIn(w, ids)
        for m in self.g.find():
            self.assertIsInstance(m["created"], float)

    def test_find_returns_only_known_keys(self):
        gid = self.put("p", created=1.0)
        meta = json.loads(self.g.meta_path(gid).read_text(encoding="utf-8"))
        meta["secret"] = "x"
        self.g.meta_path(gid).write_text(json.dumps(meta), encoding="utf-8")
        self.assertEqual(sorted(self.g.find()[0]), ["created", "id", "parent", "prompt", "size", "style"])

    def test_find_order_is_deterministic_on_equal_created(self):
        ids = [self.put(str(i), created=5.0) for i in range(5)]
        self.assertEqual([m["id"] for m in self.g.find()], sorted(ids, reverse=True))

    def test_fresh_id_skips_existing_ids(self):
        taken = self.put("t")
        with mock.patch("engine.gallery.new_id", side_effect=[taken, "g00000002"]):
            self.assertEqual(self.g.fresh_id(), "g00000002")

    def test_zip_dedupes_ids_without_warnings(self):
        a = self.put("a")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            data = self.g.zip_bytes([a, a, a])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(z.namelist(), [f"{a}.png"])

    def test_zip_skips_a_file_that_vanishes(self):
        a, b = self.put("a"), self.put("b")
        real = zipfile.ZipFile.write

        def flaky(zf, filename, arcname=None, *args, **kw):
            if str(filename).endswith(f"{a}.png"):
                raise FileNotFoundError(filename)
            return real(zf, filename, arcname, *args, **kw)

        with mock.patch.object(zipfile.ZipFile, "write", flaky):
            data = self.g.zip_bytes([a, b])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(z.namelist(), [f"{b}.png"])

    def test_delete_caps_and_dedupes(self):
        with self.assertRaises(LibraryError):
            self.g.delete([gallery.new_id() for _ in range(201)])
        a = self.put("a")
        self.assertEqual(self.g.delete([a, a]), 1)

    def test_find_survives_a_huge_created_number(self):
        ok = self.put("ok", created=1.0)
        bad = self.put("huge")
        self.g.meta_path(bad).write_text('{"id": "%s", "prompt": "p", "created": %s}' % (bad, "1" + "0" * 400), encoding="utf-8")
        self.assertEqual([m["id"] for m in self.g.find()], [ok])

    def test_unhashable_ids_are_library_errors(self):
        for bad in ([[1]], [{}], [None]):
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.g.delete(bad)
            with self.assertRaises(LibraryError, msg=repr(bad)):
                self.g.zip_bytes(bad)

    def test_id_error_does_not_echo_the_id(self):
        with self.assertRaises(LibraryError) as c:
            self.g.path("SECRET/../x")
        self.assertNotIn("SECRET", str(c.exception))
        self.assertEqual(str(c.exception), "Некоректний id зображення.")

    def test_fresh_id_gives_up_after_bounded_retries(self):
        taken = self.put("t")
        with mock.patch("engine.gallery.new_id", return_value=taken) as m:
            with self.assertRaises(LibraryError):
                self.g.fresh_id()
        self.assertLessEqual(m.call_count, 50)

    def test_find_projection_types(self):
        gid = self.put("p", created=1.0)
        bad_size = self.put("q", created=2.0)
        self.g.meta_path(gid).write_text(json.dumps({"id": gid, "prompt": "p", "created": 1, "size": "1024x1024",
                                                     "parent": 5, "style": ["x"]}), encoding="utf-8")
        self.g.meta_path(bad_size).write_text(json.dumps({"id": bad_size, "prompt": "p", "created": 2, "size": 7}), encoding="utf-8")
        items = self.g.find()
        self.assertEqual([m["id"] for m in items], [gid])
        self.assertEqual((items[0]["parent"], items[0]["style"], items[0]["size"]), (None, None, "1024x1024"))


if __name__ == "__main__":
    unittest.main()
