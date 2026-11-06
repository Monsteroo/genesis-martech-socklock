import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from engine import cli
from engine.library import parse_script
from tests.helpers import ROOT, SAMPLE_BLOCKS, make_root

CYRILLIC = re.compile("[\u0400-\u04ff]")


class SamplesTests(unittest.TestCase):
    def test_winner_script_matches_the_test_fixture(self):
        text = (ROOT / "samples" / "winner_script.txt").read_text(encoding="utf-8")
        self.assertEqual(parse_script(text), SAMPLE_BLOCKS)

    def test_pains_file_is_labelled_synthetic(self):
        text = (ROOT / "samples" / "pains.txt").read_text(encoding="utf-8")
        self.assertIn("SYNTHETIC", text.splitlines()[0])

    def test_demo_frame_is_a_png(self):
        data = (ROOT / "samples" / "demo" / "frame.png").read_bytes()
        self.assertTrue(data.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])))
        self.assertGreater(len(data), 10_000)


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
        self.assertTrue(all(CYRILLIC.search(p["pain"]) for p in pains["pains"]))  # pains are in Ukrainian

        concepts = json.loads(self.run_cli("originals", "concepts", "--pain", "1"))
        self.assertEqual(len(concepts["concepts"]), 5)
        self.assertTrue(all(CYRILLIC.search(c["angle"]) for c in concepts["concepts"]))  # angles in Ukrainian
        self.assertFalse(any(CYRILLIC.search(c["hook"]) for c in concepts["concepts"]))  # hooks stay English

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

    def test_demo_images_offline(self):
        pains_file = str(ROOT / "samples" / "pains.txt")
        self.run_cli("originals", "pains", pains_file)
        self.run_cli("originals", "concepts", "--pain", "1")
        self.run_cli("originals", "build", "--concept", "1", "--status", "winner")
        shot = json.loads(self.run_cli("images", "shot", "o001", "1"))
        self.assertEqual(shot["key"], "shot-1")
        self.assertTrue(Path(shot["path"]).read_bytes().startswith(bytes([137, 80, 78, 71])))
        again = json.loads(self.run_cli("images", "shot", "o001", "1"))
        self.assertTrue(again["cached"])
        self.run_cli("modify", "variants", "o001", "--instruction", "5 hooks", "--count", "5")
        variant = json.loads(self.run_cli("images", "variant", "o001", "1"))
        self.assertTrue(variant["key"].startswith("variant-"))
        listing = json.loads(self.run_cli("images", "list", "o001"))
        self.assertEqual(listing["keys"], sorted(["shot-1", variant["key"]]))
        self.assertIn("доступні", self.run_cli("visuals"))

    def test_ping_in_demo_mode(self):
        self.assertEqual(json.loads(self.run_cli("ping")), {"answer": "ok"})


if __name__ == "__main__":
    unittest.main()
