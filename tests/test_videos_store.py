import tempfile
import unittest
from pathlib import Path

from engine.library import LibraryError
from engine.videos import Videos


def make(store, **kw):
    vid = store.fresh_id()
    store.path(vid).parent.mkdir(parents=True, exist_ok=True)
    store.path(vid).write_bytes(b"\x00\x00\x00\x18ftypmp42")
    meta = dict(prompt="p", motion="m", duration=5, aspect="9:16", size="1024x1536", provider="demo", parent="g12345678",
                root="g12345678", bytes=12)
    meta.update(kw)
    return store.add(vid, **meta)


class VideosTests(unittest.TestCase):
    def setUp(self):
        self.store = Videos(Path(tempfile.mkdtemp()) / "videos")

    def test_ids_are_validated(self):
        for bad in ("../x", "g12345678", "v1234", "v1234567g", None, 5):
            with self.assertRaises(LibraryError):
                self.store.path(bad)
            with self.assertRaises(LibraryError):
                self.store.meta_path(bad)

    def test_fresh_id_shape(self):
        self.assertRegex(self.store.fresh_id(), r"v[0-9a-f]{8}")

    def test_add_get_find(self):
        meta = make(self.store)
        self.assertEqual(self.store.get(meta["id"])["motion"], "m")
        found = self.store.find()
        self.assertEqual([m["id"] for m in found], [meta["id"]])
        self.assertEqual(found[0]["duration"], 5)
        self.assertEqual(found[0]["kind"], "video")

    def test_find_filters_by_parent_and_trashed_and_orders_newest_first(self):
        a = make(self.store, created=1.0)
        b = make(self.store, created=2.0, parent="g00000000", root="g00000000")
        self.assertEqual([m["id"] for m in self.store.find()], [b["id"], a["id"]])
        self.assertEqual([m["id"] for m in self.store.find(parent="g00000000")], [b["id"]])
        self.store.set_trashed([a["id"]], True)
        self.assertEqual([m["id"] for m in self.store.find()], [b["id"]])
        trashed = self.store.find(trashed=True)
        self.assertEqual([m["id"] for m in trashed], [a["id"]])
        self.assertIsInstance(trashed[0]["trashed"], str)
        with self.assertRaises(LibraryError):
            self.store.find(parent="nope")

    def test_trash_restore_idempotent_and_unknown_ignored(self):
        a = make(self.store)
        self.assertEqual(self.store.set_trashed([a["id"], "v00000000"], True), 1)
        self.assertEqual(self.store.set_trashed([a["id"]], True), 1)
        self.assertEqual(self.store.set_trashed([a["id"]], False), 1)
        self.assertNotIn("trashed", self.store.get(a["id"]))
        with self.assertRaises(LibraryError):
            self.store.set_trashed("v00000000", True)
        with self.assertRaises(LibraryError):
            self.store.set_trashed(["bad"], True)

    def test_delete_removes_both_files(self):
        a = make(self.store)
        self.assertEqual(self.store.delete([a["id"], a["id"]]), 1)
        self.assertFalse(self.store.path(a["id"]).exists())
        self.assertFalse(self.store.meta_path(a["id"]).exists())
        self.assertEqual(self.store.delete(["v00000000"]), 0)
        with self.assertRaises(LibraryError):
            self.store.delete(["../x"])

    def test_find_skips_broken_records(self):
        a = make(self.store)
        self.store.meta_path("v00000001").write_text("{not json", encoding="utf-8")
        self.store.path("v00000001").write_bytes(b"x")
        self.store.meta_path("v00000002").write_text("{}", encoding="utf-8")
        self.assertEqual([m["id"] for m in self.store.find()], [a["id"]])

    def test_get_missing_is_a_library_error(self):
        with self.assertRaises(LibraryError):
            self.store.get("v00000000")

    def test_add_validates_fields(self):
        with self.assertRaises(LibraryError):
            make(self.store, motion="x" * 601)
        with self.assertRaises(LibraryError):
            make(self.store, duration="5")


if __name__ == "__main__":
    unittest.main()
