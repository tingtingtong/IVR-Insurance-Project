import audioop
from typing import AsyncIterator
from openai import AsyncOpenAI
from config import settings
from utils.tts_normalizer import normalize_tts_text

# Resample state (stateful ratecv across chunks)
_RESAMPLE_STATE = None

class TTSService:
    """
    OpenAI TTS streaming — PCM 24kHz → resampled to 8kHz mulaw for Twilio.
    Model and voice driven by settings (openai_tts_model / openai_tts_voice).
    """

    def __init__(self):
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        clean_text = normalize_tts_text(text)
        if not clean_text:
            return

        state = None  # ratecv state per utterance
        async with self._client.audio.speech.with_streaming_response.create(
            model=settings.openai_tts_model,
            voice=settings.openai_tts_voice,
            input=clean_text,
            response_format="pcm",  # raw 24kHz 16-bit signed LE PCM
        ) as resp:
            async for chunk in resp.iter_bytes(chunk_size=4096):
                if not chunk:
                    continue
                # Resample 24kHz → 8kHz (factor 1/3)
                resampled, state = audioop.ratecv(chunk, 2, 1, 24000, 8000, state)
                # Convert 16-bit PCM → G.711 mulaw
                mulaw_chunk = audioop.lin2ulaw(resampled, 2)
                yield mulaw_chunk
