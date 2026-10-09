"""#111: a Twilio `stop` event (caller hung up) marks the call ended so the post-stream
<Redirect> to /webhook/stream-fallback hangs up instead of re-prompting a dead call."""
import json
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_stream as ts
import webhooks.twilio_voice as tv
from services import conversation_store as cs


class _WS:
    """Yields the given Twilio events, then ends."""

    def __init__(self, events):
        self._events = [json.dumps(e) for e in events]
        self.close = AsyncMock()

    async def iter_text(self):
        for e in self._events:
            yield e


class StopEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_marks_the_call_ended_and_the_redirect_then_hangs_up(self):
        sid = "CA_STOP_1"
        cs.start_call(sid, "")
        cs.add_call_turn(sid, "bot", "What is the insured date of birth?", node="auth")
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(_WS([{"event": "connected"}, {"event": "stop"}]))
        h.call_sid = sid
        await h.run()
        self.assertEqual(cs.get_call(sid)["status"], "ended")

        from webhooks.twilio_voice import stream_fallback

        class Req:
            async def form(self_inner):
                return {"CallSid": sid}

        with patch.object(tv, "SessionService") as sess:
            sess.return_value.init_session = AsyncMock()
            resp = await stream_fallback(Req())
        body = resp.body.decode()
        self.assertIn("<Hangup", body)
        self.assertNotIn("<Gather", body)


if __name__ == "__main__":
    unittest.main()
