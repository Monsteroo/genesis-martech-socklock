import unittest

from engine import visuals


class VisualsTests(unittest.TestCase):
    def test_disabled_with_reason(self):
        ok, reason = visuals.available()
        self.assertFalse(ok)
        self.assertIn("Gemini", reason)

    def test_storyboard_raises_unavailable(self):
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.storyboard_frames(["frame 1"])


if __name__ == "__main__":
    unittest.main()
