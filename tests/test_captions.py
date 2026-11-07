import json
import tempfile
import unittest
from pathlib import Path

from engine import captions
from engine import glossary as gl
from engine.library import LibraryError
from engine.llm import DemoBackend
from tests.helpers import GLOSSARY, FakeLLM

A, B = "cap_0123abcd", "cap_89ef4567"


def answer(**texts):
    return {"segments": [{"id": k, "text": v} for k, v in texts.items()]}


class PlaceholderTests(unittest.TestCase):
    def test_plain_and_nested_arguments(self):
        self.assertEqual(captions.placeholders("Hi {name}, {n, plural, one {# pair} other {# pairs of {kind}}}"),
                         {"name", "n", "kind"})
        self.assertEqual(captions.placeholders("No placeholders"), set())

    def test_plural_branch_words_are_not_arguments(self):
        self.assertEqual(captions.placeholders("{n, plural, one {Paar} other {Paare}}"), {"n"})

    def test_unbalanced_braces_are_invalid(self):
        for bad in ("Hi {name", "Hi name}", "{a, plural, one {x}"):
            with self.assertRaises(ValueError):
                captions.placeholders(bad)


class LocaleTests(unittest.TestCase):
    def test_every_market_has_a_catalog_locale(self):
        from engine import visuals
        got = {k: captions.locale_for_market(k) for k in visuals.MARKETS}
        self.assertEqual(got, {"us": "en-US", "uk": "en-GB", "ca": "en-CA", "au": "en-AU",
                               "de": "de", "fr": "fr", "es": "es"})
        self.assertEqual(set(got.values()), set(captions.LOCALES))

    def test_unknown_market(self):
        with self.assertRaises(LibraryError):
            captions.locale_for_market("xx")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()) / "captions"
        self.c = captions.Captions(self.dir, FakeLLM(), gl.load(Path(__file__).resolve().parent.parent / "samples" / "glossary.json"))

    def test_empty_catalogs_for_every_locale(self):
        data = self.c.get_all()
        self.assertEqual(list(data["catalogs"]), list(captions.LOCALES))
        self.assertEqual(data["catalogs"]["de"], {"messages": {}, "status": {}})

    def test_source_put_is_approved_and_persisted_atomically(self):
        self.c.put("en-US", {A: "Pairs stay together"})
        data = self.c.get_all()["catalogs"]["en-US"]
        self.assertEqual(data["messages"], {A: "Pairs stay together"})
        self.assertEqual(data["status"], {A: "approved"})
        on_disk = json.loads((self.dir / "en-US.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["messages"], {A: "Pairs stay together"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["en-US.json"])   # no temp leftovers

    def test_unicode_survives(self):
        self.c.put("en-US", {A: "Café ñ ü"})
        self.assertIn("Café ñ ü", (self.dir / "en-US.json").read_text(encoding="utf-8"))

    def test_bad_inputs_are_rejected(self):
        bad = [
            ("xx", {A: "t"}), ("../etc", {A: "t"}), (None, {A: "t"}),
            ("en-US", {"cap_xyz": "t"}), ("en-US", {"../x": "t"}), ("en-US", {A: ""}), ("en-US", {A: "  "}),
            ("en-US", {A: 5}), ("en-US", {A: "x" * 501}), ("en-US", ["a"]), ("en-US", {A: "Hi {name"}),
            ("en-US", {f"cap_{i:08x}": "t" for i in range(101)}),
        ]
        for locale, messages in bad:
            with self.subTest(locale=locale, messages=str(messages)[:40]):
                with self.assertRaises(LibraryError):
                    self.c.put(locale, messages)
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_translation_needs_a_source_and_the_same_placeholders(self):
        with self.assertRaises(LibraryError):
            self.c.put("de", {A: "Hallo"})   # no source caption yet
        self.c.put("en-US", {A: "Hello {name}"})
        with self.assertRaises(LibraryError):
            self.c.put("de", {A: "Hallo"})   # placeholder lost
        self.c.put("de", {A: "Hallo {name}"})
        self.assertEqual(self.c.get_all()["catalogs"]["de"]["status"], {A: "machine"})

    def test_edited_text_needs_a_new_approval_unless_status_is_sent(self):
        self.c.put("en-US", {A: "Hello"})
        self.c.put("de", {A: "Hallo"}, {A: "approved"})
        self.assertEqual(self.c.get_all()["catalogs"]["de"]["status"], {A: "approved"})
        self.c.put("de", {A: "Hallo!"})
        self.assertEqual(self.c.get_all()["catalogs"]["de"]["status"], {A: "machine"})
        with self.assertRaises(LibraryError):
            self.c.put("de", {A: "Hallo"}, {A: "great"})
        with self.assertRaises(LibraryError):
            self.c.put("de", {A: "Hallo"}, {B: "approved"})

    def test_delete_removes_the_caption_from_every_locale(self):
        self.c.put("en-US", {A: "Hello", B: "Bye"})
        self.c.put("de", {A: "Hallo", B: "Tschüss"})
        self.c.put("en-US", {}, delete=[A])
        cats = self.c.get_all()["catalogs"]
        self.assertEqual(cats["en-US"]["messages"], {B: "Bye"})
        self.assertEqual(cats["de"]["messages"], {B: "Tschüss"})
        with self.assertRaises(LibraryError):
            self.c.put("en-US", {}, delete=["../x"])

    def test_corrupt_file_reads_as_empty(self):
        self.dir.mkdir(parents=True)
        (self.dir / "de.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.c.get_all()["catalogs"]["de"], {"messages": {}, "status": {}})


class TranslateTests(unittest.TestCase):
    def make(self, *answers):
        self.llm = FakeLLM(*answers)
        self.dir = Path(tempfile.mkdtemp()) / "captions"
        self.c = captions.Captions(self.dir, self.llm, GLOSSARY_OBJ)
        self.c.put("en-US", {A: "SockLock keeps {count} pairs together", B: "No more lonely socks"})
        return self.c

    def test_translates_validates_stores_as_machine(self):
        c = self.make(answer(**{A: "SockLock hält {count} Paare zusammen", B: "Nie wieder einsame Socken"}))
        res = c.translate([A, B], ["de"])
        self.assertEqual(res["results"]["de"][A], {"status": "machine", "text": "SockLock hält {count} Paare zusammen"})
        cat = c.get_all()["catalogs"]["de"]
        self.assertEqual(cat["status"], {A: "machine", B: "machine"})
        self.assertEqual(cat["messages"][B], "Nie wieder einsame Socken")
        self.assertIn("German", self.llm.prompts[0])
        self.assertIn("### TASK: translate_captions_de", self.llm.prompts[0])
        self.assertIn("SockLock", self.llm.prompts[0])
        self.assertIn("magnetic cuff", self.llm.prompts[0])

    def test_lost_placeholder_is_rejected_and_not_stored(self):
        c = self.make(answer(**{A: "SockLock hält Paare zusammen", B: "ok"}))
        res = c.translate([A, B], ["de"])["results"]["de"]
        self.assertEqual(res[A]["status"], "failed")
        self.assertIn("count", res[A]["error"])
        self.assertEqual(res[B]["status"], "machine")
        self.assertEqual(list(c.get_all()["catalogs"]["de"]["messages"]), [B])

    def test_protected_and_forbidden_terms(self):
        c = self.make(answer(**{A: "Sockenschloss hält {count} Paare", B: "Garantiert, guaranteed!"}))
        res = c.translate([A, B], ["fr"])["results"]["fr"]
        self.assertEqual((res[A]["status"], res[B]["status"]), ("failed", "failed"))
        self.assertIn("SockLock", res[A]["error"])
        self.assertEqual(c.get_all()["catalogs"]["fr"]["messages"], {})

    def test_approved_entries_are_never_overwritten(self):
        c = self.make(answer(**{B: "Neu"}))
        c.put("de", {A: "SockLock hält {count} Paare"}, {A: "approved"})
        res = c.translate([A, B], ["de"])["results"]["de"]
        self.assertEqual(res[A]["status"], "approved")
        self.assertEqual(res[B]["status"], "machine")
        self.assertEqual(c.get_all()["catalogs"]["de"]["messages"][A], "SockLock hält {count} Paare")
        self.assertNotIn(A + '"', self.llm.prompts[0].split("ВХІД:")[1])   # not even sent to the model

    def test_cache_skips_the_model_until_the_source_changes(self):
        c = self.make(answer(**{A: "SockLock hält {count} Paare", B: "Neu"}), answer(**{B: "Neuer"}))
        c.translate([A, B], ["de"])
        res = c.translate([A, B], ["de"])["results"]["de"]
        self.assertEqual((res[A]["status"], res[B]["status"]), ("cached", "cached"))
        self.assertEqual(len(self.llm.prompts), 1)
        c.put("en-US", {B: "Changed source"})
        res = c.translate([A, B], ["de"])["results"]["de"]
        self.assertEqual((res[A]["status"], res[B]["status"], res[B]["text"]), ("cached", "machine", "Neuer"))
        self.assertEqual(len(self.llm.prompts), 2)

    def test_one_call_per_locale_and_a_bad_locale_does_not_stop_the_others(self):
        c = self.make("not json", "still not json", answer(**{A: "SockLock mantiene {count} pares", B: "Adiós"}))
        res = c.translate([A, B], ["de", "es"])["results"]
        self.assertEqual({v["status"] for v in res["de"].values()}, {"failed"})
        self.assertEqual({v["status"] for v in res["es"].values()}, {"machine"})

    def test_input_validation(self):
        c = self.make()
        for ids, locales in [([], ["de"]), ([A], []), (["../x"], ["de"]), (["cap_00000000"], ["de"]),
                             ([A], ["xx"]), ([A], ["en-US"]), ("cap", ["de"]), ([A], "de")]:
            with self.subTest(ids=ids, locales=locales):
                with self.assertRaises(LibraryError):
                    c.translate(ids, locales)
        self.assertEqual(self.llm.prompts, [])

    def test_the_demo_backend_gives_deterministic_tagged_translations(self):
        c = captions.Captions(Path(tempfile.mkdtemp()) / "c", DemoBackend(), GLOSSARY_OBJ)
        c.put("en-US", {A: "Hello {name}", B: "Pairs"})
        res = c.translate([A, B], ["de", "fr"])["results"]
        self.assertEqual(res["de"][A], {"status": "machine", "text": "[demo] Hello {name}"})
        self.assertEqual(res["fr"][B]["text"], "[demo] Pairs")


class PoTests(unittest.TestCase):
    def test_gettext_export(self):
        c = captions.Captions(Path(tempfile.mkdtemp()) / "c", FakeLLM(), GLOSSARY_OBJ)
        c.put("en-US", {A: 'Say "hi"\nnow\\'})
        c.put("de", {A: 'Sag "hallo"\njetzt\\'}, {A: "approved"})
        po = c.po("de")
        self.assertIn('"Language: de\\n"', po)
        self.assertIn('"Content-Type: text/plain; charset=utf-8\\n"', po)
        self.assertIn('#. Source (en-US): Say "hi" now\\', po)
        self.assertIn("#. Status: approved", po)
        self.assertIn('msgid "cap_0123abcd"\nmsgstr "Sag \\"hallo\\"\\njetzt\\\\"', po)
        self.assertIn('msgstr "Say \\"hi\\"', c.po("en-US"))
        for bad in ("xx", None, "../x"):
            with self.assertRaises(LibraryError):
                c.po(bad)

    def test_missing_translation_is_an_empty_msgstr(self):
        c = captions.Captions(Path(tempfile.mkdtemp()) / "c", FakeLLM(), GLOSSARY_OBJ)
        c.put("en-US", {A: "Hello"})
        self.assertIn('msgid "cap_0123abcd"\nmsgstr ""', c.po("fr"))


GLOSSARY_OBJ = {"protected": ["SockLock"], "forbidden": ["guaranteed", "cure"], "notes": "magnetic socks", "style": "x"}

if __name__ == "__main__":
    unittest.main()
