"""LLM access. One function: run(prompt) -> str.

Backends: codex (the user's ChatGPT subscription through the Codex CLI) and demo (recorded answers).
There is deliberately no automatic fallback between them or to any paid API."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_PROMPT = 10_000
TIMEOUT = 240
CODEX_APP_PATH = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
STRIPPED_ENV = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY")
DEMO_DIR = Path(__file__).resolve().parent.parent / "samples" / "demo"


class LLMError(Exception):
    """Readable, user-facing failure."""


def clean_env(env=None):
    out = dict(os.environ if env is None else env)
    for key in STRIPPED_ENV:
        out.pop(key, None)
    return out


def codex_binary():
    explicit = os.environ.get("CODEX_BIN")
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    if Path(CODEX_APP_PATH).exists():
        return CODEX_APP_PATH
    return None


def subprocess_runner(args, env, timeout, cwd):
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout, cwd=cwd)


class CodexBackend:
    name = "codex"

    def __init__(self, runner=subprocess_runner, binary=None):
        self.runner = runner
        self.binary = binary or codex_binary()

    def _require_login(self, env):
        if not self.binary:
            raise LLMError("Codex CLI не знайдено. Встановіть його, задайте CODEX_BIN або використайте --backend demo.")
        res = self.runner([self.binary, "login", "status"], env, 30, None)
        text = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "ChatGPT" not in text:
            raise LLMError("Codex не залогінений через ChatGPT. Виконайте `codex login` або використайте --backend demo.")

    def run(self, prompt):
        if len(prompt) > MAX_PROMPT:
            raise LLMError(f"Запит задовгий ({len(prompt)} символів, ліміт {MAX_PROMPT}).")
        env = clean_env()
        self._require_login(env)
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "answer.txt"
            args = [
                self.binary, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--sandbox", "read-only", "--cd", tmp, "-o", str(out_file), prompt,
            ]
            try:
                res = self.runner(args, env, TIMEOUT, tmp)
            except subprocess.TimeoutExpired:
                raise LLMError(
                    f"Codex не відповів за {TIMEOUT} с. Автоматичного повтору немає: він витрачає ліміти підписки."
                )
            if res.returncode != 0:
                tail = (res.stderr or res.stdout or "").strip()[-300:]
                raise LLMError(f"Codex завершився з помилкою: {tail}")
            if not out_file.exists():
                raise LLMError("Codex не повернув відповідь.")
            return out_file.read_text(encoding="utf-8").strip()


class DemoBackend:
    name = "demo"

    def __init__(self, directory=DEMO_DIR):
        self.directory = Path(directory)

    def run(self, prompt):
        lines = prompt.strip().splitlines()
        first = lines[0] if lines else ""
        if not first.startswith("### TASK:"):
            raise LLMError("Демо-режим: у запиті немає рядка `### TASK: <назва>`.")
        task = first.split(":", 1)[1].strip()
        path = self.directory / f"{task}.json"
        if not path.exists():
            raise LLMError(f"Демо-режим: немає запису для завдання «{task}» ({path.name}).")
        return path.read_text(encoding="utf-8").strip()


def get_backend(name=None, **kw):
    name = name or os.environ.get("ENGINE_BACKEND", "codex")
    if name == "demo":
        return DemoBackend()
    if name == "codex":
        return CodexBackend(**kw)
    raise LLMError(f"Невідомий бекенд: {name}")
