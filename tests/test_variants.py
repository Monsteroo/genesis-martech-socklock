import unittest

from engine.llmjson import BadAnswer
from engine.variants import make_variants
from tests.helpers import GLOSSARY, SAMPLE_BLOCKS, FakeLLM

ORIGINAL = {"blocks": SAMPLE_BLOCKS}


def good(n, hook="Where do lost socks go?"):
    return {"variants": [
        {"hook": hook if i == 0 else f"{hook} {i}", "first_line": f"Line {i}", "onscreen": f"Text {i}"}
        for i in range(n)]}


class VariantsTests(unittest.TestCase):
    def test_returns_exactly_count_and_prompt_has_script(self):
        fake = FakeLLM(good(5))
        result = make_variants(fake, ORIGINAL, "5 hooks, curiosity angle", 5, GLOSSARY)
        self.assertEqual(len(result), 5)
        self.assertTrue(fake.prompts[0].startswith("### TASK: variants"))
        self.assertIn("[HOOK] I found where", fake.prompts[0])
        self.assertIn("curiosity angle", fake.prompts[0])

    def test_wrong_count_is_retried(self):
        fake = FakeLLM(good(3), good(5))
        self.assertEqual(len(make_variants(fake, ORIGINAL, "x", 5, GLOSSARY)), 5)
        self.assertEqual(len(fake.prompts), 2)

    def test_forbidden_word_is_retried(self):
        fake = FakeLLM(good(2, hook="A guaranteed fix"), good(2))
        make_variants(fake, ORIGINAL, "x", 2, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_hook_too_long_is_retried(self):
        long_hook = " ".join(["word"] * 15)
        fake = FakeLLM(good(1, hook=long_hook), good(1))
        make_variants(fake, ORIGINAL, "x", 1, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_duplicate_hooks_are_retried_case_insensitively(self):
        dup = good(2)
        dup["variants"][1]["hook"] = dup["variants"][0]["hook"].upper()
        fake = FakeLLM(dup, good(2))
        make_variants(fake, ORIGINAL, "x", 2, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_long_onscreen_is_retried(self):
        long = good(2)
        long["variants"][0]["onscreen"] = "one two three four five six seven"
        fake = FakeLLM(long, good(2))
        make_variants(fake, ORIGINAL, "x", 2, GLOSSARY)
        self.assertEqual(len(fake.prompts), 2)

    def test_six_word_onscreen_is_accepted(self):
        ok = good(2)
        ok["variants"][0]["onscreen"] = "one two three four five six"
        fake = FakeLLM(ok)
        make_variants(fake, ORIGINAL, "x", 2, GLOSSARY)
        self.assertEqual(len(fake.prompts), 1)

    def test_prompt_demands_distinct_fields_and_short_onscreen(self):
        fake = FakeLLM(good(1))
        make_variants(fake, ORIGINAL, "x", 1, GLOSSARY)
        self.assertIn("hook, first_line і onscreen мають відрізнятися між варіантами; onscreen — не більше 6 слів",
                      fake.prompts[0])

    def test_two_bad_answers_raise(self):
        fake = FakeLLM(good(1), good(1))
        with self.assertRaises(BadAnswer):
            make_variants(fake, ORIGINAL, "x", 5, GLOSSARY)


if __name__ == "__main__":
    unittest.main()
