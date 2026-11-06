import tempfile
import unittest
from pathlib import Path

from engine import images
from engine.library import LibraryError


class KeyTests(unittest.TestCase):
    def test_variant_key_is_stable_and_normalised(self):
        a = images.variant_key("Where do lost socks go?")
        self.assertEqual(a, images.variant_key("  where DO lost socks go? "))
        self.assertRegex(a, r"^variant-[0-9a-f]{8}$")
        self.assertNotEqual(a, images.variant_key("Another hook"))

    def test_shot_key(self):
        self.assertEqual(images.shot_key(3), "shot-3")
        with self.assertRaises(LibraryError):
            images.shot_key(0)


    def test_shot_key_rejects_bool_and_huge_numbers_are_not_valid_keys(self):
        with self.assertRaises(LibraryError):
            images.shot_key(True)
        with self.assertRaises(LibraryError):
            images.check("o001", "shot-1234567")
        images.check("o001", "shot-123456")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = images.ImageStore(self.root)

    def test_path_is_inside_root(self):
        p = self.store.path("o001", "shot-1")
        self.assertEqual(p, self.root / "o001" / "shot-1.png")

    def test_bad_ids_and_keys_rejected(self):
        for oid, key in [("../x", "shot-1"), ("o1", "shot-1"), ("o001", "shot-"), ("o001", "../shot-1"),
                         ("o001", "variant-ZZZZZZZZ"), ("o001", "shot-1.png"), (None, "shot-1")]:
            with self.assertRaises(LibraryError, msg=(oid, key)):
                self.store.path(oid, key)

    def test_unicode_digits_rejected(self):
        arabic = chr(0x661)
        for oid, key in [("o" + arabic * 3, "shot-1"), ("o001", "shot-" + arabic), ("o001", "shot-1234567")]:
            with self.assertRaises(LibraryError, msg=(oid, key)):
                self.store.path(oid, key)

    def test_keys_sorted_naturally(self):
        folder = self.root / "o001"
        folder.mkdir()
        for name in ("shot-10.png", "shot-2.png", "variant-0a1b2c3d.png", "shot-1.png", "variant-00000000.png"):
            (folder / name).write_bytes(b"x")
        self.assertEqual(self.store.keys("o001"),
                         ["shot-1", "shot-2", "shot-10", "variant-00000000", "variant-0a1b2c3d"])

    def test_keys_lists_existing_valid_files_sorted(self):
        folder = self.root / "o001"
        folder.mkdir()
        for name in ("shot-2.png", "shot-1.png", "notes.txt", "junk.png", "variant-0a1b2c3d.png"):
            (folder / name).write_bytes(b"x")
        self.assertEqual(self.store.keys("o001"), ["shot-1", "shot-2", "variant-0a1b2c3d"])
        self.assertTrue(self.store.exists("o001", "shot-1"))
        self.assertFalse(self.store.exists("o001", "shot-3"))

    def test_keys_for_unknown_original_is_empty_and_bad_id_rejected(self):
        self.assertEqual(self.store.keys("o009"), [])
        with self.assertRaises(LibraryError):
            self.store.keys("../etc")


if __name__ == "__main__":
    unittest.main()
