import tempfile
import unittest
from pathlib import Path

from engine import envfile


class ParseTests(unittest.TestCase):
    def test_basic_lines(self):
        text = "# comment\n\nA=1\nexport B=two\nC = three \nD=\n"
        self.assertEqual(envfile.parse(text), {"A": "1", "B": "two", "C": "three", "D": ""})

    def test_quotes_and_inline_comments(self):
        text = 'A="x y"\nB=\'z\'\nC=plain # note\nD="keep # this"\n'
        self.assertEqual(envfile.parse(text), {"A": "x y", "B": "z", "C": "plain", "D": "keep # this"})

    def test_garbage_lines_are_ignored(self):
        self.assertEqual(envfile.parse("no equals\n=novalue\nBAD KEY=1\nOK=1\n"), {"OK": "1"})

    def test_crlf(self):
        self.assertEqual(envfile.parse("A=1\r\nB=2\r\n"), {"A": "1", "B": "2"})


class LoadTests(unittest.TestCase):
    def write(self, text):
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_into_the_given_environment_and_returns_names_only(self):
        env = {}
        names = envfile.load_dotenv(self.write("A=1\nB=2\n"), env)
        self.assertEqual(env, {"A": "1", "B": "2"})
        self.assertEqual(sorted(names), ["A", "B"])

    def test_existing_non_empty_os_values_win(self):
        env = {"A": "from-os", "B": ""}
        envfile.load_dotenv(self.write("A=file\nB=file\n"), env)
        self.assertEqual(env, {"A": "from-os", "B": "file"})

    def test_missing_file_is_fine(self):
        self.assertEqual(envfile.load_dotenv(Path(tempfile.mkdtemp()) / "nope", {}), [])


if __name__ == "__main__":
    unittest.main()
