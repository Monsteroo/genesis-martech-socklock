import contextlib
import io
import json
import socket
import unittest
from pathlib import Path

from engine import cli, visuals
from tests.helpers import SAMPLE_BLOCKS, FakeImages, FakeLLM, make_root

TEXT = "Every single wash I lose one sock. Pairing socks is the worst chore."


def run(argv, llm=None, root=None, images=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, llm=llm, root=root, images=images)
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

    def test_corrupt_saved_pains_is_a_clean_error(self):
        out_dir = self.root / "out"
        out_dir.mkdir()
        (out_dir / "pains.json").write_text("{not json", encoding="utf-8")
        code, _, err = run(["originals", "concepts", "--pain", "1"], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_non_utf8_input_file_is_a_clean_error(self):
        bad = self.root / "bad.txt"
        bad.write_bytes(b"\xff\xfe\x00bad")
        code, _, err = run(["originals", "pains", str(bad)], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_directory_as_input_file_is_a_clean_error(self):
        code, _, err = run(["originals", "pains", str(self.root)], llm=FakeLLM(), root=self.root)
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_keyboard_interrupt_returns_130(self):
        class Interrupting:
            def run(self, prompt):
                raise KeyboardInterrupt

        code, _, err = run(["ping"], llm=Interrupting(), root=self.root)
        self.assertEqual(code, 130)
        self.assertIn("Перервано.", err)

    def test_web_port_in_use_is_a_readable_error(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            port = busy.getsockname()[1]
            code, _, err = run(["--backend", "demo", "web", "--port", str(port)], root=self.root)
        self.assertEqual(code, 1)
        self.assertIn(f"Порт {port} зайнятий. Вкажіть інший: --port N.", err)

    def test_default_web_port_is_8780(self):
        self.assertEqual(cli.build_parser().parse_args(["web"]).port, 8780)

    def test_build_rejects_unknown_status_at_parse_time(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(["originals", "build", "--concept", "1", "--status", "bogus"])

    def test_visuals_reports_unavailable_without_a_backend(self):
        code, out, _ = run(["visuals"], root=self.root, images=visuals.NoImages())
        self.assertEqual(code, 0)
        self.assertIn("недоступно", out)

    def test_visuals_with_a_working_provider(self):
        code, out, _ = run(["visuals"], root=self.root, images=FakeImages())
        self.assertEqual(code, 0)
        self.assertIn("доступні", out)

    def _original(self):
        from engine.service import Engine
        return Engine(self.root, FakeLLM()).library.add(
            "Socks", "lost socks", SAMPLE_BLOCKS, shot_list=["Close-up of a drum", "A hand and a drawer"])

    def test_images_shot_writes_a_file_and_prints_its_path(self):
        self._original()
        code, out, err = run(["images", "shot", "o001", "2"], llm=FakeLLM(), root=self.root, images=FakeImages())
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["key"], "shot-2")
        self.assertTrue(Path(data["path"]).is_file())

    def test_images_shot_out_of_range_is_a_clean_error(self):
        self._original()
        code, _, err = run(["images", "shot", "o001", "9"], llm=FakeLLM(), root=self.root, images=FakeImages())
        self.assertEqual(code, 1)
        self.assertIn("Помилка", err)

    def test_images_variant_needs_saved_variants_first(self):
        self._original()
        code, _, err = run(["images", "variant", "o001", "1"], llm=FakeLLM(), root=self.root, images=FakeImages())
        self.assertEqual(code, 1)
        self.assertIn("Спершу", err)

    def test_images_variant_and_list(self):
        self._original()
        out_dir = self.root / "out"
        out_dir.mkdir(exist_ok=True)
        variants = [{"hook": "Where do lost socks go?", "first_line": "Every wash.", "onscreen": "Lost sock mystery"}]
        (out_dir / "o001_variants.json").write_text(json.dumps(variants), encoding="utf-8")
        fake = FakeImages()
        code, out, err = run(["images", "variant", "o001", "1"], llm=FakeLLM(), root=self.root, images=fake)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["key"].startswith("variant-"))
        code, out, _ = run(["images", "list", "o001"], llm=FakeLLM(), root=self.root, images=fake)
        self.assertEqual(len(json.loads(out)["keys"]), 1)
        code, _, err = run(["images", "variant", "o001", "5"], llm=FakeLLM(), root=self.root, images=fake)
        self.assertEqual(code, 1)

    def test_images_without_a_provider_say_why(self):
        self._original()
        code, _, err = run(["images", "shot", "o001", "1"], llm=FakeLLM(), root=self.root, images=visuals.NoImages())
        self.assertEqual(code, 1)
        self.assertIn("не налаштовано", err)


if __name__ == "__main__":
    unittest.main()
