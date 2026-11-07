import http.client
import io
import json
import os
import threading
import unittest
import zipfile
from unittest import mock

from engine import web
from engine.llm import LLMError
from engine.service import Engine
from engine.web import make_server
from engine.web import MAX_BODY
from tests.helpers import PNG_MAGIC, FakeImages, FakeLLM, make_root


def raw_request(port, method, path, body=None, headers=None, host=None):
    status, payload, response_headers = raw_response(port, method, path, body, headers, host)
    return status, payload, response_headers.get("Content-Type")


def raw_response(port, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    resp = conn.getresponse()
    payload = resp.read()
    response_headers = {k: v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, payload, response_headers


class HttpMixin:
    def request(self, method, path, body=None, headers=None, host=None):
        status, data, _ = raw_request(self.port, method, path, body, headers, host)
        return status, data.decode("utf-8")

    def request_headers(self, method, path, headers=None, host=None):
        """Returns (status, bytes, response headers dict)."""
        return raw_response(self.port, method, path, None, headers, host)

    def request_raw(self, method, path, headers=None, host=None, body=None):
        return raw_request(self.port, method, path, body, headers, host)


class WebTests(HttpMixin, unittest.TestCase):
    def setUp(self):
        self.engine = Engine(make_root(), FakeLLM(), images=FakeImages())
        self.server = make_server(self.engine, port=0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def auth(self):
        return {"X-Token": self.server.token}

    def test_index_contains_the_token(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.server.token, html)
        self.assertNotIn("__TOKEN__", html)

    def test_post_without_token_is_forbidden(self):
        status, _ = self.request("POST", "/api/gen", {"prompt": "x"})
        self.assertEqual(status, 403)

    def test_post_with_wrong_token_is_forbidden(self):
        status, _ = self.request("POST", "/api/gen", {}, {"X-Token": "nope"})
        self.assertEqual(status, 403)

    def test_foreign_host_header_is_forbidden(self):
        status, _ = self.request("GET", "/", host="evil.example")
        self.assertEqual(status, 403)

    def test_domain_error_is_a_400_with_message(self):
        status, text = self.request("POST", "/api/modify", {"parent": "g0000abcd", "change": "x"}, self.auth)
        self.assertEqual(status, 400)
        self.assertIn("не знайдено", json.loads(text)["error"])

    def test_unknown_route_and_bad_json(self):
        status, _ = self.request("POST", "/api/nope", {}, self.auth)
        self.assertEqual(status, 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        bad = b"{not json"
        conn.request("POST", "/api/gen", body=bad,
                     headers={"X-Token": self.server.token, "Content-Length": str(len(bad))})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_localhost_host_header_is_allowed(self):
        status, _ = self.request("GET", "/", host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_visuals_route_reports_unavailable(self):
        self.engine.images = FakeImages(available=(False, "Немає ключа."))
        status, text = self.request("GET", "/api/visuals")
        self.assertEqual(status, 200)
        data = json.loads(text)["data"]
        self.assertFalse(data["available"])
        self.assertTrue(data["reason"])
        status, _ = self.request("GET", "/api/visuals", host="evil.example")
        self.assertEqual(status, 403)

    def test_visuals_route_reflects_the_provider(self):
        status, text = self.request("GET", "/api/visuals")
        self.assertEqual(json.loads(text)["data"], {"available": True, "reason": ""})

    def test_unexpected_exception_is_a_500_and_releases_the_slot(self):
        def boom(*args, **kwargs):
            raise RuntimeError("secret")

        self.engine.quick_image = boom
        status, text = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(text), {"ok": False, "error": "Внутрішня помилка сервера"})
        del self.engine.quick_image
        status, _ = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 200)

    def test_non_ascii_token_is_forbidden_not_a_crash(self):
        status, _ = self.request("POST", "/api/gen", {}, {"X-Token": "t\u00e9st"})
        self.assertEqual(status, 403)

    def _raw_post(self, content_length, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/gen", skip_host=True)
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
        status, text = self.request("POST", "/api/gen", ["x"], self.auth)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(text)["error"], "Очікується JSON-об'єкт")

    def test_oserror_from_engine_is_400_and_the_slot_is_released(self):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        self.engine.quick_image = boom
        status, text = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(text)["error"], "Помилка файлової системи")
        del self.engine.quick_image
        status, _ = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 200)

    def test_oserror_message_is_not_leaked(self):
        self.engine.images.fail = OSError("/secret/path/x")
        status, text = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 400)
        self.assertNotIn("/secret/path", text)
        self.assertEqual(json.loads(text)["error"], "Помилка файлової системи")

    def test_failing_provider_is_a_400_and_releases_the_slot(self):
        self.engine.images.fail = LLMError("boom")
        status, text = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 400)
        self.assertIn("boom", json.loads(text)["error"])
        self.engine.images.fail = None
        status, _ = self.request("POST", "/api/gen", {"prompt": "x"}, self.auth)
        self.assertEqual(status, 200)


class QuickWebTests(HttpMixin, unittest.TestCase):
    def setUp(self):
        self.fake = FakeImages()
        self.engine = Engine(make_root(), FakeLLM(), images=self.fake)
        self.server = make_server(self.engine, port=0, image_workers=2)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.auth = {"X-Token": self.server.token}

    def gen(self, body):
        status, text = self.request("POST", "/api/gen", body, self.auth)
        return status, json.loads(text)

    def test_root_is_the_simple_page(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Генератор зображень", html)
        self.assertIn(self.server.token, html)
        self.assertNotIn("__TOKEN__", html)

    def test_gen_without_a_token_is_forbidden(self):
        status, _ = self.request("POST", "/api/gen", {"prompt": "Socks"})
        self.assertEqual(status, 403)

    def test_gen_then_png_list_zip_and_delete(self):
        status, data = self.gen({"prompt": "Socks on a floor", "format": "square", "style": "product"})
        self.assertEqual(status, 200)
        meta = data["data"]
        self.assertRegex(meta["id"], r"^g[0-9a-f]{8}$")
        self.assertEqual(meta["size"], "1024x1024")

        status, body, ctype = self.request_raw("GET", f"/api/g/{meta['id']}.png", headers=self.auth)
        self.assertEqual((status, ctype), (200, "image/png"))
        self.assertTrue(body.startswith(PNG_MAGIC))
        status, _, _ = self.request_raw("GET", f"/api/g/{meta['id']}.png")
        self.assertEqual(status, 403)

        status, text = self.request("GET", "/api/gallery")
        self.assertEqual([m["id"] for m in json.loads(text)["data"]["items"]], [meta["id"]])

        status, body, ctype = self.request_raw("GET", f"/api/gallery/zip?ids={meta['id']}", headers=self.auth)
        self.assertEqual((status, ctype), (200, "application/zip"))
        self.assertTrue(body.startswith(b"PK"))
        status, _, _ = self.request_raw("GET", f"/api/gallery/zip?ids={meta['id']}")
        self.assertEqual(status, 403)

        status, text = self.request("POST", "/api/gallery/delete", {"ids": [meta["id"]]}, self.auth)
        self.assertEqual(json.loads(text)["data"], {"deleted": 1})
        status, _, _ = self.request_raw("GET", f"/api/g/{meta['id']}.png", headers=self.auth)
        self.assertEqual(status, 404)

    def test_validation_errors_are_400_with_a_message(self):
        for body in ({"prompt": ""}, {"prompt": "x", "format": "huge"}):
            status, data = self.gen(body)
            self.assertEqual(status, 400, body)
            self.assertTrue(data["error"])
        self.assertEqual(self.fake.prompts, [])

    def test_bad_png_paths_are_not_served(self):
        for path in ("/api/g/g0000abcd.png", "/api/g/..%2Fx.png", "/api/g/gZZZZZZZZ.png"):
            status, _, _ = self.request_raw("GET", path, headers=self.auth)
            self.assertEqual(status, 404, path)

    def test_gallery_zip_without_ids_is_400(self):
        status, _, _ = self.request_raw("GET", "/api/gallery/zip?ids=", headers=self.auth)
        self.assertEqual(status, 400)

    def test_gen_is_an_image_route_and_delete_needs_no_lock(self):
        self.assertEqual(web.IMAGE_ROUTES, {"/api/gen", "/api/modify", "/api/localize"})
        self.assertNotIn("/api/gallery/delete", web.IMAGE_ROUTES)

    def test_provider_errors_do_not_leak_and_release_the_slot(self):
        engine = Engine(make_root(), FakeLLM(), images=FakeImages(fail=LLMError("boom")))
        server = make_server(engine, port=0, image_workers=1)
        self.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for _ in range(2):  # a second call proves the slot was released
            status, text = self.request("POST", "/api/gen", {"prompt": "Socks"}, {"X-Token": server.token})
            self.assertEqual(status, 400)
            self.assertIn("boom", json.loads(text)["error"])


class QuickWebSemanticsTests(HttpMixin, unittest.TestCase):
    def make(self, images=None, llm=None, workers=2):
        self.fake = images or FakeImages()
        self.engine = Engine(make_root(), llm or FakeLLM(), images=self.fake)
        self.server = make_server(self.engine, port=0, image_workers=workers)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.auth = {"X-Token": self.server.token}

    def gen(self, body):
        status, text = self.request("POST", "/api/gen", body, self.auth)
        return status, json.loads(text)

    def blocking_images(self):
        outer = self
        outer.release, outer.entered = threading.Event(), threading.Semaphore(0)

        class Blocking(FakeImages):
            def generate(self, prompt, dest, size=None):
                if prompt.startswith("block"):
                    outer.entered.release()
                    outer.release.wait(5)
                return super().generate(prompt, dest, size)

        return Blocking()

    def test_png_etag_304_and_delete_beats_the_cache(self):
        self.make()
        _, data = self.gen({"prompt": "Socks"})
        gid = data["data"]["id"]
        path = f"/api/g/{gid}.png"
        status, _, headers = self.request_headers("GET", path, self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        etag = headers["ETag"]
        cond = {**self.auth, "If-None-Match": etag}
        status, body, _ = self.request_headers("GET", path, cond)
        self.assertEqual((status, body), (304, b""))
        status, _, _ = self.request_headers("GET", path, {"If-None-Match": etag})
        self.assertEqual(status, 403)
        self.request("POST", "/api/gallery/delete", {"ids": [gid]}, self.auth)
        status, _, _ = self.request_headers("GET", path, cond)
        self.assertEqual(status, 404)

    def test_delete_works_while_all_image_slots_are_busy(self):
        self.make(self.blocking_images(), workers=1)
        _, data = self.gen({"prompt": "Socks"})
        worker = threading.Thread(target=self.gen, args=({"prompt": "block"},))
        worker.start()
        self.assertTrue(self.entered.acquire(timeout=5))
        status, text = self.request("POST", "/api/gallery/delete", {"ids": [data["data"]["id"]]}, self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["data"], {"deleted": 1})
        self.release.set()
        worker.join(10)

    def test_gen_runs_in_parallel_up_to_the_worker_count(self):
        self.make(self.blocking_images(), workers=2)
        threads = [threading.Thread(target=self.gen, args=({"prompt": f"block {n}"},)) for n in range(3)]
        [t.start() for t in threads]
        self.assertTrue(self.entered.acquire(timeout=5))
        self.assertTrue(self.entered.acquire(timeout=5))
        self.assertFalse(self.entered.acquire(timeout=0.3))  # the third waits for a slot
        self.release.set()
        [t.join(10) for t in threads]

    def test_response_headers(self):
        self.make()
        _, data = self.gen({"prompt": "Socks"})
        gid = data["data"]["id"]
        _, _, headers = self.request_headers("GET", f"/api/g/{gid}.png", self.auth)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        _, _, headers = self.request_headers("GET", f"/api/gallery/zip?ids={gid}", self.auth)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="images.zip"')
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        _, _, headers = self.request_headers("GET", "/")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_zip_validation_and_dedup(self):
        self.make()
        _, data = self.gen({"prompt": "Socks"})
        gid = data["data"]["id"]
        many = ",".join(f"g{i:08x}" for i in range(51))
        for ids in (many, "bad!"):
            status, _, _ = self.request_headers("GET", f"/api/gallery/zip?ids={ids}", self.auth)
            self.assertEqual(status, 400, ids)
        status, body, _ = self.request_headers("GET", f"/api/gallery/zip?ids={gid},{gid}", self.auth)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(archive.namelist(), [f"{gid}.png"])
        status, _, _ = self.request_headers("GET", "/api/gallery/zip?ids=g0000abcd")
        self.assertEqual(status, 403)

    def test_get_errors_are_json_without_a_traceback(self):
        self.make()
        for exc, status, message in ((OSError("/secret/x"), 400, "Помилка файлової системи"),
                                     (RuntimeError("secret"), 500, "Внутрішня помилка сервера")):
            def boom(*args, exc=exc, **kwargs):
                raise exc
            self.engine.list_gallery = boom
            code, text = self.request("GET", "/api/gallery")
            self.assertEqual(code, status)
            self.assertEqual(json.loads(text), {"ok": False, "error": message})
            self.assertNotIn("secret", text)

    def test_malformed_bodies_get_a_generic_400(self):
        self.make()
        status, text = self.request("POST", "/api/gen", {"prompt": "x", "format": ["x"]}, self.auth)
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(text)["error"])
        status, text = self.request("POST", "/api/gallery/delete", {}, self.auth)  # KeyError
        self.assertEqual((status, json.loads(text)["error"]), (400, "Некоректний запит."))
        status, text = self.request("POST", "/api/gallery/delete", {"ids": 5}, self.auth)
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(text)["error"])


class ParallelImageTests(HttpMixin, unittest.TestCase):
    def make(self, images, workers=2):
        self.engine = Engine(make_root(), FakeLLM(), images=images)
        self.server = make_server(self.engine, port=0, image_workers=workers)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.auth = {"X-Token": self.server.token}

    def post_gen(self, n, results):
        status, text = self.request("POST", "/api/gen", {"prompt": f"p{n}"}, self.auth)
        results[n] = (status, json.loads(text))

    def test_images_run_in_parallel_up_to_the_worker_limit(self):
        state = {"active": 0, "max": 0}
        lock = threading.Lock()
        release = threading.Event()
        entered = threading.Semaphore(0)

        class BlockingImages(FakeImages):
            def generate(self, prompt, dest, size=None):
                with lock:
                    state["active"] += 1
                    state["max"] = max(state["max"], state["active"])
                entered.release()
                release.wait(5)
                with lock:
                    state["active"] -= 1
                return super().generate(prompt, dest, size)

        self.make(BlockingImages(), workers=2)
        results = {}
        threads = [threading.Thread(target=self.post_gen, args=(n, results)) for n in (1, 2, 3)]
        [t.start() for t in threads]
        self.assertTrue(entered.acquire(timeout=5))
        self.assertTrue(entered.acquire(timeout=5))
        self.assertFalse(entered.acquire(timeout=0.3))  # the third waits for a free slot
        release.set()
        [t.join(10) for t in threads]
        self.assertEqual(state["max"], 2)
        self.assertEqual({n: r[0] for n, r in results.items()}, {1: 200, 2: 200, 3: 200})

    def test_a_full_queue_times_out_with_409(self):
        release, entered = threading.Event(), threading.Event()

        class BlockingImages(FakeImages):
            def generate(self, prompt, dest, size=None):
                entered.set()
                release.wait(5)
                return super().generate(prompt, dest, size)

        self.make(BlockingImages(), workers=1)
        results = {}
        first = threading.Thread(target=self.post_gen, args=(1, results))
        first.start()
        self.assertTrue(entered.wait(5))
        with mock.patch("engine.web.IMAGE_QUEUE_TIMEOUT", 0.2):
            self.post_gen(2, results)
        self.assertEqual(results[2][0], 409)
        self.assertIn("слоти", results[2][1]["error"])
        release.set()
        first.join(10)

    def test_a_full_queue_also_times_out_modify_with_409(self):
        release, entered = threading.Event(), threading.Event()

        class BlockingEdits(FakeImages):
            def edit(self, prompt, src, dest, size=None):
                entered.set()
                release.wait(5)
                return super().edit(prompt, src, dest, size)

        self.make(BlockingEdits(), workers=1)
        oid = json.loads(self.request("POST", "/api/gen", {"prompt": "Socks"}, self.auth)[1])["data"]["id"]
        body = {"parent": oid, "change": "x", "variation": 0}
        first = threading.Thread(target=self.request, args=("POST", "/api/modify", body, self.auth))
        first.start()
        self.assertTrue(entered.wait(5))
        with mock.patch("engine.web.IMAGE_QUEUE_TIMEOUT", 0.2):
            status, text = self.request("POST", "/api/modify", body, self.auth)
        self.assertEqual(status, 409)
        self.assertIn("слоти", json.loads(text)["error"])
        release.set()
        first.join(10)

    def test_config_route_has_no_secrets(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-0123456789", "OPENAI_IMAGE_PRICE_USD": "0.04"}):
            self.make(FakeImages(), workers=3)
            status, text = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        data = json.loads(text)["data"]
        self.assertEqual(data["workers"], 3)
        self.assertEqual(data["price_usd"], 0.04)
        self.assertNotIn("sk-secret", text)

    def test_workers_from_env(self):
        for value, expected in (("6", 6), ("0", 1), ("99", 8), ("junk", 4), ("", 4)):
            with mock.patch.dict(os.environ, {"IMAGE_WORKERS": value}):
                self.assertEqual(web.image_workers_from_env(), expected)


class ModifyWebTests(HttpMixin, unittest.TestCase):
    def setUp(self):
        self.fake = FakeImages()
        self.engine = Engine(make_root(), FakeLLM(), images=self.fake)
        self.server = make_server(self.engine, port=0, image_workers=2)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.auth = {"X-Token": self.server.token}

    def post(self, path, body):
        status, text = self.request("POST", path, body, self.auth)
        return status, json.loads(text)

    def test_modify_over_http_and_gallery_filters(self):
        _, orig = self.post("/api/gen", {"prompt": "Socks", "format": "square"})
        oid = orig["data"]["id"]
        self.assertEqual(orig["data"]["kind"], "original")
        status, mod = self.post("/api/modify", {"parent": oid, "change": "evening light", "variation": 0})
        self.assertEqual(status, 200)
        self.assertEqual((mod["data"]["kind"], mod["data"]["parent"]), ("modification", oid))
        status, text = self.request("GET", "/api/gallery?kind=modification&parent=" + oid)
        self.assertEqual([m["id"] for m in json.loads(text)["data"]["items"]], [mod["data"]["id"]])
        status, text = self.request("GET", "/api/gallery?kind=original")
        self.assertEqual([m["id"] for m in json.loads(text)["data"]["items"]], [oid])

    def test_modify_a_modification_and_filter_by_root(self):
        _, orig = self.post("/api/gen", {"prompt": "Socks", "format": "square"})
        oid = orig["data"]["id"]
        _, m1 = self.post("/api/modify", {"parent": oid, "change": "evening light"})
        _, m2 = self.post("/api/modify", {"parent": m1["data"]["id"], "change": "bigger"})
        self.assertEqual((m2["data"]["parent"], m2["data"]["root"]), (m1["data"]["id"], oid))
        status, text = self.request("GET", "/api/gallery?root=" + oid)
        self.assertEqual(sorted(m["id"] for m in json.loads(text)["data"]["items"]),
                         sorted([m1["data"]["id"], m2["data"]["id"]]))
        status, _ = self.request("GET", "/api/gallery?root=../x")
        self.assertEqual(status, 400)

    def test_localize_over_http_and_config_markets(self):
        _, orig = self.post("/api/gen", {"prompt": "Socks", "format": "square"})
        oid = orig["data"]["id"]
        status, loc = self.post("/api/localize", {"parent": oid, "market": "ca"})
        self.assertEqual((status, loc["data"]["market"], loc["data"]["parent"]), (200, "ca", oid))
        for body in ({"parent": oid, "market": "xx"}, {"parent": "../x", "market": "us"}, {"parent": oid}):
            status, _ = self.post("/api/localize", body)
            self.assertEqual(status, 400, body)
        status, _ = self.request("POST", "/api/localize", {"parent": oid, "market": "us"})
        self.assertEqual(status, 403)
        _, cfg = self.request("GET", "/api/config")
        markets = json.loads(cfg)["data"]["markets"]
        self.assertEqual([m["key"] for m in markets], ["us", "uk", "ca", "au", "de", "fr", "es"])
        self.assertEqual(sorted(markets[0]), ["key", "lang", "name", "name_uk", "status"])

    def test_modify_accepts_a_format_and_rejects_a_bad_one(self):
        _, orig = self.post("/api/gen", {"prompt": "Socks", "format": "wide"})
        oid = orig["data"]["id"]
        status, mod = self.post("/api/modify", {"parent": oid, "change": "x", "format": "vertical"})
        self.assertEqual((status, mod["data"]["size"]), (200, "1024x1536"))
        self.assertEqual(self.fake.edits[-1][2], "1024x1536")
        status, mod = self.post("/api/modify", {"parent": oid, "change": "x"})
        self.assertEqual(mod["data"]["size"], "1536x1024")
        count = len(self.fake.edits)
        status, _ = self.post("/api/modify", {"parent": oid, "change": "x", "format": "huge"})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.fake.edits), count)

    def test_modify_needs_a_token_and_validates(self):
        status, _ = self.request("POST", "/api/modify", {"parent": "g0000abcd"})
        self.assertEqual(status, 403)
        for body in ({"parent": "g0000abcd"}, {"parent": "../x"}, {}):
            status, data = self.post("/api/modify", body)
            self.assertEqual(status, 400, body)
        for qs in ("kind=bogus", "parent=../x"):
            status, _ = self.request("GET", "/api/gallery?" + qs)
            self.assertEqual(status, 400, qs)
        self.assertEqual(self.fake.edits, [])

    def test_gen_ignores_parent_fields_and_modify_is_an_image_route(self):
        status, data = self.post("/api/gen", {"prompt": "Socks", "parent": "g0000abcd", "variation": 1})
        self.assertEqual(status, 200)
        self.assertIsNone(data["data"]["parent"])
        self.assertEqual(web.IMAGE_ROUTES, {"/api/gen", "/api/modify", "/api/localize"})

    def test_removed_routes_and_pages_are_gone(self):
        for path in ("/workflow", "/api/originals", "/api/images?id=o001", "/api/image/o001/shot-1.png"):
            status, _ = self.request("GET", path, None, self.auth)
            self.assertEqual(status, 404, path)
        for path in ("/api/pains", "/api/concepts", "/api/build", "/api/status", "/api/variants",
                     "/api/compose", "/api/image"):
            status, _ = self.request("POST", path, {}, self.auth)
            self.assertEqual(status, 404, path)

    def test_modify_runs_in_parallel_up_to_the_worker_count(self):
        both = threading.Barrier(2, timeout=5)

        class ParallelEdits(FakeImages):
            def edit(self, prompt, src, dest, size=None):
                both.wait()  # passes only if two edits are inside at the same time
                return super().edit(prompt, src, dest, size)

        engine = Engine(make_root(), FakeLLM(), images=ParallelEdits())
        server = make_server(engine, port=0, image_workers=2)
        self.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        auth = {"X-Token": server.token}
        oid = json.loads(self.request("POST", "/api/gen", {"prompt": "Socks"}, auth)[1])["data"]["id"]
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            self.request("POST", "/api/modify", {"parent": oid, "change": "x", "variation": 0}, auth)[0])) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(results, [200, 200])


if __name__ == "__main__":
    unittest.main()
