import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import llm, visuals
from tests.helpers import PNG_MAGIC, TEST_KEY, FakeTransport, http_error, http_ok

ORIGINAL = {"pain": "Lost socks"}


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


class RegistryTests(unittest.TestCase):
    def test_builtin_providers(self):
        self.assertEqual(visuals.get_provider("demo").name, "demo")
        self.assertEqual(visuals.get_provider("openai").name, "openai")
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.get_provider("codex")

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


def image_response(data=PNG_MAGIC + b"img"):
    return http_ok({"data": [{"b64_json": base64.b64encode(data).decode("ascii")}]})


class OpenAIImageProviderTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OPENAI_API_KEY": TEST_KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dest = Path(tempfile.mkdtemp()) / "lib" / "o001" / "shot-1.png"

    def test_generates_and_writes_the_png(self):
        t = FakeTransport(image_response())
        p = visuals.OpenAIImageProvider(transport=t)
        self.assertEqual(p.generate("a scene", self.dest), self.dest)
        self.assertEqual(self.dest.read_bytes(), PNG_MAGIC + b"img")
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])
        req = t.requests[0]
        self.assertTrue(req["url"].endswith("/images/generations"))
        body = json.loads(req["body"])
        self.assertEqual(body, {"model": "gpt-image-2", "prompt": "a scene", "size": "1024x1536",
                                "quality": "medium", "n": 1, "output_format": "png"})
        self.assertEqual(req["timeout"], visuals.IMAGE_API_TIMEOUT)

    def test_env_and_explicit_settings(self):
        env = {"OPENAI_IMAGE_MODEL": "m-env", "OPENAI_IMAGE_SIZE": "1024x1024", "OPENAI_IMAGE_QUALITY": "low"}
        with mock.patch.dict(os.environ, env):
            p = visuals.OpenAIImageProvider()
            self.assertEqual((p.model, p.size, p.quality), ("m-env", "1024x1024", "low"))
            q = visuals.OpenAIImageProvider(model="m2", size="1536x1024", quality="high")
            self.assertEqual((q.model, q.size, q.quality), ("m2", "1536x1024", "high"))

    def test_available_depends_on_the_key_only(self):
        self.assertEqual(visuals.OpenAIImageProvider().available(), (True, ""))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            ok, reason = visuals.OpenAIImageProvider().available()
        self.assertFalse(ok)
        self.assertIn(".env", reason)

    def test_missing_key_never_calls_the_transport(self):
        t = FakeTransport()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(llm.LLMError):
                visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        self.assertEqual(t.requests, [])

    def test_bad_answers_leave_no_file(self):
        bad = [
            http_ok({"data": []}),
            http_ok({"data": [{"b64_json": "not base64!!"}]}),
            image_response(b"not a png at all"),
            http_ok({}),
        ]
        for response in bad:
            with self.assertRaises(llm.LLMError):
                visuals.OpenAIImageProvider(transport=FakeTransport(response)).generate("a scene", self.dest)
            self.assertFalse(self.dest.exists())
            self.assertEqual(list(self.dest.parent.glob("*.part")), [])

    def test_api_errors_are_not_retried_and_never_leak_the_key(self):
        t = FakeTransport(http_error(429, {"error": {"message": f"limit for {TEST_KEY}"}}), image_response())
        with self.assertRaises(llm.LLMError) as ctx:
            visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        self.assertNotIn(TEST_KEY, str(ctx.exception))
        self.assertEqual(len(t.requests), 1)
        self.assertFalse(self.dest.exists())

    def test_empty_and_oversized_prompts_never_call_the_transport(self):
        t = FakeTransport()
        p = visuals.OpenAIImageProvider(transport=t)
        for bad in ("", "   ", "x" * (visuals.MAX_IMAGE_PROMPT + 1)):
            with self.assertRaises(llm.LLMError):
                p.generate(bad, self.dest)
        self.assertEqual(t.requests, [])

    def test_two_providers_can_generate_in_parallel_threads(self):
        import threading
        results, errors = [], []

        def run(n):
            try:
                t = FakeTransport(image_response(PNG_MAGIC + bytes([n])))
                dest = self.dest.parent / f"shot-{n}.png"
                visuals.OpenAIImageProvider(transport=t).generate("a scene", dest)
                results.append(dest.read_bytes())
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(n,)) for n in range(1, 5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), sorted(PNG_MAGIC + bytes([n]) for n in range(1, 5)))


    def test_failing_replace_propagates_and_leaves_no_part(self):
        t = FakeTransport(image_response())
        with mock.patch.object(visuals.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])
        self.assertFalse(self.dest.exists())

    def test_destination_folder_problem_fails_before_the_request(self):
        blocker = Path(tempfile.mkdtemp()) / "afile"
        blocker.write_text("x")
        t = FakeTransport(image_response())
        with self.assertRaises(llm.LLMError) as ctx:
            visuals.OpenAIImageProvider(transport=t).generate("a scene", blocker / "sub" / "s.png")
        self.assertNotIn(str(blocker), str(ctx.exception))
        self.assertEqual(t.requests, [])

    def test_non_string_image_data_is_a_readable_error(self):
        for bad in (None, 123, ["x"]):
            t = FakeTransport(http_ok({"data": [{"b64_json": bad}]}))
            with self.assertRaises(llm.LLMError) as ctx:
                visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
            self.assertIn("пошкоджені", str(ctx.exception))
            self.assertEqual(len(t.requests), 1)
            self.assertFalse(self.dest.exists())

    def test_newline_wrapped_base64_is_accepted(self):
        raw = base64.b64encode(PNG_MAGIC + b"img" * 40).decode("ascii")
        wrapped = "\n".join(raw[i:i + 20] for i in range(0, len(raw), 20))
        t = FakeTransport(http_ok({"data": [{"b64_json": wrapped}]}))
        visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        self.assertEqual(self.dest.read_bytes(), PNG_MAGIC + b"img" * 40)

    def test_oversized_image_data_is_rejected_without_decoding(self):
        t = FakeTransport(http_ok({"data": [{"b64_json": "A" * 40_000_001}]}))
        with mock.patch.object(visuals.base64, "b64decode") as dec:
            with self.assertRaises(llm.LLMError) as ctx:
                visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        dec.assert_not_called()
        self.assertIn("надто великі", str(ctx.exception))
        self.assertFalse(self.dest.exists())

    def test_api_400_is_shown_readably(self):
        t = FakeTransport(http_error(400, {"error": {"message": f"Unknown parameter: output_format {TEST_KEY}"}}))
        with self.assertRaises(llm.LLMError) as ctx:
            visuals.OpenAIImageProvider(transport=t).generate("a scene", self.dest)
        msg = str(ctx.exception)
        self.assertIn("400", msg)
        self.assertIn("output_format", msg)
        self.assertNotIn(TEST_KEY, msg)


class AtomicWriteConcurrencyTests(unittest.TestCase):
    def _race(self, op):
        import threading
        dest = Path(tempfile.mkdtemp()) / "same.png"
        sources, errors = [], []
        real = os.replace
        barrier = threading.Barrier(2)

        def spy(src, dst):
            sources.append(str(src))
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return real(src, dst)

        def run(n):
            try:
                op(dest, n)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with mock.patch.object(visuals.os, "replace", spy):
            ths = [threading.Thread(target=run, args=(n,)) for n in (1, 2)]
            [t.start() for t in ths]
            [t.join() for t in ths]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(sources)), 2)
        self.assertIn(dest.read_bytes(), (PNG_MAGIC + b"\x01" * 5000, PNG_MAGIC + b"\x02" * 5000))
        self.assertEqual(list(dest.parent.glob("*.part")), [])

    def test_final_files_are_world_readable(self):
        tmp = Path(tempfile.mkdtemp())
        visuals._atomic_write(tmp / "w.png", PNG_MAGIC)
        src = tmp / "src.png"
        src.write_bytes(PNG_MAGIC)
        visuals._atomic_copy(src, tmp / "c.png")
        for name in ("w.png", "c.png"):
            self.assertEqual((tmp / name).stat().st_mode & 0o777, 0o644)

    def test_atomic_write_uses_unique_temp_files(self):
        self._race(lambda dest, n: visuals._atomic_write(dest, PNG_MAGIC + bytes([n]) * 5000))

    def test_atomic_copy_uses_unique_temp_files(self):
        tmp = Path(tempfile.mkdtemp())
        srcs = {}
        for n in (1, 2):
            srcs[n] = tmp / f"s{n}.png"
            srcs[n].write_bytes(PNG_MAGIC + bytes([n]) * 5000)
        self._race(lambda dest, n: visuals._atomic_copy(srcs[n], dest))


class DemoDelayTests(unittest.TestCase):
    def test_delay_slows_the_demo_provider_down(self):
        import time
        tmp = Path(tempfile.mkdtemp())
        frame = tmp / "frame.png"
        frame.write_bytes(PNG_MAGIC + b"demo")
        p = visuals.DemoImageProvider(frame=frame, delay=0.2)
        start = time.monotonic()
        p.generate("x", tmp / "out.png")
        self.assertGreaterEqual(time.monotonic() - start, 0.15)

    def test_delay_comes_from_the_environment_and_defaults_to_zero(self):
        frame = Path(tempfile.mkdtemp()) / "frame.png"
        self.assertEqual(visuals.DemoImageProvider(frame=frame).delay, 0)
        with mock.patch.dict(os.environ, {"DEMO_IMAGE_DELAY": "1.5"}):
            self.assertEqual(visuals.DemoImageProvider(frame=frame).delay, 1.5)
        with mock.patch.dict(os.environ, {"DEMO_IMAGE_DELAY": "junk"}):
            self.assertEqual(visuals.DemoImageProvider(frame=frame).delay, 0)

    def test_delay_ignores_non_finite_and_is_clamped(self):
        frame = Path(tempfile.mkdtemp()) / "frame.png"
        for raw, want in (("inf", 0), ("nan", 0), ("-3", 0), ("1000", 60)):
            with mock.patch.dict(os.environ, {"DEMO_IMAGE_DELAY": raw}):
                self.assertEqual(visuals.DemoImageProvider(frame=frame).delay, want, raw)


class QuickPromptTests(unittest.TestCase):
    def test_sizes_styles_and_variations(self):
        self.assertEqual(visuals.SIZES, {"vertical": "1024x1536", "square": "1024x1024", "wide": "1536x1024"})
        self.assertEqual(sorted(visuals.STYLES), ["illustration", "lifestyle", "minimal", "product"])
        self.assertEqual(len(visuals.VARIATIONS), 8)

    def test_quick_prompt_parts(self):
        text = visuals.quick_prompt("  A cup of coffee  ", "minimal", 1)
        self.assertTrue(text.startswith("A cup of coffee"))
        self.assertIn(visuals.STYLES["minimal"], text)
        self.assertIn(visuals.VARIATIONS[1], text)
        self.assertIn("No text, no logos, no watermarks", text)

    def test_quick_prompt_without_style_or_variation(self):
        text = visuals.quick_prompt("A cup", None, None)
        self.assertEqual(text, "A cup\nNo text, no logos, no watermarks.")

    def test_variation_index_wraps_around(self):
        self.assertIn(visuals.VARIATIONS[0], visuals.quick_prompt("x", None, len(visuals.VARIATIONS)))

    def test_unknown_style_is_ignored_safely(self):
        self.assertNotIn("None", visuals.quick_prompt("x", "bogus", None))


class SizeParameterTests(unittest.TestCase):
    def test_openai_provider_sends_the_requested_size(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": TEST_KEY}):
            t = FakeTransport(image_response(), image_response())
            p = visuals.OpenAIImageProvider(transport=t)
            tmp = Path(tempfile.mkdtemp())
            p.generate("a scene", tmp / "a.png", size="1536x1024")
            p.generate("a scene", tmp / "b.png")
        self.assertEqual(json.loads(t.requests[0]["body"])["size"], "1536x1024")
        self.assertEqual(json.loads(t.requests[1]["body"])["size"], "1024x1536")

    def test_demo_and_none_providers_accept_the_size_argument(self):
        tmp = Path(tempfile.mkdtemp())
        frame = tmp / "frame.png"
        frame.write_bytes(PNG_MAGIC + b"demo")
        visuals.DemoImageProvider(frame=frame, delay=0).generate("x", tmp / "o.png", size="1024x1024")
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.NoImages().generate("x", tmp / "n.png", size="1024x1024")

if __name__ == "__main__":
    unittest.main()


class ModifyPromptTests(unittest.TestCase):
    def test_with_a_change(self):
        text = visuals.modify_prompt("Socks on a floor", "make it evening light", 3)
        self.assertIn("make it evening light", text)
        self.assertIn("Socks on a floor", text)
        self.assertNotIn(visuals.VARIATIONS[3], text)
        self.assertIn("No text, no logos, no watermarks", text)

    def test_without_a_change_uses_a_variation_hint(self):
        text = visuals.modify_prompt("Socks on a floor", None, 3)
        self.assertIn(visuals.VARIATIONS[3], text)
        self.assertIn(visuals.VARIATIONS[0], visuals.modify_prompt("x", "", len(visuals.VARIATIONS)))


class OpenAIEditTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OPENAI_API_KEY": TEST_KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "orig.png"
        self.src.write_bytes(PNG_MAGIC + b"original-bytes")
        self.dest = self.tmp / "out" / "mod.png"

    def test_edit_posts_multipart_and_writes_the_png(self):
        t = FakeTransport(image_response())
        p = visuals.OpenAIImageProvider(transport=t)
        self.assertEqual(p.edit("change the light", self.src, self.dest, size="1536x1024"), self.dest)
        self.assertEqual(self.dest.read_bytes(), PNG_MAGIC + b"img")
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])
        req = t.requests[0]
        self.assertTrue(req["url"].endswith("/images/edits"))
        self.assertTrue(req["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
        body = req["body"]
        for part in (b'name="model"\r\n\r\ngpt-image-2', b'name="prompt"\r\n\r\nchange the light',
                     b'name="size"\r\n\r\n1536x1024', b'name="quality"\r\n\r\nmedium', b'name="n"\r\n\r\n1',
                     b'name="image"; filename="original.png"', PNG_MAGIC + b"original-bytes"):
            self.assertIn(part, body)
        self.assertEqual(req["timeout"], visuals.IMAGE_API_TIMEOUT)

    def test_default_size_comes_from_the_provider(self):
        t = FakeTransport(image_response())
        visuals.OpenAIImageProvider(transport=t).edit("x", self.src, self.dest)
        self.assertIn(b'name="size"\r\n\r\n1024x1536', t.requests[0]["body"])

    def test_bad_inputs_never_reach_the_api(self):
        t = FakeTransport()
        p = visuals.OpenAIImageProvider(transport=t)
        not_png = self.tmp / "x.png"
        not_png.write_bytes(b"GIF89a....")
        for prompt, src in (("", self.src), ("  ", self.src), ("x" * (visuals.MAX_IMAGE_PROMPT + 1), self.src),
                            ("ok", self.tmp / "missing.png"), ("ok", not_png), ("ok", self.tmp)):
            with self.assertRaises(llm.LLMError, msg=(prompt[:5], str(src))):
                p.edit(prompt, src, self.dest)
        with mock.patch.object(visuals, "MAX_EDIT_SOURCE", 5):
            with self.assertRaises(llm.LLMError):
                p.edit("ok", self.src, self.dest)
        self.assertEqual(t.requests, [])
        self.assertFalse(self.dest.exists())

    def test_bad_answers_leave_no_file_and_errors_do_not_retry_or_leak(self):
        for response in (http_ok({"data": []}), http_ok({"data": [{"b64_json": "!!"}]}), image_response(b"not png"),
                         http_ok({"data": [{"b64_json": None}]})):
            with self.assertRaises(llm.LLMError):
                visuals.OpenAIImageProvider(transport=FakeTransport(response)).edit("x", self.src, self.dest)
            self.assertFalse(self.dest.exists())
        t = FakeTransport(http_error(429, {"error": {"message": f"limit {TEST_KEY}"}}), image_response())
        with self.assertRaises(llm.LLMError) as ctx:
            visuals.OpenAIImageProvider(transport=t).edit("x", self.src, self.dest)
        self.assertNotIn(TEST_KEY, str(ctx.exception))
        self.assertEqual(len(t.requests), 1)

    def test_missing_key_never_calls_the_transport(self):
        t = FakeTransport()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(llm.LLMError):
                visuals.OpenAIImageProvider(transport=t).edit("x", self.src, self.dest)
        self.assertEqual(t.requests, [])

    def test_demo_and_none_providers_have_edit(self):
        frame = self.tmp / "frame.png"
        frame.write_bytes(PNG_MAGIC + b"demo")
        out = self.tmp / "demo-out.png"
        visuals.DemoImageProvider(frame=frame, delay=0).edit("x", self.src, out, size="1024x1024")
        self.assertEqual(out.read_bytes(), PNG_MAGIC + b"demo")
        with self.assertRaises(visuals.VisualsUnavailable):
            visuals.NoImages().edit("x", self.src, out)
