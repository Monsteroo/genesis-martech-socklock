import http.client
import json
import threading
import unittest

from engine import visuals
from engine.service import Engine
from engine.web import make_server
from engine.web import MAX_BODY
from tests.helpers import SAMPLE_BLOCKS, FakeLLM, make_root, segments_answer


def variants_answer(n=5):
    return {"variants": [{"hook": "H", "first_line": "L", "onscreen": "O"} for _ in range(n)]}


class BlockingLLM(FakeLLM):
    """FakeLLM whose run() waits until released, to hold the server busy."""

    def __init__(self, *answers):
        super().__init__(*answers)
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, prompt):
        self.started.set()
        self.release.wait(5)
        return super().run(prompt)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(make_root(), FakeLLM(variants_answer(5)))
        self.engine.library.add("Socks", "lost", SAMPLE_BLOCKS)
        self.server = make_server(self.engine, port=0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        conn.close()
        return resp.status, text

    def test_index_contains_the_token_and_two_tabs(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, html)
        self.assertNotIn("__TOKEN__", html)
        self.assertIn("Оригінали", html)
        self.assertIn("Модифікації", html)

    def test_post_without_token_is_forbidden(self):
        status, _ = self.request("POST", "/api/variants", {"id": "o001", "instruction": "x", "count": 5})
        self.assertEqual(status, 403)

    def test_post_with_wrong_token_is_forbidden(self):
        status, _ = self.request("POST", "/api/variants", {}, {"X-Token": "nope"})
        self.assertEqual(status, 403)

    def test_foreign_host_header_is_forbidden(self):
        status, _ = self.request("GET", "/", host="evil.example")
        self.assertEqual(status, 403)

    def test_variants_with_token(self):
        status, text = self.request(
            "POST", "/api/variants", {"id": "o001", "instruction": "5 hooks", "count": 5},
            {"X-Token": self.server.token})
        self.assertEqual(status, 200)
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]), 5)

    def test_domain_error_is_a_400_with_message(self):
        status, text = self.request(
            "POST", "/api/variants", {"id": "o404", "instruction": "x", "count": 5},
            {"X-Token": self.server.token})
        self.assertEqual(status, 400)
        self.assertIn("не знайдено", json.loads(text)["error"])

    def test_originals_listing_and_status_filter(self):
        status, text = self.request("GET", "/api/originals?status=winner")
        self.assertEqual(json.loads(text)["data"], [])
        status, text = self.request("GET", "/api/originals?status=draft")
        self.assertEqual(len(json.loads(text)["data"]), 1)
        status, _ = self.request("GET", "/api/originals?status=zzz")
        self.assertEqual(status, 400)

    def test_status_change_via_api(self):
        status, text = self.request("POST", "/api/status", {"id": "o001", "status": "winner"},
                                    {"X-Token": self.server.token})
        self.assertEqual(json.loads(text)["data"]["status"], "winner")

    def test_unknown_route_and_bad_json(self):
        status, _ = self.request("POST", "/api/nope", {}, {"X-Token": self.server.token})
        self.assertEqual(status, 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        bad = b"{not json"
        conn.request("POST", "/api/pains", body=bad,
                     headers={"X-Token": self.server.token, "Content-Length": str(len(bad))})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    @property
    def auth(self):
        return {"X-Token": self.server.token}

    def post_ok(self, path, body):
        status, text = self.request("POST", path, body, self.auth)
        self.assertEqual(status, 200, text)
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        return payload["data"]

    def test_localhost_host_header_is_allowed(self):
        status, _ = self.request("GET", "/", host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_visuals_route_reports_unavailable(self):
        status, text = self.request("GET", "/api/visuals")
        self.assertEqual(status, 200)
        data = json.loads(text)["data"]
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], visuals.REASON)
        status, _ = self.request("GET", "/api/visuals", host="evil.example")
        self.assertEqual(status, 403)

    def test_unexpected_exception_is_a_500_and_releases_the_lock(self):
        status, text = self.request("POST", "/api/compose", {"pain": "p", "reuse": [], "new": ["HOOK"]}, self.auth)
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(text), {"ok": False, "error": "Внутрішня помилка сервера"})
        status, _ = self.request("POST", "/api/status", {"id": "o001", "status": "winner"}, self.auth)
        self.assertEqual(status, 200)

    def test_non_ascii_token_is_forbidden_not_a_crash(self):
        status, _ = self.request("POST", "/api/status", {}, {"X-Token": "t\u00e9st"})
        self.assertEqual(status, 403)

    def test_second_concurrent_post_gets_409_and_first_completes(self):
        llm = BlockingLLM(variants_answer(5))
        self.engine.llm = llm
        first = {}

        def go():
            first["result"] = self.request(
                "POST", "/api/variants", {"id": "o001", "instruction": "x", "count": 5}, self.auth)

        thread = threading.Thread(target=go)
        thread.start()
        self.assertTrue(llm.started.wait(5))
        status, _ = self.request("POST", "/api/variants", {"id": "o001", "instruction": "x", "count": 5}, self.auth)
        self.assertEqual(status, 409)
        llm.release.set()
        thread.join(5)
        self.assertEqual(first["result"][0], 200)

    def _raw_post(self, content_length, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/pains", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{self.port}")
        conn.putheader("X-Token", self.server.token)
        conn.putheader("Content-Length", content_length)
        conn.endheaders(body)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8")
        conn.close()
        return resp.status, text

    def test_oversized_body_is_413(self):
        status, text = self._raw_post(str(MAX_BODY + 1))
        self.assertEqual(status, 413)
        self.assertFalse(json.loads(text)["ok"])

    def test_non_numeric_content_length_is_400(self):
        status, text = self._raw_post("abc")
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(text)["ok"])

    def test_negative_content_length_is_400(self):
        status, text = self._raw_post("-5")
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(text)["ok"])

    def test_non_object_json_body_is_400(self):
        status, text = self.request("POST", "/api/pains", ["x"], self.auth)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(text)["error"], "Очікується JSON-об'єкт")

    def test_oserror_from_engine_is_400_and_lock_is_released(self):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        self.engine.variants = boom
        status, text = self.request("POST", "/api/variants", {"id": "o001", "instruction": "x"}, self.auth)
        self.assertEqual(status, 400)
        self.assertIn("disk full", json.loads(text)["error"])
        status, _ = self.request("POST", "/api/status", {"id": "o001", "status": "testing"}, self.auth)
        self.assertEqual(status, 200)

    def test_pains_route(self):
        text = ('Every single wash I lose one sock. Pairing socks after laundry is the worst chore. '
                'Tried sock clips and rings. They fall off in the wash and then I lose those too.')
        self.engine.llm = FakeLLM({"pains": [
            {"pain": "Lost socks", "quotes": ["Every single wash I lose one sock."]}]})
        data = self.post_ok("/api/pains", {"text": text})
        self.assertEqual(data["pains"][0]["pain"], "Lost socks")

    def test_concepts_route(self):
        self.engine.llm = FakeLLM({"concepts": [{"angle": f"A{i}", "hook": f"H{i}"} for i in range(5)]})
        data = self.post_ok("/api/concepts", {"pain": {"pain": "Lost socks", "quotes": ["q"]}})
        self.assertEqual(len(data["concepts"]), 5)

    def test_build_route(self):
        self.engine.llm = FakeLLM({
            "title": "T", "blocks": SAMPLE_BLOCKS,
            "shot_list": ["a", "b", "c", "d", "e"]})
        data = self.post_ok("/api/build", {
            "pain": {"pain": "Lost socks", "quotes": ["q"]},
            "concept": {"angle": "Mystery", "hook": "H"}, "status": "testing"})
        self.assertEqual(data["id"], "o002")
        self.assertEqual(data["status"], "testing")

    def test_localize_route(self):
        self.engine.llm = FakeLLM(segments_answer())
        data = self.post_ok("/api/localize", {"id": "o001", "markets": ["de"]})
        self.assertEqual(data["de"]["market"], "de")
        self.assertEqual(len(data["de"]["segments"]), 6)

    def test_compose_route(self):
        self.engine.llm = FakeLLM({"blocks": [{"type": "HOOK", "text": "New hook"},
                                              {"type": "PAIN", "text": "New pain"}]})
        data = self.post_ok("/api/compose", {
            "pain": "Lost socks",
            "reuse": {t: "o001" for t in ("DEMO", "PROOF", "ONSCREEN", "CTA")},
            "new": ["HOOK", "PAIN"]})
        self.assertEqual(data["reuse_share"], 0.67)
        self.assertEqual(len(data["blocks"]), 6)


if __name__ == "__main__":
    unittest.main()
