"""Cache of synthesized audio for fixed prompts on the stream path (#106).

Only texts registered with register() are ever cached, so replies that contain a caller's
name, amounts or dates are never stored. The audio is G.711 mulaw, 8 kHz, as sent to Twilio.
"""
import structlog

from config import settings
from core.prompts.retry_prompts import PROMPTS

log = structlog.get_logger()

_audio: dict[tuple, list[bytes]] = {}
_static: set[str] = set()


def register(text: str) -> None:
    _static.add(text)


def is_static(text: str) -> bool:
    return text in _static


def _key(text: str) -> tuple:
    # A voice/model/provider change must not replay old audio.
    return (
        getattr(settings, "tts_provider", "openai"),
        getattr(settings, "openai_tts_voice", ""),
        getattr(settings, "openai_tts_model", ""),
        text,
    )


def get(text: str) -> list[bytes] | None:
    return _audio.get(_key(text))


def put(text: str, chunks: list[bytes]) -> None:
    if chunks:
        _audio[_key(text)] = list(chunks)


def clear() -> None:
    _audio.clear()


async def warm(tts) -> int:
    """Synthesize every registered prompt that is not cached yet. Returns how many were added."""
    added = 0
    for text in sorted(_static):
        if get(text) is not None:
            continue
        chunks = [c async for c in tts.stream(text) if c]
        put(text, chunks)
        added += 1 if chunks else 0
    return added


register(PROMPTS["greeting"]["welcome"])
