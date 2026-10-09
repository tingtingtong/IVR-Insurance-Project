"""#109: barge-in must not cancel a prompt that has not started playing yet."""
import asyncio
import base64
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_stream as ts


class FakeWS:
    def __init__(self):
        self.close = AsyncMock()
        self.send_text = AsyncMock()


def _media():
    return {"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}


class BargeInTimingTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.call_sid = "CA_BARGE1"
        h.stream_sid = "MZ1"
        h.vad.process = MagicMock(return_value=True)    # the VAD hears speech/noise on every frame
        h._cancel_tts = AsyncMock()
        return h

    async def test_noise_while_waiting_for_first_audio_does_not_cancel_the_prompt(self):
        h = self._handler()
        first, second = asyncio.Event(), asyncio.Event()

        async def gen(_text):
            await first.wait()           # TTS has not produced audio yet (the ~2.5 s gap)
            yield b"aa"
            await second.wait()
            yield b"bb"
        h.tts.stream = gen
        task = asyncio.create_task(h._stream_tts("Please enter your CVV."))
        await asyncio.sleep(0.05)

        await h._on_media(_media())      # caller noise / trailing speech before any audio plays
        h._cancel_tts.assert_not_awaited()
        self.assertFalse(h._speaking)

        first.set()
        await asyncio.sleep(0.05)
        self.assertTrue(h._speaking)     # audio is playing now
        second.set()
        await task

    async def test_caller_can_still_interrupt_once_the_prompt_is_playing(self):
        h = self._handler()
        release = asyncio.Event()

        async def gen(_text):
            yield b"aa"
            await release.wait()
            yield b"bb"
        h.tts.stream = gen
        task = asyncio.create_task(h._stream_tts("A long prompt."))
        await asyncio.sleep(0.05)
        self.assertTrue(h._speaking)

        await h._on_media(_media())
        h._cancel_tts.assert_awaited_once()
        release.set()
        await task

    async def test_speaking_is_cleared_when_the_prompt_ends(self):
        h = self._handler()

        async def gen(_text):
            yield b"aa"
        h.tts.stream = gen
        await h._stream_tts("Short.")
        self.assertFalse(h._speaking)


if __name__ == "__main__":
    unittest.main()
