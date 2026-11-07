import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import video, visuals
from engine.heygen_api import HeyGenError
from tests.helpers import PNG_MAGIC, FakeTransport, http_ok

KEY = "hg-test-0123456789abcdef"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40
URL = "https://resource2.heygen.ai/videos/x/VIDEO.mp4?sig=abc"


def accepted(vid="vid_1"):
    return {"status": 202, "body": json.dumps({"data": {"video_id": vid, "status": "pending"}}).encode()}


def state(status, **extra):
    return http_ok({"data": {"video_id": "vid_1", "status": status, **extra}})


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"HEYGEN_API_KEY": KEY})
        p.start()
        self.addCleanup(p.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.src = self.dir / "in.png"
        self.src.write_bytes(PNG_MAGIC + b"x" * 100)
        self.dest = self.dir / "out" / "o.mp4"

    def provider(self, transport, **kw):
        kw.setdefault("interval", 0)
        kw.setdefault("timeout", 5)
        return video.HeyGenVideoProvider(transport=transport, **kw)


class MotionPromptTests(unittest.TestCase):
    def test_chips_are_mapped_to_camera_directions(self):
        text = video.motion_prompt("повільний наїзд, тканина лопоче")
        self.assertIn("push-in", text)
        self.assertIn("тканина лопоче", text)
        self.assertIn("no logos", text.lower())
        self.assertIn("first frame", text.lower())

    def test_empty_motion_has_a_default(self):
        self.assertIn("slow", video.motion_prompt("").lower())

    def test_every_chip_has_a_mapping(self):
        for chip in ("повільний наїзд", "відʼїзд", "паралакс", "легкий рух тканини", "плавний поворот", "мерехтіння світла"):
            self.assertIn(chip, video.CHIP_PROMPTS)


class HeyGenProviderTests(Base):
    def test_available_follows_the_key(self):
        self.assertTrue(video.HeyGenVideoProvider().available()[0])
        with mock.patch.dict(os.environ, {"HEYGEN_API_KEY": ""}):
            ok, reason = video.HeyGenVideoProvider().available()
        self.assertFalse(ok)
        self.assertIn("HEYGEN_API_KEY", reason)

    def test_small_image_goes_inline_and_flow_completes(self):
        t = FakeTransport(accepted(), state("pending"), state("processing"), state("completed", video_url=URL),
                          {"status": 200, "body": MP4})
        out = self.provider(t).animate("push in", self.src, self.dest, 5)
        self.assertEqual(out, self.dest)
        self.assertEqual(self.dest.read_bytes(), MP4)
        create = t.requests[0]
        body = json.loads(create["body"])
        self.assertEqual(body["model"], "heygen-video-1")
        self.assertEqual(body["mode"], "image_to_video")
        self.assertEqual(body["duration"], 5)
        self.assertEqual(body["resolution"], "768p")
        self.assertNotIn("aspect_ratio", body)
        self.assertEqual(body["image"]["type"], "base64")
        self.assertEqual(body["image"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(body["image"]["data"]), self.src.read_bytes())
        self.assertTrue(create["headers"]["Idempotency-Key"])
        self.assertEqual([r["method"] for r in t.requests], ["POST", "GET", "GET", "GET", "GET"])
        self.assertNotIn("x-api-key", {k.lower() for k in t.requests[-1]["headers"]})
        self.assertEqual(list(self.dest.parent.glob("*.part")), [])

    def test_large_image_is_uploaded_as_asset(self):
        self.src.write_bytes(PNG_MAGIC + b"x" * (video.MAX_INLINE_BYTES + 10))
        t = FakeTransport(http_ok({"data": {"asset_id": "as_9", "url": "u", "mime_type": "image/png", "size_bytes": 1}}),
                          accepted(), state("completed", video_url=URL), {"status": 200, "body": MP4})
        self.provider(t).animate("p", self.src, self.dest, 10)
        self.assertEqual(t.requests[0]["url"], "https://api.heygen.com/v3/assets")
        body = json.loads(t.requests[1]["body"])
        self.assertEqual(body["image"], {"type": "asset_id", "asset_id": "as_9"})
        self.assertEqual(body["duration"], 10)

    def test_resolution_is_configurable(self):
        t = FakeTransport(accepted(), state("completed", video_url=URL), {"status": 200, "body": MP4})
        self.provider(t, resolution="480p").animate("p", self.src, self.dest, 5)
        self.assertEqual(json.loads(t.requests[0]["body"])["resolution"], "480p")

    def test_failed_job_maps_to_a_generic_error_with_id(self):
        t = FakeTransport(accepted(), state("failed", failure_code="generation_failed", failure_message=f"oops {KEY}"))
        with self.assertRaises(HeyGenError) as ctx:
            self.provider(t).animate("p", self.src, self.dest, 5)
        text = str(ctx.exception)
        self.assertIn("vid_1", text)
        self.assertNotIn(KEY, text)
        self.assertFalse(self.dest.exists())

    def test_cancelled_job(self):
        t = FakeTransport(accepted(), state("cancelled", failure_code="generation_cancelled"))
        with self.assertRaises(HeyGenError):
            self.provider(t).animate("p", self.src, self.dest, 5)

    def test_poll_timeout_is_hard(self):
        t = FakeTransport(accepted(), *[state("processing")] * 1000)
        with self.assertRaises(HeyGenError) as ctx:
            self.provider(t, timeout=0.05, interval=0.01).animate("p", self.src, self.dest, 5)
        self.assertIn("vid_1", str(ctx.exception))
        self.assertLess(len(t.requests), 1000)

    def test_transient_poll_errors_are_tolerated_then_give_up(self):
        t = FakeTransport(accepted(), HeyGenError("net", transient=True), state("completed", video_url=URL), {"status": 200, "body": MP4})
        self.provider(t).animate("p", self.src, self.dest, 5)
        t = FakeTransport(accepted(), *[HeyGenError("net", transient=True)] * 10)
        with self.assertRaises(HeyGenError):
            self.provider(t).animate("p", self.src, self.dest, 5)
        self.assertLessEqual(len(t.requests), 6)

    def test_post_failure_is_not_retried(self):
        t = FakeTransport(HeyGenError("net", transient=True), accepted())
        with self.assertRaises(HeyGenError):
            self.provider(t).animate("p", self.src, self.dest, 5)
        self.assertEqual(len(t.requests), 1)

    def test_bad_video_url_host_is_refused(self):
        t = FakeTransport(accepted(), state("completed", video_url="https://evil.example/a.mp4"))
        with self.assertRaises(HeyGenError):
            self.provider(t).animate("p", self.src, self.dest, 5)
        self.assertEqual(len(t.requests), 2)

    def test_expired_link_is_refreshed_once(self):
        t = FakeTransport(accepted(), state("completed", video_url=URL), {"status": 403, "body": b""},
                          state("completed", video_url=URL), {"status": 200, "body": MP4})
        self.provider(t).animate("p", self.src, self.dest, 5)
        self.assertEqual(self.dest.read_bytes(), MP4)

    def test_source_must_be_a_png(self):
        self.src.write_bytes(b"not a png")
        t = FakeTransport()
        with self.assertRaises(HeyGenError):
            self.provider(t).animate("p", self.src, self.dest, 5)
        self.assertEqual(t.requests, [])

    def test_timeout_from_env(self):
        with mock.patch.dict(os.environ, {"HEYGEN_TIMEOUT": "42"}):
            self.assertEqual(video.HeyGenVideoProvider().timeout, 42)
        with mock.patch.dict(os.environ, {"HEYGEN_TIMEOUT": "abc"}):
            self.assertEqual(video.HeyGenVideoProvider().timeout, 600)
        with mock.patch.dict(os.environ, {"HEYGEN_TIMEOUT": "99999"}):
            self.assertEqual(video.HeyGenVideoProvider().timeout, 1800)


class EnvLoadingTests(unittest.TestCase):
    def test_heygen_and_video_vars_are_loaded_from_dotenv(self):
        from engine import envfile
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text("HEYGEN_API_KEY=a\nHEYGEN_TIMEOUT=9\nVIDEO_WORKERS=3\nHEYGEN_CLIP_PRICE_USD=1\nDEMO_VIDEO_DELAY=1\nOTHER=1\n", encoding="utf-8")
        env = {}
        envfile.load_dotenv(path, env, allowed=envfile.ENGINE_VARS)
        self.assertEqual(sorted(env), ["DEMO_VIDEO_DELAY", "HEYGEN_API_KEY", "HEYGEN_CLIP_PRICE_USD", "HEYGEN_TIMEOUT", "VIDEO_WORKERS"])


class DemoAndRegistryTests(Base):
    def test_demo_copies_the_clip(self):
        p = video.DemoVideoProvider(delay=0)
        self.assertTrue(p.available()[0])
        p.animate("p", self.src, self.dest, 5)
        self.assertEqual(self.dest.read_bytes()[4:8], b"ftyp")
        self.assertLess(self.dest.stat().st_size, 30_000)

    def test_demo_without_clip_is_unavailable(self):
        p = video.DemoVideoProvider(clip=self.dir / "none.mp4", delay=0)
        self.assertFalse(p.available()[0])
        with self.assertRaises(visuals.VisualsUnavailable):
            p.animate("p", self.src, self.dest, 5)

    def test_novideo(self):
        p = video.NoVideo()
        self.assertFalse(p.available()[0])
        with self.assertRaises(visuals.VisualsUnavailable):
            p.animate("p", self.src, self.dest, 5)

    def test_registry(self):
        self.assertIsInstance(visuals.get_provider("heygen", "video"), video.HeyGenVideoProvider)
        self.assertIsInstance(visuals.get_provider("demo", "video"), video.DemoVideoProvider)
        self.assertEqual(visuals.get_provider("heygen", "video").kind, "video")

    def test_durations(self):
        self.assertEqual(video.DURATIONS, (5, 10, 15))
        self.assertEqual(video.DEFAULT_DURATION, 5)


if __name__ == "__main__":
    unittest.main()
