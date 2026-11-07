"""Video (image -> clip) providers: heygen (HeyGen Video image_to_video), demo (a bundled 1-second clip), NoVideo.

A provider has kind "video", name, `available() -> (bool, reason)` and
`animate(prompt, src_png, dest_mp4, duration) -> Path`. The clip keeps the aspect ratio of the source frame:
HeyGen ignores aspect_ratio for image_to_video."""
import base64
import os
import time
import uuid
from pathlib import Path

from engine import heygen_api
from engine.heygen_api import HeyGenError
from engine.visuals import PNG_MAGIC, VisualsUnavailable, _atomic_copy, _atomic_write, _env_float, register_provider

DURATIONS = (5, 10, 15)          # image_to_video accepts 5-30 s; longer clips cost more, so the UI offers a short list
DEFAULT_DURATION = 5
DEFAULT_RESOLUTION = "768p"      # HeyGen: 480p | 768p | 1080p | 2k
MAX_INLINE_BYTES = 3_700_000     # base64 inflates by 4/3 and HeyGen limits inline images to 5 MB
MAX_SOURCE_BYTES = 32_000_000    # POST /v3/assets: images up to 32 MB
DEFAULT_TIMEOUT = 600
POLL_INTERVAL = 5
MAX_POLL_ERRORS = 3
DEMO_CLIP = Path(__file__).resolve().parent.parent / "samples" / "demo" / "clip.mp4"

CHIP_PROMPTS = {
    "повільний наїзд": "slow, smooth push-in of the camera toward the subject",
    "відʼїзд": "slow, smooth pull-back of the camera away from the subject",
    "паралакс": "gentle parallax: the camera drifts sideways and the foreground moves slightly faster than the background",
    "легкий рух тканини": "the fabric moves very slightly, as if in a light breeze",
    "плавний поворот": "smooth, slow orbit of the camera around the subject",
    "мерехтіння світла": "soft light flickers gently across the scene",
}
DEFAULT_MOTION = "a very slow, subtle push-in; everything else stays calm"
GUIDE = ("The clip opens on the supplied image as its first frame and keeps its composition. One short continuous shot, "
         "locked-off background, no cuts. No music, no dialogue, no voice-over. "
         "No logos, brand names, printed words or on-screen text anywhere in frame.")


def motion_prompt(motion):
    parts = [p.strip() for p in (motion or "").split(",") if p.strip()]
    mapped = [CHIP_PROMPTS.get(p, p) for p in parts]
    return f"Camera and motion: {'; '.join(mapped) if mapped else DEFAULT_MOTION}. {GUIDE}"


def _timeout_from_env():
    try:
        value = float(os.environ.get("HEYGEN_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT
    if value != value:
        return DEFAULT_TIMEOUT
    return min(max(value, 30), 1800)


class NoVideo:
    kind = "video"
    name = "none"

    def available(self):
        return False, "Провайдер відео не налаштовано."

    def animate(self, prompt, src, dest, duration=DEFAULT_DURATION):
        raise VisualsUnavailable(self.available()[1])


class DemoVideoProvider:
    kind = "video"
    name = "demo"

    def __init__(self, clip=DEMO_CLIP, delay=None):
        self.clip = Path(clip)
        self.delay = delay if delay is not None else _env_float("DEMO_VIDEO_DELAY")

    def available(self):
        if self.clip.is_file():
            return True, ""
        return False, f"Немає демо-відео ({self.clip.name})."

    def animate(self, prompt, src, dest, duration=DEFAULT_DURATION):
        ok, reason = self.available()
        if not ok:
            raise VisualsUnavailable(reason)
        if self.delay > 0:
            time.sleep(self.delay)
        return _atomic_copy(self.clip, dest)


class HeyGenVideoProvider:
    kind = "video"
    name = "heygen"

    def __init__(self, resolution=None, transport=None, timeout=None, interval=POLL_INTERVAL):
        self.resolution = resolution or DEFAULT_RESOLUTION
        self.transport = transport
        self.timeout = _timeout_from_env() if timeout is None else timeout
        self.interval = interval

    def available(self):
        if (os.environ.get("HEYGEN_API_KEY") or "").strip():
            return True, ""
        return False, "Не задано HEYGEN_API_KEY (додайте ключ у файл .env)."

    def _image(self, src):
        src = Path(src)
        try:
            if not src.is_file() or src.stat().st_size > MAX_SOURCE_BYTES:
                raise HeyGenError("Кадр не знайдено або він завеликий.")
            content = src.read_bytes()
        except OSError:
            raise HeyGenError("Не вдалося прочитати кадр.") from None
        if not content.startswith(PNG_MAGIC):
            raise HeyGenError("Кадр має бути PNG-зображенням.")
        if len(content) <= MAX_INLINE_BYTES:
            return {"type": "base64", "media_type": "image/png", "data": base64.b64encode(content).decode("ascii")}
        return {"type": "asset_id", "asset_id": heygen_api.upload_asset(content, transport=self.transport)}

    def _wait(self, video_id):
        """Polls until the job ends. Only GETs are retried (a few transient errors); returns the completed record."""
        deadline = time.monotonic() + self.timeout
        errors = 0
        while True:
            try:
                data = heygen_api.get_job(video_id, transport=self.transport)
                errors = 0
            except HeyGenError as exc:
                errors += 1
                if not exc.transient or errors > MAX_POLL_ERRORS:
                    raise HeyGenError(f"{exc} (відео HeyGen: {video_id})", transient=False) from None
                data = {}
            status = data.get("status")
            if status == "completed":
                return data
            if status in ("failed", "cancelled"):
                detail = heygen_api.redact(str(data.get("failure_message") or ""), os.environ.get("HEYGEN_API_KEY"))[:200].strip()
                raise HeyGenError(f"HeyGen не створив відео ({status}). {detail} (відео HeyGen: {video_id})".replace("  ", " "))
            if time.monotonic() >= deadline:
                raise HeyGenError(f"HeyGen не встиг за {int(self.timeout)} с. Відео може ще з'явитись у вашому кабінеті HeyGen "
                                  f"(відео HeyGen: {video_id}). Автоматичного повтору немає.")
            time.sleep(self.interval)

    def animate(self, prompt, src, dest, duration=DEFAULT_DURATION):
        if not (isinstance(prompt, str) and prompt.strip()):
            raise HeyGenError("Порожній опис анімації.")
        image = self._image(src)
        payload = {"model": "heygen-video-1", "mode": "image_to_video", "prompt": prompt, "image": image,
                   "duration": duration, "resolution": self.resolution}
        video_id = heygen_api.create_job(payload, "socklock-" + uuid.uuid4().hex, transport=self.transport)
        record = self._wait(video_id)
        data = None
        for attempt in (0, 1):   # the signed link can expire: ask for a fresh one once
            try:
                data = heygen_api.download(record.get("video_url"), transport=self.transport)
                break
            except HeyGenError as exc:
                if attempt or "403" not in str(exc):
                    raise HeyGenError(f"{exc} (відео HeyGen: {video_id})") from None
                record = self._wait(video_id)
        return _atomic_write(dest, data)


register_provider("demo", "video", DemoVideoProvider)
register_provider("heygen", "video", HeyGenVideoProvider)
