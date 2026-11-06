import http.client
import json
import threading
import unittest

from engine.service import Engine
from engine.web import make_server
from tests.helpers import SAMPLE_BLOCKS, FakeLLM, make_root


def variants_answer(n=5):
    return {"variants": [{"hook": "H", "first_line": "L", "onscreen": "O"} for _ in range(n)]}


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
        conn.request("POST", "/api/pains", body=b"{not json",
                     headers={"X-Token": self.server.token, "Content-Length": "9"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()


if __name__ == "__main__":
    unittest.main()
