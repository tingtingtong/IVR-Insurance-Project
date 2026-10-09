"""Pick the voice path (Gather or Media Stream) for a new call (#83)."""
import hashlib

from config import settings


def _norm(caller: str) -> str:
    """Phone numbers compare on their last 10 digits; other ids (client:...) compare as-is."""
    digits = "".join(c for c in caller if c.isdigit())
    if digits and caller.lstrip("+").isdigit():
        return digits[-10:]
    return caller.strip().lower()


def use_stream_path(call_sid: str, caller: str) -> bool:
    """True when this call should use Media Streams. Default (VOICE_PATH=gather) is False."""
    if (settings.voice_path or "gather").strip().lower() != "stream":
        return False

    allowed = {_norm(n) for n in (settings.stream_numbers or "").split(",") if n.strip()}
    if allowed and caller and _norm(caller) in allowed:
        return True

    percent = max(0, min(100, int(settings.stream_canary_percent)))
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    # Deterministic per call, so a retried webhook gets the same answer.
    bucket = int(hashlib.sha256(call_sid.encode("utf-8")).hexdigest(), 16) % 100
    return bucket < percent
