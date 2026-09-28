"""Google Cloud Speech-to-Text streaming STT service."""
import asyncio
import struct
from typing import Callable, Awaitable
import structlog
from config import settings

log = structlog.get_logger()


class GoogleSTTService:
    """
    Google Cloud Speech-to-Text streaming STT.

    Requires:
      - pip install google-cloud-speech
      - GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON
        OR running on GCP with default credentials

    Config in .env:
      GOOGLE_STT_LANGUAGE=en-US
      GOOGLE_STT_MODEL=telephony
    """

    def __init__(self, on_transcript: Callable[[str, bool], Awaitable[None]]):
        self._on_transcript = on_transcript
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        from google.cloud.speech_v1 import SpeechAsyncClient
        self._loop = asyncio.get_event_loop()
        self._queue = asyncio.Queue()
        self._client = SpeechAsyncClient()
        self._task = asyncio.create_task(self._stream_recognize())

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        if self._queue:
            await self._queue.put(mulaw_bytes)

    async def finish(self) -> None:
        if self._queue:
            await self._queue.put(None)  # sentinel
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _audio_generator(self):
        """Yields StreamingRecognizeRequest messages."""
        from google.cloud.speech_v1 import (
            StreamingRecognizeRequest,
            StreamingRecognitionConfig,
            RecognitionConfig,
        )

        # First request: config only
        config = StreamingRecognitionConfig(
            config=RecognitionConfig(
                encoding=RecognitionConfig.AudioEncoding.MULAW,
                sample_rate_hertz=8000,
                language_code=getattr(settings, "google_stt_language", "en-US"),
                model=getattr(settings, "google_stt_model", "telephony"),
                enable_automatic_punctuation=True,
            ),
            interim_results=True,
        )
        yield StreamingRecognizeRequest(streaming_config=config)

        # Subsequent requests: audio chunks
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield StreamingRecognizeRequest(audio_content=chunk)

    async def _stream_recognize(self):
        try:
            responses = await self._client.streaming_recognize(
                requests=self._audio_generator()
            )
            async for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    text = result.alternatives[0].transcript.strip()
                    if text:
                        await self._on_transcript(text, result.is_final)
        except Exception as e:
            log.error("google_stt_error", error=str(e))
