import unittest

from engine.library import LibraryError
from engine.localize import MARKETS, localize, to_segments
from tests.helpers import GLOSSARY, SAMPLE_BLOCKS, FakeLLM, segments_answer

ORIGINAL = {"blocks": SAMPLE_BLOCKS}


class LocalizeTests(unittest.TestCase):
    def test_segments_have_stable_ids(self):
        segs = to_segments(ORIGINAL)
        self.assertEqual([s["id"] for s in segs], ["s1", "s2", "s3", "s4", "s5", "s6"])
        self.assertEqual(segs[4]["type"], "ONSCREEN")

    def test_clean_translation_has_no_flags(self):
        fake = FakeLLM(segments_answer("DE "))
        result = localize(fake, ORIGINAL, "de", GLOSSARY)
        self.assertEqual(result["market"], "de")
        self.assertFalse(result["needs_review"])
        self.assertEqual(result["segments"][0]["text"], "DE I found where all your missing socks live.")
        self.assertEqual(result["segments"][0]["source"], SAMPLE_BLOCKS[0]["text"])
        self.assertTrue(fake.prompts[0].startswith("### TASK: localize_de"))
        self.assertIn('"s1"', fake.prompts[0])

    def test_long_onscreen_text_is_flagged(self):
        answer = segments_answer()
        answer["segments"][4]["text"] = "Nie wieder einsame Socken"  # 25 chars vs 20 (>120%)
        result = localize(FakeLLM(answer), ORIGINAL, "de", GLOSSARY)
        self.assertTrue(result["needs_review"])
        self.assertIn("задовгий", result["segments"][4]["flags"][0])
        self.assertEqual(result["segments"][0]["flags"], [])

    def test_lost_brand_name_is_flagged(self):
        answer = segments_answer()
        answer["segments"][2]["text"] = "Der Magnet im Bündchen hält das Paar zusammen."
        result = localize(FakeLLM(answer), ORIGINAL, "de", GLOSSARY)
        self.assertIn("термін не збережено: SockLock", result["segments"][2]["flags"])

    def test_forbidden_word_is_flagged(self):
        answer = segments_answer()
        answer["segments"][3]["text"] = "Guaranteed to stay together."
        result = localize(FakeLLM(answer), ORIGINAL, "fr", GLOSSARY)
        self.assertIn("заборонене слово: guaranteed", result["segments"][3]["flags"])

    def test_changed_numbers_are_flagged(self):
        answer = segments_answer()
        answer["segments"][5]["text"] = "Teste 5 Paare f\u00fcr 29 US-Dollar, alle 3 Monate."
        result = localize(FakeLLM(answer), ORIGINAL, "de", GLOSSARY)
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["segments"][5]["flags"], ["числа змінено: 5, 29 \u2192 5, 29, 3"])

    def test_same_numbers_with_currency_symbol_are_not_flagged(self):
        answer = segments_answer()
        answer["segments"][5]["text"] = "Teste 5 Paare f\u00fcr 29 $, geliefert jedes Quartal."
        result = localize(FakeLLM(answer), ORIGINAL, "de", GLOSSARY)
        self.assertEqual(result["segments"][5]["flags"], [])

    def test_prompt_keeps_numbers_and_currency(self):
        fake = FakeLLM(segments_answer())
        localize(fake, ORIGINAL, "de", GLOSSARY)
        self.assertIn("Числа, ціни та символи валют залишай точно як у вхідному тексті "
                      "(не заміняй $ на US-Dollar тощо)", fake.prompts[0])

    def test_missing_id_is_retried(self):
        broken = segments_answer()
        broken["segments"].pop()
        fake = FakeLLM(broken, segments_answer())
        result = localize(fake, ORIGINAL, "fr", GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)
        self.assertEqual(len(result["segments"]), 6)

    def test_unknown_market_raises_before_calling_model(self):
        fake = FakeLLM()
        with self.assertRaises(LibraryError):
            localize(fake, ORIGINAL, "xx", GLOSSARY)
        self.assertEqual(fake.prompts, [])

    def test_markets_list(self):
        self.assertEqual(sorted(MARKETS), ["de", "es", "fr", "it", "pl"])


if __name__ == "__main__":
    unittest.main()
