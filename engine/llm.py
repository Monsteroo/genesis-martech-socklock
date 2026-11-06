"""LLM access. One function: run(prompt) -> str.

Backends: openai (the OpenAI API with the user's own key, see engine/openai_api.py) and demo (recorded
answers). There is deliberately no automatic fallback between them."""
import os
from pathlib import Path

MAX_PROMPT = 10_000
DEMO_DIR = Path(__file__).resolve().parent.parent / "samples" / "demo"


class LLMError(Exception):
    """Readable, user-facing failure."""


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
    name = name or os.environ.get("ENGINE_BACKEND") or "openai"
    if name == "demo":
        return DemoBackend()
    if name == "openai":
        from engine.openai_api import OpenAIBackend  # local import: openai_api imports this module

        return OpenAIBackend(**kw)
    if name == "codex":
        raise LLMError("Бекенд codex видалено: система працює через OpenAI API (--backend openai).")
    raise LLMError(f"Невідомий бекенд: {name}")
