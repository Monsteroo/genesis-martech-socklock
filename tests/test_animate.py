import http.client
import json
import threading
import unittest
from unittest import mock

from engine.library import LibraryError
from engine.llm import LLMError
from engine.service import Engine
from engine.web import make_server
from tests.helpers import MP4_BYTES, FakeImages, FakeLLM, FakeVideo, make_root
from tests.test_web import HttpMixin

TEST_HG = "hg-secret-key-123456"


def engine_with_image(video=None, **kw):
    e = Engine(make_root(), FakeLLM(), images=FakeImages(), video=video or FakeVideo())
    img = e.quick_image("socks on a floor", kw.get("format", "vertical"))
    return e, img


class AnimateServiceTests(unittest.TestCase):
    def test_animate_stores_clip_and_meta(self):
        v = FakeVideo()
        e, img = engine_with_image(v)
        meta = e.animate(img["id"], "повільний наїзд", 10)
        self.assertRegex(meta["id"], r"v[0-9a-f]{8}")
        self.assertEqual((meta["parent"], meta["root"], meta["duration"], meta["aspect"]), (img["id"], img["id"], 10, "9:16"))
        self.assertEqual(meta["motion"], "повільний наїзд")
        self.assertEqual(meta["provider"], "fake")
        self.assertEqual(meta["kind"], "video")
        self.assertEqual(e.videos.path(meta["id"]).read_bytes(), MP4_BYTES)
        self.assertEqual(meta["bytes"], len(MP4_BYTES))
        prompt, src, duration = v.calls[0]
        self.assertIn("push-in", prompt)
        self.assertEqual(duration, 10)
        self.assertEqual(src, str(e.gallery.path(img["id"])))
        self.assertEqual([m["id"] for m in e.list_videos()["items"]], [meta["id"]])

    def test_modification_chain_gets_its_root(self):
        e, img = engine_with_image()
        mod = e.modify_image(img["id"], "інше світло")
        meta = e.animate(mod["id"], "", 5)
        self.assertEqual((meta["parent"], meta["root"]), (mod["id"], img["id"]))

    def test_defaults_and_format(self):
        e, img = engine_with_image()
        self.assertEqual(e.animate(img["id"])["duration"], 5)
        self.assertEqual(e.animate(img["id"], "x", 5, "vertical")["aspect"], "9:16")
        with self.assertRaises(LibraryError):
            e.animate(img["id"], "x", 5, "wide")      # the clip follows the frame: no reshaping
        with self.assertRaises(LibraryError):
            e.animate(img["id"], "x", 5, "bogus")

    def test_square_and_wide_aspects(self):
        e, img = engine_with_image(format="square")
        self.assertEqual(e.animate(img["id"])["aspect"], "1:1")
        e, img = engine_with_image(format="wide")
        self.assertEqual(e.animate(img["id"])["aspect"], "16:9")

    def test_validation(self):
        v = FakeVideo()
        e, img = engine_with_image(v)
        bad = [dict(parent=None), dict(parent="../x"), dict(parent="g00000000"), dict(parent=img["id"], motion="x" * 601),
               dict(parent=img["id"], motion=5), dict(parent=img["id"], duration=7), dict(parent=img["id"], duration="5"),
               dict(parent=img["id"], duration=True), dict(parent=img["id"], duration=None)]
        for kw in bad:
            with self.assertRaises(LibraryError, msg=str(kw)):
                e.animate(**kw)
        self.assertEqual(v.calls, [])
        self.assertEqual(e.list_videos()["items"], [])

    def test_trashed_parent_is_refused(self):
        e, img = engine_with_image()
        e.trash_gallery([img["id"]])
        with self.assertRaises(LibraryError):
            e.animate(img["id"], "x", 5)

    def test_provider_failure_leaves_nothing(self):
        e, img = engine_with_image(FakeVideo(fail=LLMError("boom")))
        with self.assertRaises(LLMError):
            e.animate(img["id"], "x", 5)
        self.assertEqual(list(e.videos.root.glob("*")) if e.videos.root.exists() else [], [])

    def test_trash_restore_delete(self):
        e, img = engine_with_image()
        vid = e.animate(img["id"])["id"]
        self.assertEqual(e.trash_videos([vid]), {"trashed": 1})
        self.assertEqual(e.list_videos()["items"], [])
        self.assertEqual(len(e.list_videos(trashed=True)["items"]), 1)
        self.assertEqual(e.restore_videos([vid]), {"restored": 1})
        self.assertEqual(e.delete_videos([vid]), {"deleted": 1})
        self.assertEqual(e.list_videos(trashed=True)["items"], [])

    def test_info(self):
        e, _ = engine_with_image(FakeVideo(available=(False, "no key")))
        info = e.video_info()
        self.assertFalse(info["available"])
        self.assertEqual(info["durations"], [5, 10, 15])
        self.assertEqual(info["provider"], "fake")

    def test_default_engine_has_no_video(self):
        e = Engine(make_root(), FakeLLM())
        self.assertFalse(e.video.available()[0])


class AnimateWebTests(HttpMixin, unittest.TestCase):
    def setUp(self):
        self.video = FakeVideo()
        self.engine = Engine(make_root(), FakeLLM(), images=FakeImages(), video=self.video)
        self.server = make_server(self.engine, port=0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.h = {"X-Token": self.server.token}
        self.img = self.engine.quick_image("socks")["id"]

    def post(self, path, body):
        status, text = self.request("POST", path, body, self.h)
        return status, json.loads(text)

    def make_clip(self, duration=5):
        status, body = self.post("/api/animate", {"parent": self.img, "motion": "паралакс", "duration": duration})
        self.assertEqual(status, 200, body)
        return body["data"]

    def test_animate_needs_token_and_host(self):
        self.assertEqual(self.request("POST", "/api/animate", {"parent": self.img})[0], 403)
        self.assertEqual(self.request("POST", "/api/animate", {"parent": self.img}, self.h, host="evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/videos")[0], 403)
        self.assertEqual(self.request("GET", "/api/videos", headers=self.h, host="evil.example")[0], 403)

    def test_animate_list_and_serve(self):
        meta = self.make_clip()
        status, text = self.request("GET", "/api/videos", headers=self.h)
        self.assertEqual([m["id"] for m in json.loads(text)["data"]["items"]], [meta["id"]])
        status, data, headers = self.request_headers("GET", f"/api/v/{meta['id']}.mp4", self.h)
        self.assertEqual(status, 200)
        self.assertEqual(data, MP4_BYTES)
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        etag = headers["ETag"]
        status, data, _ = self.request_headers("GET", f"/api/v/{meta['id']}.mp4", {**self.h, "If-None-Match": etag})
        self.assertEqual((status, data), (304, b""))

    def test_range(self):
        meta = self.make_clip()
        url = f"/api/v/{meta['id']}.mp4"
        status, data, headers = self.request_headers("GET", url, {**self.h, "Range": "bytes=4-7"})
        self.assertEqual((status, data), (206, MP4_BYTES[4:8]))
        self.assertEqual(headers["Content-Range"], f"bytes 4-7/{len(MP4_BYTES)}")
        status, data, _ = self.request_headers("GET", url, {**self.h, "Range": "bytes=-4"})
        self.assertEqual((status, data), (206, MP4_BYTES[-4:]))
        status, data, headers = self.request_headers("GET", url, {**self.h, "Range": "bytes=999-"})
        self.assertEqual(status, 416)
        status, data, _ = self.request_headers("GET", url, {**self.h, "Range": "bytes=0-1,3-4"})
        self.assertEqual((status, data), (200, MP4_BYTES))

    def test_serve_guards(self):
        meta = self.make_clip()
        self.assertEqual(self.request("GET", f"/api/v/{meta['id']}.mp4")[0], 403)
        for bad in ("/api/v/../x.mp4", "/api/v/v1.mp4", "/api/v/g12345678.mp4", "/api/v/v00000000.mp4", "/api/v/%2e%2e.mp4"):
            self.assertIn(self.request("GET", bad, headers=self.h)[0], (403, 404), bad)

    def test_list_filters(self):
        meta = self.make_clip()
        self.post("/api/videos/trash", {"ids": [meta["id"]]})
        self.assertEqual(json.loads(self.request("GET", "/api/videos", headers=self.h)[1])["data"]["items"], [])
        got = json.loads(self.request("GET", "/api/videos?trashed=1", headers=self.h)[1])["data"]["items"]
        self.assertEqual(len(got), 1)
        self.assertEqual(self.request("GET", "/api/videos?trashed=2", headers=self.h)[0], 400)
        self.assertEqual(self.request("GET", "/api/videos?parent=bad", headers=self.h)[0], 400)
        self.assertEqual(self.request("GET", f"/api/videos?trashed=1&parent={self.img}", headers=self.h)[0], 200)

    def test_trash_restore_delete_routes(self):
        meta = self.make_clip()
        self.assertEqual(self.post("/api/videos/trash", {"ids": [meta["id"]]})[1]["data"], {"trashed": 1})
        self.assertEqual(self.post("/api/videos/restore", {"ids": [meta["id"]]})[1]["data"], {"restored": 1})
        self.assertEqual(self.post("/api/videos/delete", {"ids": [meta["id"]]})[1]["data"], {"deleted": 1})
        self.assertEqual(self.request("GET", f"/api/v/{meta['id']}.mp4", headers=self.h)[0], 404)
        self.assertEqual(self.post("/api/videos/trash", {"ids": ["../x"]})[0], 400)
        self.assertEqual(self.post("/api/videos/trash", {})[0], 400)

    def test_errors_are_generic(self):
        status, body = self.post("/api/animate", {"parent": "bad"})
        self.assertEqual(status, 400)
        self.video.fail = OSError("/secret/path")
        status, body = self.post("/api/animate", {"parent": self.img})
        self.assertEqual(status, 400)
        self.assertNotIn("secret", json.dumps(body))
        self.video.fail = RuntimeError("internal")
        status, body = self.post("/api/animate", {"parent": self.img})
        self.assertEqual(status, 500)
        self.video.fail = None
        self.assertEqual(self.post("/api/animate", {"parent": self.img})[0], 200)   # the slot was released

    def test_config_has_video_block(self):
        data = json.loads(self.request("GET", "/api/config", headers=self.h)[1])["data"]
        self.assertEqual(data["video"]["available"], True)
        self.assertEqual(data["video"]["durations"], [5, 10, 15])
        self.assertEqual(data["video"]["provider"], "fake")
        self.assertIsNone(data["video"]["price_usd"])
        self.assertEqual(data["video"]["workers"], 2)
        with mock.patch.dict("os.environ", {"HEYGEN_CLIP_PRICE_USD": "0.75", "HEYGEN_API_KEY": TEST_HG}):
            data = json.loads(self.request("GET", "/api/config", headers=self.h)[1])["data"]
        self.assertEqual(data["video"]["price_usd"], 0.75)
        self.assertNotIn(TEST_HG, json.dumps(data))


class VideoWorkersTests(HttpMixin, unittest.TestCase):
    def serve(self, video, workers=None):
        self.engine = Engine(make_root(), FakeLLM(), images=FakeImages(), video=video)
        self.server = make_server(self.engine, port=0, video_workers=workers)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.h = {"X-Token": self.server.token}
        return self.engine.quick_image("socks")["id"]

    def test_workers_from_env_are_clamped(self):
        from engine import web
        for raw, want in (("", 2), ("1", 1), ("9", 4), ("0", 1), ("abc", 2)):
            with mock.patch.dict("os.environ", {"VIDEO_WORKERS": raw}):
                self.assertEqual(web.video_workers_from_env(), want)

    def test_video_slots_are_separate_from_image_slots_and_time_out(self):
        gate, entered = threading.Event(), threading.Event()

        class Blocking(FakeVideo):
            def animate(self, *a, **k):
                entered.set()
                gate.wait(5)
                return super().animate(*a, **k)

        img = self.serve(Blocking(), workers=1)
        from engine import web
        results = []
        t = threading.Thread(target=lambda: results.append(self.request("POST", "/api/animate", {"parent": img}, self.h)[0]))
        t.start()
        self.assertTrue(entered.wait(5))
        # an image generation is not blocked by a busy video slot
        self.assertEqual(self.request("POST", "/api/gen", {"prompt": "x"}, self.h)[0], 200)
        with mock.patch.object(web, "VIDEO_QUEUE_TIMEOUT", 0.2):
            self.assertEqual(self.request("POST", "/api/animate", {"parent": img}, self.h)[0], 409)
        gate.set()
        t.join(5)
        self.assertEqual(results, [200])


if __name__ == "__main__":
    unittest.main()
