import json
import shutil
import tempfile
import threading
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
        self.sizes = []
        self.edits = []

    def available(self):
        return self._available

    def generate(self, prompt, dest, size=None):
        self.prompts.append(prompt)
        self.sizes.append(size)
        if self.fail:
            raise self.fail
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_MAGIC + b"fake")
        return dest

    def edit(self, prompt, src, dest, size=None):
        self.edits.append((prompt, str(src), size))
        if self.fail:
            raise self.fail
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(PNG_MAGIC + b"edit")
        return dest


TEST_KEY = "sk-test-0123456789abcdef"


class FakeTransport:
    """Replaces only the HTTP transport. Records requests; answers in order (a response dict or an Exception to raise)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self._lock = threading.Lock()

    def __call__(self, request):
        with self._lock:
            self.requests.append(request)
            if not self.responses:
                raise AssertionError("FakeTransport: більше немає підготовлених відповідей")
            response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_ok(payload):
    return {"status": 200, "body": json.dumps(payload).encode("utf-8")}


def http_error(status, payload=None):
    return {"status": status, "body": json.dumps(payload if payload is not None else {}).encode("utf-8")}


MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"fake"


class FakeVideo:
    """Replaces only the video provider. Records calls and writes a tiny MP4-looking file."""

    kind = "video"
    name = "fake"

    def __init__(self, available=(True, ""), fail=None):
        self._available = available
        self.fail = fail
        self.calls = []

    def available(self):
        return self._available

    def animate(self, prompt, src, dest, duration=5):
        self.calls.append((prompt, str(src), duration))
        if self.fail:
            raise self.fail
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(MP4_BYTES)
        return dest
