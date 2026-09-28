"""
STT provider factory.

Set STT_PROVIDER in .env:
  - "deepgram" (default) — Deepgram Nova-2, real-time streaming WebSocket
"""
from typing import Callable, Awaitable
from config import settings


def STTService(on_transcript: Callable[[str, bool], Awaitable[None]]):
    """Factory — returns the STT service based on STT_PROVIDER setting."""
    provider = settings.stt_provider.lower()
    if provider == "deepgram":
        from services.stt_deepgram import DeepgramSTTService
        return DeepgramSTTService(on_transcript=on_transcript)
    raise ValueError(f"Unknown STT_PROVIDER: {provider!r}. Use 'deepgram'.")
