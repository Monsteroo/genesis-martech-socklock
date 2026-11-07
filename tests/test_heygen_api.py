import json
import os
import unittest
from unittest import mock

from engine import heygen_api
from engine.heygen_api import HeyGenError
from tests.helpers import FakeTransport, http_error, http_ok

KEY = "hg-test-0123456789abcdef"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40


class EnvCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"HEYGEN_API_KEY": KEY})
        patcher.start()
        self.addCleanup(patcher.stop)


class KeyTests(unittest.TestCase):
    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"HEYGEN_API_KEY": " "}):
            with self.assertRaises(HeyGenError) as ctx:
                heygen_api.api_key()
        self.assertIn("HEYGEN_API_KEY", str(ctx.exception))

    def test_bad_characters(self):
        with mock.patch.dict(os.environ, {"HEYGEN_API_KEY": "abc def"}):
            with self.assertRaises(HeyGenError):
                heygen_api.api_key()


class RequestTests(EnvCase):
    def test_request_shape_fixed_base_and_header(self):
        t = FakeTransport({"status": 202, "body": json.dumps({"data": {"video_id": "vid_1", "status": "pending"}}).encode()})
        job = heygen_api.create_job({"a": 1}, "idem-1", transport=t)
        self.assertEqual(job, "vid_1")
        req = t.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["url"], "https://api.heygen.com/v3/models/videos")
        self.assertEqual(req["headers"]["x-api-key"], KEY)
        self.assertEqual(req["headers"]["Idempotency-Key"], "idem-1")
        self.assertEqual(json.loads(req["body"]), {"a": 1})
        self.assertNotIn(KEY, req["url"])

    def test_no_retry_on_post_failure(self):
        t = FakeTransport(http_error(503, {"error": {"message": "later"}}), http_ok({}))
        with self.assertRaises(HeyGenError):
            heygen_api.create_job({}, "k", transport=t)
        self.assertEqual(len(t.requests), 1)

    def test_status_messages_and_redaction(self):
        for status in (400, 401, 402, 403, 404, 409, 429, 500):
            t = FakeTransport(http_error(status, {"error": {"message": f"bad {KEY} detail"}}))
            with self.assertRaises(HeyGenError) as ctx:
                heygen_api.create_job({}, "k", transport=t)
            self.assertIn(str(status), str(ctx.exception))
            self.assertNotIn(KEY, str(ctx.exception))

    def test_signed_url_query_is_redacted(self):
        text = heygen_api.redact("see https://resource2.heygen.ai/a.mp4?Signature=SECRET&x=1 now")
        self.assertNotIn("SECRET", text)

    def test_bad_video_id_in_response(self):
        t = FakeTransport({"status": 202, "body": json.dumps({"data": {"video_id": "../x"}}).encode()})
        with self.assertRaises(HeyGenError):
            heygen_api.create_job({}, "k", transport=t)

    def test_get_job_validates_id_before_request(self):
        t = FakeTransport()
        with self.assertRaises(HeyGenError):
            heygen_api.get_job("../etc", transport=t)
        self.assertEqual(t.requests, [])

    def test_get_job_returns_data(self):
        t = FakeTransport(http_ok({"data": {"status": "processing"}}))
        self.assertEqual(heygen_api.get_job("abc", transport=t)["status"], "processing")
        self.assertEqual(t.requests[0]["url"], "https://api.heygen.com/v3/models/videos/abc")
        self.assertEqual(t.requests[0]["method"], "GET")

    def test_upload_asset(self):
        t = FakeTransport(http_ok({"data": {"asset_id": "as_1", "url": "u", "mime_type": "image/png", "size_bytes": 3}}))
        self.assertEqual(heygen_api.upload_asset(b"\x89PNGdata", transport=t), "as_1")
        req = t.requests[0]
        self.assertEqual(req["url"], "https://api.heygen.com/v3/assets")
        self.assertTrue(req["headers"]["Content-Type"].startswith("multipart/form-data"))
        self.assertIn(b'name="file"', req["body"])


class DownloadTests(EnvCase):
    def test_host_rules(self):
        ok = ["https://resource2.heygen.ai/v/a.mp4?sig=1", "https://files.heygen.com/a.mp4"]
        bad = ["http://resource2.heygen.ai/a.mp4", "https://evilheygen.ai/a.mp4", "https://heygen.ai.evil.com/a.mp4",
               "https://user@resource2.heygen.ai/a.mp4", "https://resource2.heygen.ai:8443/a.mp4", "file:///etc/passwd", "", None]
        for url in ok:
            heygen_api.check_download_url(url)
        for url in bad:
            with self.assertRaises(HeyGenError, msg=str(url)):
                heygen_api.check_download_url(url)

    def test_download_has_no_api_key_and_returns_bytes(self):
        t = FakeTransport({"status": 200, "body": MP4})
        data = heygen_api.download("https://resource2.heygen.ai/a.mp4", transport=t)
        self.assertEqual(data, MP4)
        self.assertNotIn("x-api-key", {k.lower() for k in t.requests[0]["headers"]})
        self.assertEqual(t.requests[0]["max_bytes"], heygen_api.MAX_VIDEO_BYTES)

    def test_download_rejects_bad_status_and_non_mp4(self):
        with self.assertRaises(HeyGenError):
            heygen_api.download("https://resource2.heygen.ai/a.mp4", transport=FakeTransport({"status": 403, "body": b""}))
        with self.assertRaises(HeyGenError):
            heygen_api.download("https://resource2.heygen.ai/a.mp4", transport=FakeTransport({"status": 200, "body": b"<html>" * 10}))

    def test_download_to_bad_host_never_calls_transport(self):
        t = FakeTransport()
        with self.assertRaises(HeyGenError):
            heygen_api.download("https://evil.example/a.mp4", transport=t)
        self.assertEqual(t.requests, [])

    def test_size_cap_is_200_mb(self):
        self.assertEqual(heygen_api.MAX_VIDEO_BYTES, 200 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
