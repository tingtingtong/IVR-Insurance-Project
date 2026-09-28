"""ElevenLabs TTS streaming — PCM 8kHz mono → mulaw for Twilio."""
import audioop
import aiohttp
from typing import AsyncIterator
from config import settings
from utils.tts_normalizer import normalize_tts_text


class ElevenLabsTTSService:
    """
    ElevenLabs streaming TTS.
    Requests pcm_8000 (8kHz 16-bit mono PCM) and converts to G.711 mulaw for Twilio.
    """

    def __init__(self):
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = settings.elevenlabs_voice_id
        self._model = settings.elevenlabs_model
        self._url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}/stream"
        )

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        clean_text = normalize_tts_text(text)
        if not clean_text:
            return

        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": clean_text,
            "model_id": self._model,
            "voice_settings": {
                "stability": settings.elevenlabs_stability,
                "similarity_boost": settings.elevenlabs_similarity_boost,
            },
            "output_format": "pcm_8000",
        }
        params = {
            "optimize_streaming_latency": settings.elevenlabs_optimize_streaming_latency,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url, json=body, headers=headers, params=params
            ) as resp:
                if resp.status != 200:
                    import structlog
                    log = structlog.get_logger()
                    log.error("elevenlabs_tts_error", status=resp.status,
                              body=await resp.text())
                    return
                async for chunk in resp.content.iter_chunked(4096):
                    if not chunk:
                        continue
                    # pcm_8000 is already 8kHz 16-bit — just convert to mulaw
                    mulaw_chunk = audioop.lin2ulaw(chunk, 2)
                    yield mulaw_chunk
