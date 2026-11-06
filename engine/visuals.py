"""Optional key-frame generation. Disabled in the MVP.

Checked on 2026-11-06: Gemini CLI 0.67.0 rejects "Sign in with Google" for individual accounts
("migrate to Antigravity"); the remaining options are an API key or Vertex AI, which break the
subscription-only rule. We report the limitation instead of pretending to generate anything."""

REASON = (
    "Gemini CLI: вхід через Google для особистих акаунтів більше не підтримується "
    "(лишаються API-ключ або Vertex AI, що суперечить правилу «лише підписка»)."
)


class VisualsUnavailable(Exception):
    """Readable, user-facing failure."""


def available():
    return False, REASON


def storyboard_frames(shot_list):
    ok, reason = available()
    if not ok:
        raise VisualsUnavailable(reason)
