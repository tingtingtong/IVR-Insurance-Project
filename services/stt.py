import asyncio
from typing import Callable, Awaitable
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)
from config import settings

# Deepgram options — driven by settings so they can be changed via the dashboard
def _build_deepgram_options() -> LiveOptions:
    return LiveOptions(
        model=settings.deepgram_model,
        language=settings.deepgram_language,
        encoding=settings.deepgram_encoding,
        sample_rate=settings.deepgram_sample_rate,
        channels=settings.deepgram_channels,
        punctuate=settings.deepgram_punctuate,
        interim_results=settings.deepgram_interim_results,
        endpointing=settings.deepgram_endpointing,
        utterance_end_ms=settings.deepgram_utterance_end_ms,
        smart_format=settings.deepgram_smart_format,
        no_delay=settings.deepgram_no_delay,
        keywords=[              # domain-specific boost for insuranceCompany
            "insuranceCompany",
            "policy number",
            "beneficiary",
            "premium",
            "paid-to-date",
            "autopay",
        ],
    )

DEEPGRAM_OPTIONS = _build_deepgram_options()


class STTService:
    """
    Deepgram streaming STT.
    Wraps a live connection and exposes send_audio() + a transcript callback.
    """

    def __init__(self, on_transcript: Callable[[str, bool], Awaitable[None]]):
        """
        on_transcript(text, is_final):
          text     — transcript text
          is_final — True when Deepgram considers the utterance complete
        """
        self._on_transcript = on_transcript
        self._dg = DeepgramClient(
            settings.deepgram_api_key,
            DeepgramClientOptions(options={"keepalive": "true"}),
        )
        self._connection = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._connection = self._dg.listen.asynclive.v("1")

        self._connection.on(LiveTranscriptionEvents.Transcript, self._handle_transcript)
        self._connection.on(LiveTranscriptionEvents.Error, self._handle_error)

        await self._connection.start(DEEPGRAM_OPTIONS)

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        """Forward raw mulaw audio from Twilio directly to Deepgram."""
        if self._connection:
            await self._connection.send(mulaw_bytes)

    async def finish(self) -> None:
        if self._connection:
            await self._connection.finish()

    async def _handle_transcript(self, _client, result, **kwargs) -> None:
        try:
            alt = result.channel.alternatives[0]
            text = alt.transcript.strip()
            if not text:
                return
            is_final = result.is_final
            await self._on_transcript(text, is_final)
        except (AttributeError, IndexError):
            pass

    async def _handle_error(self, _client, error, **kwargs) -> None:
        import structlog
        log = structlog.get_logger()
        log.error("deepgram_error", error=str(error))
