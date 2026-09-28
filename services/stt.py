"""
STT provider factory.

Set STT_PROVIDER in .env:
  - "deepgram" (default) — Deepgram Nova-2, real-time streaming WebSocket
  - "google"   — Google Cloud Speech-to-Text V1 streaming
  - "azure"    — Azure Cognitive Services Speech streaming
"""
from typing import Callable, Awaitable
from config import settings


def STTService(on_transcript: Callable[[str, bool], Awaitable[None]]):
    """Factory — returns the STT service based on STT_PROVIDER setting."""
    provider = settings.stt_provider.lower()
    if provider == "deepgram":
        from services.stt_deepgram import DeepgramSTTService
        return DeepgramSTTService(on_transcript=on_transcript)
    elif provider == "google":
        from services.stt_google import GoogleSTTService
        return GoogleSTTService(on_transcript=on_transcript)
    elif provider == "azure":
        from services.stt_azure import AzureSTTService
        return AzureSTTService(on_transcript=on_transcript)
    else:
        raise ValueError(
            f"Unknown STT_PROVIDER: {provider!r}. Use 'deepgram', 'google', or 'azure'."
        )
