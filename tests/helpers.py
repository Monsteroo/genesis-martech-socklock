import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SAMPLE_BLOCKS = [
    {"type": "HOOK", "text": "I found where all your missing socks live."},
    {"type": "PAIN", "text": "Every wash, one sock vanishes. You keep the lonely one for months, just in case."},
    {"type": "DEMO", "text": "SockLock has a tiny magnet in the cuff. The pair snaps together and stays together through wash and dry."},
    {"type": "PROOF", "text": "Customers say the pairs finally stay together."},
    {"type": "ONSCREEN", "text": "No more lonely socks"},
    {"type": "CTA", "text": "Try 5 pairs for $29, delivered every quarter."},
]

GLOSSARY = {"protected": ["SockLock"], "forbidden": ["guaranteed", "cure"], "notes": "magnetic socks"}


class FakeLLM:
    """Replaces only the model call. Returns queued answers in order and records prompts."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError("FakeLLM: більше немає підготовлених відповідей")
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


def make_root():
    """A temp project root with a copy of samples/ (glossary, demo answers)."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "samples", tmp / "samples")
    return tmp


def segments_answer(prefix=""):
    return {"segments": [{"id": f"s{i}", "text": prefix + b["text"]} for i, b in enumerate(SAMPLE_BLOCKS, 1)]}


PNG_MAGIC = bytes([137, 80, 78, 71, 13, 10, 26, 10])


class FakeImages:
    """Replaces only the image provider. Records prompts and writes a tiny PNG-looking file."""

    kind = "image"
    name = "fake"

    def __init__(self, available=(True, ""), fail=None):
        self._available = available
        self.fail = fail
        self.prompts = []

    def available(self):
        return self._available

    def generate(self, prompt, dest):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_MAGIC + b"fake")
        return dest
