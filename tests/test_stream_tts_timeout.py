"""#110: a TTS request that never produces audio must not leave the caller in silence."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_stream as ts
from config import settings
from services import tts_cache


class FakeWS:
    def __init__(self):
        self.close = AsyncMock()
        self.send_text = AsyncMock()


class TtsFirstAudioTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tts_cache.clear()

    def _handler(self):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.call_sid = "CA_TTS1"
        h.stream_sid = "MZ1"
        return h

    async def test_stalled_tts_drops_the_call_to_gather(self):
        h = self._handler()

        async def stalled(_text):
            await asyncio.sleep(30)
            yield b"never"
        h.tts.stream = stalled
        with patch.object(settings, "tts_first_audio_timeout_s", 0.1):
            await h._stream_tts("What is the insured date of birth?")
        h.ws.close.assert_awaited_once_with(code=1011)
        self.assertTrue(h._fell_back)
        h.ws.send_text.assert_not_awaited()          # no half-played audio
        self.assertFalse(h._speaking)

    async def test_slow_later_chunks_are_not_a_stall(self):
        h = self._handler()

        async def gen(_text):
            yield b"aa"
            await asyncio.sleep(0.3)                 # longer than the first-audio timeout
            yield b"bb"
        h.tts.stream = gen
        with patch.object(settings, "tts_first_audio_timeout_s", 0.1):
            await h._stream_tts("A prompt.")
        self.assertFalse(h._fell_back)
        self.assertEqual(h.ws.send_text.await_count, 2)

    async def test_prompt_that_starts_in_time_plays_normally(self):
        h = self._handler()

        async def gen(_text):
            yield b"aa"
        h.tts.stream = gen
        with patch.object(settings, "tts_first_audio_timeout_s", 1.0):
            await h._stream_tts("Fine.")
        self.assertFalse(h._fell_back)
        h.ws.close.assert_not_awaited()

    async def test_cached_prompt_has_no_wait_to_time_out(self):
        from core.prompts.retry_prompts import PROMPTS
        text = PROMPTS["greeting"]["welcome"]
        tts_cache.put(text, [b"aa", b"bb"])
        h = self._handler()
        with patch.object(settings, "tts_first_audio_timeout_s", 0.001):
            await h._stream_tts(text)
        self.assertFalse(h._fell_back)
        self.assertEqual(h.ws.send_text.await_count, 2)

    async def test_zero_disables_the_guard(self):
        h = self._handler()

        async def slow(_text):
            await asyncio.sleep(0.2)
            yield b"aa"
        h.tts.stream = slow
        with patch.object(settings, "tts_first_audio_timeout_s", 0):
            await h._stream_tts("Slow but allowed.")
        self.assertFalse(h._fell_back)
        self.assertEqual(h.ws.send_text.await_count, 1)


if __name__ == "__main__":
    unittest.main()
