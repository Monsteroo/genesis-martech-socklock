import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine import llm, visuals
from tests.helpers import PNG_MAGIC

ORIGINAL = {"pain": "Lost socks"}


def make_runner(codex_home, thread_id="th-1", login_text="Logged in using ChatGPT",
                features="image_generation   stable   true\n", make_image=True, exec_rc=0, events=()):
    calls = []

    def runner(args, env, timeout, cwd):
        calls.append({"args": args, "env": env, "timeout": timeout, "cwd": cwd})
        if args[1] == "login":
            return SimpleNamespace(returncode=0, stdout="", stderr=login_text)
        if args[1] == "features":
            return SimpleNamespace(returncode=0, stdout=features, stderr="")
        if make_image:
            folder = Path(codex_home) / "generated_images" / thread_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "exec-1.png").write_bytes(PNG_MAGIC + b"image")
        out = json.dumps({"type": "thread.started", "thread_id": thread_id})
        for event in events:
            out += "\n" + json.dumps(event)
        return SimpleNamespace(returncode=exec_rc, stdout=out + "\n", stderr="boom" if exec_rc else "")

    return runner, calls


class PromptTests(unittest.TestCase):
    def test_frame_prompt(self):
        text = visuals.frame_prompt("A hand pulls one sock from a drawer", ORIGINAL, "soft light")
        for part in ("A hand pulls one sock from a drawer", "Lost socks", "soft light", "No text", "9:16"):
            self.assertIn(part, text)

    def test_variant_prompt_never_asks_to_render_the_onscreen_text(self):
        v = {"hook": "Where do lost socks go?", "first_line": "Every wash, one vanishes.", "onscreen": "Lost sock mystery"}
        text = visuals.variant_prompt(v, ORIGINAL, "soft light")
        self.assertIn("Where do lost socks go?", text)
        self.assertIn("Lost sock mystery", text)
        self.assertIn("do NOT render", text)
        self.assertIn("9:16", text)


class SimpleProviderTests(unittest.TestCase):
    def test_no_images_is_unavailable_and_raises_an_llm_error(self):
        p = visuals.NoImages()
        ok, reason = p.available()
        self.assertFalse(ok)
        self.assertTrue(reason)
        with self.assertRaises(visuals.VisualsUnavailable) as ctx:
            p.generate("x", Path(tempfile.mkdtemp()) / "a.png")
        self.assertIsInstance(ctx.exception, llm.LLMError)

    def test_demo_provider_copies_the_frame(self):
        tmp = Path(tempfile.mkdtemp())
        frame = tmp / "frame.png"
        frame.write_bytes(PNG_MAGIC + b"demo")
        p = visuals.DemoImageProvider(frame=frame)
        self.assertEqual(p.available(), (True, ""))
        dest = tmp / "out" / "o001" / "shot-1.png"
        self.assertEqual(p.generate("anything", dest), dest)
        self.assertEqual(dest.read_bytes(), PNG_MAGIC + b"demo")

    def test_demo_interrupted_copy_leaves_no_partial_file(self):
        tmp = Path(tempfile.mkdtemp())
        frame = tmp / "frame.png"
        frame.write_bytes(PNG_MAGIC)
        dest = tmp / "out" / "a.png"

        def broken_copy(src, dst):
            Path(dst).write_bytes(b"trunc")
            raise OSError("disk full")

        with mock.patch.object(visuals.shutil, "copyfile", broken_copy):
            with self.assertRaises(OSError):
                visuals.DemoImageProvider(frame=frame).generate("x", dest)
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.glob("*.part")), [])

    def test_demo_provider_without_frame(self):
        p = visuals.DemoImageProvider(frame=Path(tempfile.mkdtemp()) / "missing.png")
        self.assertFalse(p.available()[0])
        with self.assertRaises(visuals.VisualsUnavailable):
            p.generate("x", Path(tempfile.mkdtemp()) / "a.png")


class CodexImageProviderTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()) / "codex"
        self.dest = Path(tempfile.mkdtemp()) / "lib" / "o001" / "shot-1.png"

    def provider(self, **kw):
        runner, calls = make_runner(self.home, **kw)
        return visuals.CodexImageProvider(runner=runner, binary="/fake/codex", codex_home=self.home), calls

    def test_generates_and_copies_the_image(self):
        p, calls = self.provider()
        self.assertEqual(p.generate("a scene", self.dest), self.dest)
        self.assertEqual(self.dest.read_bytes(), PNG_MAGIC + b"image")
        args = calls[1]["args"]
        self.assertEqual(args[:2], ["/fake/codex", "exec"])
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        for flag in ("--ephemeral", "--ignore-user-config", "--json", "--skip-git-repo-check"):
            self.assertIn(flag, args)
        self.assertIn("a scene", args[-1])
        self.assertIn("image generation tool", args[-1])
        self.assertEqual(calls[1]["timeout"], visuals.IMAGE_TIMEOUT)

    def test_api_keys_are_stripped(self):
        p, calls = self.provider()
        fake_env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": "u", "CODEX_API_KEY": "c", "PATH": "/bin"}
        with mock.patch.dict(os.environ, fake_env):
            p.generate("a scene", self.dest)
        for call in calls:
            for key in llm.STRIPPED_ENV:
                self.assertNotIn(key, call["env"])

    def test_not_logged_in_never_starts_exec(self):
        p, calls = self.provider(login_text="Not logged in")
        with self.assertRaises(llm.LLMError):
            p.generate("a scene", self.dest)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.dest.exists())

    def test_timeout_is_reported_and_not_retried(self):
        calls = []

        def runner(args, env, timeout, cwd):
            calls.append(args)
            if args[1] == "login":
                return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")
            raise subprocess.TimeoutExpired(args, timeout)

        p = visuals.CodexImageProvider(runner=runner, binary="/fake/codex", codex_home=self.home)
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        self.assertIn("не створив зображення", str(ctx.exception))
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.dest.exists())

    def test_nonzero_exit_is_an_error(self):
        p, _ = self.provider(exec_rc=3)
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        self.assertIn("boom", str(ctx.exception))

    def test_no_image_produced_is_an_error(self):
        p, _ = self.provider(make_image=False)
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        self.assertIn("image_generation", str(ctx.exception))
        self.assertFalse(self.dest.exists())

    def test_error_event_message_is_shown_on_nonzero_exit(self):
        p, _ = self.provider(exec_rc=1, events=[{"type": "error", "message": "usage limit reached"}])
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        self.assertIn("usage limit reached", str(ctx.exception))
        self.assertNotIn("boom", str(ctx.exception))

    def test_turn_failed_event_message_is_used_and_the_last_one_wins(self):
        events = [{"type": "error", "message": "first"},
                  {"type": "turn.failed", "error": {"message": "second failure"}}]
        p, _ = self.provider(exec_rc=1, events=events)
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        self.assertIn("second failure", str(ctx.exception))
        self.assertNotIn("first", str(ctx.exception))

    def test_no_image_with_error_event_shows_hint_and_event(self):
        p, _ = self.provider(make_image=False, events=[{"type": "error", "message": "quota exhausted"}])
        with self.assertRaises(llm.LLMError) as ctx:
            p.generate("a scene", self.dest)
        text = str(ctx.exception)
        self.assertIn("generated_images", text)
        self.assertIn("th-1", text)
        self.assertIn("вичерпано ліміт підписки", text)
        self.assertIn("image_generation", text)
        self.assertIn("quota exhausted", text)

    def test_last_error_ignores_noise(self):
        self.assertIsNone(visuals._last_error("not json\n[1]\n" + json.dumps({"type": "error", "message": 5})))
        self.assertIsNone(visuals._last_error(None))

    def test_hostile_thread_id_is_rejected(self):
        p, _ = self.provider(thread_id="../../etc")
        with self.assertRaises(llm.LLMError):
            p.generate("a scene", self.dest)
        self.assertFalse(self.dest.exists())

    def test_interrupted_copy_leaves_no_partial_file(self):
        p, _ = self.provider()

        def broken_copy(src, dst):
            Path(dst).write_bytes(b"trunc")
            raise OSError("disk full")

        with mock.patch.object(visuals.shutil, "copyfile", broken_copy):
            with self.assertRaises(OSError):
                p.generate("a scene", self.dest)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])

    def test_successful_copy_leaves_no_part_file(self):
        p, _ = self.provider()
        p.generate("a scene", self.dest)
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])

    def test_empty_and_oversized_prompts_never_call_the_runner(self):
        p, calls = self.provider()
        for bad in ("", "   ", "x" * (visuals.MAX_IMAGE_PROMPT + 1)):
            with self.assertRaises(llm.LLMError):
                p.generate(bad, self.dest)
        self.assertEqual(calls, [])

    def test_available_true_when_logged_in_and_feature_on(self):
        p, _ = self.provider()
        self.assertEqual(p.available(), (True, ""))

    def test_available_false_when_feature_off(self):
        p, _ = self.provider(features="image_generation   stable   false\n")
        ok, reason = p.available()
        self.assertFalse(ok)
        self.assertIn("image_generation", reason)

    def test_available_false_when_not_logged_in(self):
        p, _ = self.provider(login_text="Not logged in")
        self.assertFalse(p.available()[0])

    def test_available_false_without_binary(self):
        with mock.patch.object(visuals, "codex_binary", return_value=None):
            p = visuals.CodexImageProvider(runner=lambda *a: None, codex_home=self.home)
        ok, reason = p.available()
        self.assertFalse(ok)
        self.assertIn("Codex", reason)


class RegistryTests(unittest.TestCase):
    def test_builtin_providers(self):
        self.assertEqual(visuals.get_provider("demo").name, "demo")
        self.assertEqual(visuals.get_provider("codex").name, "codex")

    def test_unknown_provider_raises(self):
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.get_provider("nope")
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.get_provider("demo", kind="video")

    def test_a_video_provider_such_as_heygen_plugs_in_without_touching_anything_else(self):
        class FakeHeygen:
            kind = "video"
            name = "heygen"

            def available(self):
                return True, ""

            def generate(self, prompt, dest):
                Path(dest).write_bytes(b"video")
                return Path(dest)

        visuals.register_provider("heygen", "video", lambda **kw: FakeHeygen())
        self.addCleanup(visuals._REGISTRY.pop, ("video", "heygen"), None)
        p = visuals.get_provider("heygen", kind="video")
        self.assertEqual((p.kind, p.name), ("video", "heygen"))
        dest = Path(tempfile.mkdtemp()) / "dub.mp4"
        self.assertEqual(p.generate("script", dest), dest)
        # the image side is unaffected
        self.assertEqual(visuals.get_provider("demo").kind, "image")


if __name__ == "__main__":
    unittest.main()
