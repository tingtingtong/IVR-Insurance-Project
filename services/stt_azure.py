"""Azure Cognitive Services Speech-to-Text streaming STT service."""
import asyncio
from typing import Callable, Awaitable
import structlog
from config import settings

log = structlog.get_logger()


class AzureSTTService:
    """
    Azure Speech Services streaming STT.

    Requires:
      - pip install azure-cognitiveservices-speech
      - AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env

    Config in .env:
      AZURE_SPEECH_KEY=your-key
      AZURE_SPEECH_REGION=eastus
      AZURE_STT_LANGUAGE=en-US
    """

    def __init__(self, on_transcript: Callable[[str, bool], Awaitable[None]]):
        self._on_transcript = on_transcript
        self._loop: asyncio.AbstractEventLoop | None = None
        self._push_stream = None
        self._recognizer = None

    async def start(self) -> None:
        import azure.cognitiveservices.speech as speechsdk

        self._loop = asyncio.get_event_loop()

        speech_key = getattr(settings, "azure_speech_key", "")
        speech_region = getattr(settings, "azure_speech_region", "eastus")
        language = getattr(settings, "azure_stt_language", "en-US")

        speech_config = speechsdk.SpeechConfig(
            subscription=speech_key,
            region=speech_region,
        )
        speech_config.speech_recognition_language = language

        # Push stream for feeding raw audio
        self._push_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=speechsdk.audio.AudioStreamFormat(
                samples_per_second=8000,
                bits_per_sample=8,
                channels=1,
                wave_stream_format=speechsdk.audio.AudioStreamWaveFormat.MULAW,
            )
        )
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)

        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Wire up callbacks
        self._recognizer.recognizing.connect(
            lambda evt: self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._on_transcript(evt.result.text.strip(), False),
            )
        )
        self._recognizer.recognized.connect(
            lambda evt: self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._on_transcript(evt.result.text.strip(), True),
            )
        )
        self._recognizer.canceled.connect(self._on_canceled)

        self._recognizer.start_continuous_recognition_async()

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        if self._push_stream:
            self._push_stream.write(mulaw_bytes)

    async def finish(self) -> None:
        if self._push_stream:
            self._push_stream.close()
        if self._recognizer:
            self._recognizer.stop_continuous_recognition_async()

    def _on_canceled(self, evt) -> None:
        import azure.cognitiveservices.speech as speechsdk
        if evt.reason == speechsdk.CancellationReason.Error:
            log.error("azure_stt_error", error=evt.error_details)
