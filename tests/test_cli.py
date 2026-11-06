import contextlib
import io
import json
import unittest

from engine import cli
from tests.helpers import SAMPLE_BLOCKS, FakeLLM, make_root

TEXT = "Every single wash I lose one sock. Pairing socks is the worst chore."


def run(argv, llm=None, root=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, llm=llm, root=root)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.pains_file = self.root / "pains.txt"
        self.pains_file.write_text(TEXT, encoding="utf-8")

    def test_ping(self):
        code, out, _ = run(["ping"], llm=FakeLLM("ok"), root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"answer": "ok"})

    def test_build_list_show_status(self):
        pains = {"pains": [{"pain": "Lost socks", "quotes": ["Every single wash I lose one sock."]}]}
        concepts = {"concepts": [{"angle": "Mystery", "hook": "Hook"}]}
        script = {"title": "T", "blocks": SAMPLE_BLOCKS, "shot_list": ["shot"]}
        fake = FakeLLM(pains, concepts, script)
        code, out, err = run(["originals", "pains", str(self.pains_file)], llm=fake, root=self.root)
        self.assertEqual(code, 0, err)
        shown_pain = json.loads(out)["pains"][0]["pain"]
        code, out, err = run(["originals", "concepts", "--pain", "1"], llm=fake, root=self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.prompts), 2)
        code, out, err = run(["originals", "build", "--concept", "1"], llm=fake, root=self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.prompts), 3)
        record = json.loads(out)
        self.assertEqual(record["id"], "o001")
        self.assertEqual(record["pain"], shown_pain)
        self.assertEqual(record["source"], "Mystery")

        code, out, _ = run(["library", "status", "o001", "winner"], llm=FakeLLM(), root=self.root)
        self.assertEqual(json.loads(out)["status"], "winner")

        code, out, _ = run(["library", "list", "--status", "winner"], llm=FakeLLM(), root=self.root)
        self.assertEqual([r["id"] for r in json.loads(out)], ["o001"])

        code, out, _ = run(["library", "show", "o001"], llm=FakeLLM(), root=self.root)
        self.assertIn("[HOOK] I found where", out)
        self.assertIn("Shot list:", out)

    def test_pain_number_out_of_range_is_a_clean_error(self):
        pains = {"pains": [{"pain": "Lost socks", "quotes": ["Every single wash I lose one sock."]}]}
        fake = FakeLLM(pains)
        run(["originals", "pains", str(self.pains_file)], llm=fake, root=self.root)
        code, out, err = run(["originals", "concepts", "--pain", "5"], llm=fake, root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_concept_number_out_of_range_is_a_clean_error(self):
        pains = {"pains": [{"pain": "Lost socks", "quotes": ["q"]}]}
        concepts = {"concepts": [{"angle": "Mystery", "hook": "Hook"}]}
        fake = FakeLLM(pains, concepts)
        run(["originals", "pains", str(self.pains_file)], llm=fake, root=self.root)
        run(["originals", "concepts", "--pain", "1"], llm=fake, root=self.root)
        code, _, err = run(["originals", "build", "--concept", "3"], llm=fake, root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_concepts_before_pains_needs_pains_first(self):
        code, _, err = run(["originals", "concepts", "--pain", "1"], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Спершу", err)

    def test_build_before_concepts_needs_concepts_first(self):
        code, _, err = run(["originals", "build", "--concept", "1"], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Спершу", err)

    def test_library_error_returns_1(self):
        code, _, err = run(["library", "show", "o404"], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("не знайдено", err)

    def test_compose_reuse_syntax_error(self):
        code, _, err = run(["modify", "compose", "--pain", "p", "--reuse", "DEMO", "--new", "HOOK"],
                           llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("TYPE=ID", err)

    def test_visuals_reports_unavailable_without_a_backend(self):
        code, out, _ = run(["visuals"], root=self.root)
        self.assertEqual(code, 0)
        self.assertIn("недоступно", out)


if __name__ == "__main__":
    unittest.main()
