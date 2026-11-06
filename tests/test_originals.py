import unittest

from engine import originals as origin
from engine.llm import LLMError
from tests.helpers import GLOSSARY, SAMPLE_BLOCKS, FakeLLM

TEXT = "Every single wash I lose one sock. Pairing socks is the worst chore."
PAIN = {"pain": "Lost socks", "quotes": ["Every single wash I lose one sock."]}
CONCEPT = {"angle": "Mystery", "hook": "I found where all your missing socks live."}


class FindPainsTests(unittest.TestCase):
    def test_keeps_only_quotes_found_in_text(self):
        fake = FakeLLM({"pains": [{"pain": "Lost socks", "quotes": [
            "Every single wash I lose one sock.", "I swear the dryer eats them."]}]})
        result = origin.find_pains(fake, TEXT)
        self.assertEqual(result["pains"][0]["quotes"], ["Every single wash I lose one sock."])
        self.assertEqual(result["dropped_quotes"], 1)
        self.assertTrue(fake.prompts[0].startswith("### TASK: pains"))
        self.assertIn(TEXT, fake.prompts[0])

    def test_pain_without_grounded_quote_is_dropped(self):
        fake = FakeLLM({"pains": [
            {"pain": "Real", "quotes": ["Pairing socks is the worst chore."]},
            {"pain": "Invented", "quotes": ["Totally made up."]}]})
        result = origin.find_pains(fake, TEXT)
        self.assertEqual([p["pain"] for p in result["pains"]], ["Real"])
        self.assertEqual(result["dropped_quotes"], 1)

    def test_all_invented_raises(self):
        fake = FakeLLM({"pains": [{"pain": "Invented", "quotes": ["Made up."]}]})
        with self.assertRaises(LLMError):
            origin.find_pains(fake, TEXT)

    def test_quote_match_ignores_case_and_spacing(self):
        fake = FakeLLM({"pains": [{"pain": "Lost", "quotes": ["every single   WASH i lose one sock."]}]})
        self.assertEqual(len(origin.find_pains(fake, TEXT)["pains"]), 1)


    def test_prompt_states_languages(self):
        fake = FakeLLM({"pains": [{"pain": "Lost socks", "quotes": ["Every single wash I lose one sock."]}]})
        origin.find_pains(fake, TEXT)
        self.assertIn("pain — українською", fake.prompts[0])
        self.assertIn("quotes — дослівно мовою оригіналу", fake.prompts[0])

    def test_curly_apostrophe_in_text_matches_straight_quote(self):
        curly = "I can" + chr(0x2019) + "t find my socks."
        fake = FakeLLM({"pains": [{"pain": "Lost", "quotes": ["I can't find my socks."]}]})
        result = origin.find_pains(fake, curly)
        self.assertEqual(result["pains"][0]["quotes"], ["I can't find my socks."])

    def test_straight_apostrophe_in_text_matches_curly_quote(self):
        fake = FakeLLM({"pains": [{"pain": "Lost", "quotes": ["I can" + chr(0x2019) + "t find my socks."]}]})
        result = origin.find_pains(fake, "I can't find my socks.")
        self.assertEqual(len(result["pains"]), 1)

    def test_word_change_still_dropped_despite_punctuation_normalisation(self):
        curly = "I can" + chr(0x2019) + "t find my socks."
        fake = FakeLLM({"pains": [{"pain": "Lost", "quotes": ["I cannot find my socks."]}]})
        with self.assertRaises(LLMError):
            origin.find_pains(fake, curly)


class ConceptsTests(unittest.TestCase):
    def test_trimmed_to_count(self):
        items = [{"angle": f"a{i}", "hook": f"h{i}"} for i in range(7)]
        result = origin.make_concepts(FakeLLM({"concepts": items}), PAIN, count=5)
        self.assertEqual(len(result), 5)

    def test_prompt_states_languages(self):
        fake = FakeLLM({"concepts": [{"angle": "a", "hook": "h"}]})
        origin.make_concepts(fake, PAIN, language="English")
        self.assertIn("angle — українською", fake.prompts[0])
        self.assertIn("hook — мовою: English", fake.prompts[0])

    def test_concept_without_hook_is_retried(self):
        bad = {"concepts": [{"angle": "a"}]}
        good = {"concepts": [{"angle": "a", "hook": "h"}]}
        fake = FakeLLM(bad, good)
        self.assertEqual(origin.make_concepts(fake, PAIN), [{"angle": "a", "hook": "h"}])
        self.assertEqual(len(fake.prompts), 2)


class ScriptTests(unittest.TestCase):
    GOOD = {"title": "Where socks live", "blocks": SAMPLE_BLOCKS, "shot_list": ["Close-up of a washing drum"]}

    def test_returns_title_blocks_and_shots(self):
        fake = FakeLLM(self.GOOD)
        result = origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(result["title"], "Where socks live")
        self.assertEqual(len(result["blocks"]), 6)
        self.assertEqual(result["shot_list"], ["Close-up of a washing drum"])
        self.assertIn("Mystery", fake.prompts[0])

    def test_forbidden_word_triggers_retry(self):
        tainted = dict(self.GOOD, blocks=[
            {"type": "HOOK", "text": "A guaranteed fix"}, {"type": "CTA", "text": "Buy"}])
        fake = FakeLLM(tainted, self.GOOD)
        result = origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)
        self.assertEqual(len(result["blocks"]), 6)

    def test_missing_cta_triggers_retry(self):
        no_cta = dict(self.GOOD, blocks=[{"type": "HOOK", "text": "Hi"}])
        fake = FakeLLM(no_cta, self.GOOD)
        origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_missing_proof_triggers_retry(self):
        no_proof = dict(self.GOOD, blocks=[b for b in SAMPLE_BLOCKS if b["type"] != "PROOF"])
        fake = FakeLLM(no_proof, self.GOOD)
        result = origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("PROOF", {b["type"] for b in result["blocks"]})

    def test_each_required_type_is_enforced(self):
        for missing in ("HOOK", "PAIN", "DEMO", "PROOF", "CTA"):
            broken = dict(self.GOOD, blocks=[b for b in SAMPLE_BLOCKS if b["type"] != missing])
            fake = FakeLLM(broken, self.GOOD)
            origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
            self.assertEqual(len(fake.prompts), 2, missing)

    def test_onscreen_is_optional(self):
        no_onscreen = dict(self.GOOD, blocks=[b for b in SAMPLE_BLOCKS if b["type"] != "ONSCREEN"])
        fake = FakeLLM(no_onscreen)
        origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(len(fake.prompts), 1)

    def test_long_onscreen_triggers_retry(self):
        blocks = [dict(b) for b in SAMPLE_BLOCKS]
        blocks[4]["text"] = "No more lonely socks in your drawer ever"  # 8 words
        fake = FakeLLM(dict(self.GOOD, blocks=blocks), self.GOOD)
        origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_prompt_states_block_and_onscreen_rules(self):
        fake = FakeLLM(self.GOOD)
        origin.write_script(fake, PAIN, CONCEPT, GLOSSARY)
        self.assertIn("HOOK, PAIN, DEMO, PROOF і CTA", fake.prompts[0])
        self.assertIn("ONSCREEN — коротка фраза, не більше 6 слів", fake.prompts[0])


if __name__ == "__main__":
    unittest.main()
