import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine import llm


def make_runner(answer="ok", login_text="Logged in using ChatGPT", exec_rc=0, write=True):
    calls = []

    def runner(args, env, timeout, cwd):
        calls.append({"args": args, "env": env, "timeout": timeout, "cwd": cwd})
        if args[1] == "login":
            return SimpleNamespace(returncode=0, stdout="", stderr=login_text)
        if write:
            Path(args[args.index("-o") + 1]).write_text(answer, encoding="utf-8")
        return SimpleNamespace(returncode=exec_rc, stdout="", stderr="boom" if exec_rc else "")

    return runner, calls


class CodexBackendTests(unittest.TestCase):
    def backend(self, **kw):
        runner, calls = make_runner(**kw)
        return llm.CodexBackend(runner=runner, binary="/fake/codex"), calls

    def test_returns_last_message(self):
        b, _ = self.backend(answer="  привіт \n")
        self.assertEqual(b.run("### TASK: x"), "привіт")

    def test_exec_is_read_only_ephemeral_and_ignores_user_config(self):
        b, calls = self.backend()
        b.run("### TASK: x\nhello")
        args = calls[1]["args"]
        self.assertEqual(args[:2], ["/fake/codex", "exec"])
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", args)
        self.assertIn("--ignore-user-config", args)
        self.assertEqual(args[-1], "### TASK: x\nhello")
        self.assertEqual(calls[1]["timeout"], llm.TIMEOUT)

    def test_api_keys_are_stripped_from_child_env(self):
        b, calls = self.backend()
        fake_env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": "http://u", "CODEX_API_KEY": "c", "PATH": "/bin"}
        with mock.patch.dict(os.environ, fake_env):
            b.run("### TASK: x")
        for call in calls:
            for key in llm.STRIPPED_ENV:
                self.assertNotIn(key, call["env"])
            self.assertEqual(call["env"]["PATH"], "/bin")

    def test_not_logged_in_raises(self):
        b, calls = self.backend(login_text="Not logged in")
        with self.assertRaises(llm.LLMError) as ctx:
            b.run("### TASK: x")
        self.assertIn("не залогінений", str(ctx.exception))
        self.assertEqual(len(calls), 1)  # exec was never started

    def test_missing_binary_raises_and_does_not_fall_back(self):
        runner, calls = make_runner()
        with mock.patch.object(llm, "codex_binary", return_value=None):
            b = llm.get_backend("codex", runner=runner)
            with self.assertRaises(llm.LLMError):
                b.run("### TASK: x")
        self.assertEqual(calls, [])

    def test_timeout_is_reported_and_not_retried(self):
        calls = []

        def runner(args, env, timeout, cwd):
            calls.append(args)
            if args[1] == "login":
                return SimpleNamespace(returncode=0, stdout="", stderr="Logged in using ChatGPT")
            raise subprocess.TimeoutExpired(args, timeout)

        b = llm.CodexBackend(runner=runner, binary="/fake/codex")
        with self.assertRaises(llm.LLMError) as ctx:
            b.run("### TASK: x")
        self.assertIn("не відповів", str(ctx.exception))
        self.assertEqual(len(calls), 2)  # login check + exactly one exec

    def test_nonzero_exit_includes_error_tail(self):
        b, _ = self.backend(exec_rc=2)
        with self.assertRaises(llm.LLMError) as ctx:
            b.run("### TASK: x")
        self.assertIn("boom", str(ctx.exception))

    def test_missing_answer_file_raises(self):
        b, _ = self.backend(write=False)
        with self.assertRaises(llm.LLMError):
            b.run("### TASK: x")

    def test_prompt_too_long_raises(self):
        b, calls = self.backend()
        with self.assertRaises(llm.LLMError):
            b.run("x" * (llm.MAX_PROMPT + 1))
        self.assertEqual(calls, [])


class DemoBackendTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ping.json").write_text("ok\n", encoding="utf-8")

    def test_reads_answer_by_task_name(self):
        self.assertEqual(llm.DemoBackend(self.dir).run("### TASK: ping\nanything"), "ok")

    def test_requires_task_line(self):
        with self.assertRaises(llm.LLMError):
            llm.DemoBackend(self.dir).run("no task line")

    def test_unknown_task_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.DemoBackend(self.dir).run("### TASK: nope")


class GetBackendTests(unittest.TestCase):
    def test_unknown_backend(self):
        with self.assertRaises(llm.LLMError):
            llm.get_backend("gemini")

    def test_default_comes_from_env(self):
        with mock.patch.dict(os.environ, {"ENGINE_BACKEND": "demo"}):
            self.assertIsInstance(llm.get_backend(), llm.DemoBackend)


if __name__ == "__main__":
    unittest.main()
