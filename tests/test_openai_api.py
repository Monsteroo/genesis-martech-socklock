import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from engine import llm, openai_api
from engine.openai_api import OpenAIBackend, OpenAIError
from tests.helpers import TEST_KEY, FakeTransport, http_error, http_ok


class EnvCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OPENAI_API_KEY": TEST_KEY, "OPENAI_TEXT_MODEL": "text-model-x"})
        patcher.start()
        self.addCleanup(patcher.stop)


class RequestJsonTests(EnvCase):
    def test_request_shape_and_fixed_base_url(self):
        t = FakeTransport(http_ok({"ok": True}))
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "https://evil.example/v1"}):
            data = openai_api.request_json("POST", "/chat/completions", {"a": 1}, transport=t, timeout=7)
        self.assertEqual(data, {"ok": True})
        req = t.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(req["headers"]["Authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(req["headers"]["User-Agent"], "socklock-creative-engine")
        self.assertEqual(json.loads(req["body"]), {"a": 1})
        self.assertEqual(req["timeout"], 7)

    def test_get_has_no_body(self):
        t = FakeTransport(http_ok({}))
        openai_api.request_json("GET", "/models", transport=t)
        self.assertIsNone(t.requests[0]["body"])

    def test_missing_key_never_calls_the_transport(self):
        t = FakeTransport()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "  "}):
            with self.assertRaises(OpenAIError) as ctx:
                openai_api.request_json("GET", "/models", transport=t)
        self.assertIn(".env", str(ctx.exception))
        self.assertEqual(t.requests, [])

    def test_status_messages_include_the_api_detail(self):
        for status in (400, 401, 403, 404, 429, 500):
            t = FakeTransport(http_error(status, {"error": {"message": "detail here"}}))
            with self.assertRaises(OpenAIError) as ctx:
                openai_api.request_json("GET", "/models", transport=t)
            self.assertIn(str(status), str(ctx.exception))
            self.assertIn("detail here", str(ctx.exception))

    def test_key_is_redacted_from_api_messages(self):
        message = f"Incorrect API key provided: {TEST_KEY} and also sk-abcdefgh12345678"
        t = FakeTransport(http_error(401, {"error": {"message": message}}))
        with self.assertRaises(OpenAIError) as ctx:
            openai_api.request_json("GET", "/models", transport=t)
        text = str(ctx.exception)
        self.assertNotIn(TEST_KEY, text)
        self.assertNotIn("sk-abcdefgh12345678", text)

    def test_detail_is_truncated(self):
        t = FakeTransport(http_error(400, {"error": {"message": "x" * 1000}}))
        with self.assertRaises(OpenAIError) as ctx:
            openai_api.request_json("GET", "/models", transport=t)
        self.assertLess(len(str(ctx.exception)), 500)

    def test_non_json_200_is_an_error(self):
        t = FakeTransport({"status": 200, "body": b"<html>"})
        with self.assertRaises(OpenAIError):
            openai_api.request_json("GET", "/models", transport=t)

    def test_transport_errors_propagate_without_retry(self):
        t = FakeTransport(OpenAIError("Немає зв'язку"), http_ok({}))
        with self.assertRaises(OpenAIError):
            openai_api.request_json("GET", "/models", transport=t)
        self.assertEqual(len(t.requests), 1)

    def test_429_is_not_retried(self):
        t = FakeTransport(http_error(429, {}), http_ok({}))
        with self.assertRaises(OpenAIError):
            openai_api.request_json("GET", "/models", transport=t)
        self.assertEqual(len(t.requests), 1)

    def test_default_transport_is_the_guarded_one_in_offline_tests(self):
        with self.assertRaises(AssertionError):
            openai_api.request_json("GET", "/models")


class RealTransportTests(unittest.TestCase):
    def request(self):
        return {"method": "GET", "url": "https://api.openai.com/v1/models", "headers": {}, "body": None, "timeout": 5}

    def test_success(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.status = 200
        resp.read.return_value = b"{}"
        with mock.patch("engine.openai_api.urllib.request.urlopen", return_value=resp):
            self.assertEqual(openai_api.real_transport(self.request()), {"status": 200, "body": b"{}"})

    def test_http_error_becomes_a_status_response(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error":{"message":"bad"}}'))
        with mock.patch("engine.openai_api.urllib.request.urlopen", side_effect=err):
            resp = openai_api.real_transport(self.request())
        self.assertEqual(resp["status"], 401)
        self.assertIn(b"bad", resp["body"])

    def test_network_errors_become_openai_errors(self):
        for exc in (urllib.error.URLError("boom"), TimeoutError("slow")):
            with mock.patch("engine.openai_api.urllib.request.urlopen", side_effect=exc):
                with self.assertRaises(OpenAIError) as ctx:
                    openai_api.real_transport(self.request())
            self.assertIn("зв'язку", str(ctx.exception))


class ListModelsTests(EnvCase):
    def test_sorted_ids(self):
        t = FakeTransport(http_ok({"data": [{"id": "b-model"}, {"id": "a-model"}, {"nope": 1}, "x"]}))
        self.assertEqual(openai_api.list_models(transport=t), ["a-model", "b-model"])
        self.assertTrue(t.requests[0]["url"].endswith("/models"))


class BackendTests(EnvCase):
    def answer(self, text):
        return http_ok({"choices": [{"message": {"content": text}}]})

    def test_run_returns_text_and_sends_chat_completions(self):
        t = FakeTransport(self.answer("  привіт \n"))
        self.assertEqual(OpenAIBackend(transport=t).run("### TASK: x"), "привіт")
        req = t.requests[0]
        self.assertTrue(req["url"].endswith("/chat/completions"))
        self.assertEqual(json.loads(req["body"]),
                         {"model": "text-model-x", "messages": [{"role": "user", "content": "### TASK: x"}]})

    def test_explicit_model_wins(self):
        t = FakeTransport(self.answer("ok"))
        OpenAIBackend(model="m2", transport=t).run("### TASK: x")
        self.assertEqual(json.loads(t.requests[0]["body"])["model"], "m2")

    def test_missing_model_explains_how_to_fix_it(self):
        t = FakeTransport()
        with mock.patch.dict(os.environ, {"OPENAI_TEXT_MODEL": ""}):
            with self.assertRaises(llm.LLMError) as ctx:
                OpenAIBackend(transport=t).run("### TASK: x")
        self.assertIn("OPENAI_TEXT_MODEL", str(ctx.exception))
        self.assertIn("models", str(ctx.exception))
        self.assertEqual(t.requests, [])

    def test_odd_responses_are_errors(self):
        for odd in (http_ok({"choices": []}), self.answer("  "), http_ok({"x": 1}), self.answer(None)):
            with self.assertRaises(llm.LLMError):
                OpenAIBackend(transport=FakeTransport(odd)).run("### TASK: x")

    def test_prompt_too_long_never_calls_the_transport(self):
        t = FakeTransport()
        with self.assertRaises(llm.LLMError):
            OpenAIBackend(transport=t).run("x" * (llm.MAX_PROMPT + 1))
        self.assertEqual(t.requests, [])

    def test_no_retry_after_a_server_error(self):
        t = FakeTransport(http_error(500, {}), self.answer("late"))
        with self.assertRaises(llm.LLMError):
            OpenAIBackend(transport=t).run("### TASK: x")
        self.assertEqual(len(t.requests), 1)


if __name__ == "__main__":
    unittest.main()
