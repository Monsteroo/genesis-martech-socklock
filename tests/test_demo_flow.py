import contextlib
import io
import json
import unittest

from engine import cli
from engine.library import parse_script
from tests.helpers import ROOT, SAMPLE_BLOCKS, make_root


class SamplesTests(unittest.TestCase):
    def test_winner_script_matches_the_test_fixture(self):
        text = (ROOT / "samples" / "winner_script.txt").read_text(encoding="utf-8")
        self.assertEqual(parse_script(text), SAMPLE_BLOCKS)

    def test_pains_file_is_labelled_synthetic(self):
        text = (ROOT / "samples" / "pains.txt").read_text(encoding="utf-8")
        self.assertIn("SYNTHETIC", text.splitlines()[0])


class DemoFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--backend", "demo", *argv], root=self.root)
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def test_full_flow_offline(self):
        pains_file = str(ROOT / "samples" / "pains.txt")

        pains = json.loads(self.run_cli("originals", "pains", pains_file))
        self.assertEqual(len(pains["pains"]), 3)
        self.assertEqual(pains["dropped_quotes"], 0)  # every demo quote is literally in pains.txt

        concepts = json.loads(self.run_cli("originals", "concepts", "--pain", "1"))
        self.assertEqual(len(concepts["concepts"]), 5)

        built = json.loads(self.run_cli("originals", "build", "--concept", "1"))
        self.assertEqual(built["id"], "o001")
        self.assertEqual(built["blocks"], SAMPLE_BLOCKS)

        self.run_cli("library", "status", "o001", "winner")

        variants = json.loads(self.run_cli("modify", "variants", "o001", "--instruction", "5 hooks", "--count", "5"))
        self.assertEqual(len(variants), 5)

        loc = json.loads(self.run_cli("modify", "localize", "o001", "--market", "de", "--market", "fr"))
        self.assertTrue(loc["de"]["needs_review"])  # the German on-screen text is deliberately too long
        self.assertFalse(loc["fr"]["needs_review"])

        composed = json.loads(self.run_cli(
            "modify", "compose", "--pain", "Matching socks wastes time",
            "--reuse", "DEMO=o001", "--reuse", "PROOF=o001", "--reuse", "CTA=o001",
            "--new", "HOOK", "--new", "PAIN"))
        self.assertEqual(composed["reuse_share"], 0.6)

        for name in ("o001_variants.json", "o001_localize_de.json", "o001_localize_fr.json", "compose_1.json"):
            self.assertTrue((self.root / "out" / name).exists(), name)

    def test_ping_in_demo_mode(self):
        self.assertEqual(json.loads(self.run_cli("ping")), {"answer": "ok"})


if __name__ == "__main__":
    unittest.main()
