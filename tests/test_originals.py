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


class ConceptsTests(unittest.TestCase):
    def test_trimmed_to_count(self):
        items = [{"angle": f"a{i}", "hook": f"h{i}"} for i in range(7)]
        result = origin.make_concepts(FakeLLM({"concepts": items}), PAIN, count=5)
        self.assertEqual(len(result), 5)

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


if __name__ == "__main__":
    unittest.main()
