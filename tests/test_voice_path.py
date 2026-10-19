"""#83: VOICE_PATH flag, stream TwiML, and per-call fallback to Gather."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_voice as tv
import webhooks.twilio_stream as ts
from config import settings
from fastapi import BackgroundTasks
from services import conversation_store as cs
from services.voice_path import use_stream_path


class FakeRequest:
    def __init__(self, form=None, headers=None, scheme="https"):
        self._form = form or {}
        self.headers = headers or {}
        self.url = SimpleNamespace(scheme=scheme)

    async def form(self):
        return self._form


def _cfg(**kw):
    return patch.multiple(settings, **kw)


class UseStreamPathTests(unittest.TestCase):
    def test_default_is_gather(self):
        with _cfg(voice_path="gather", stream_numbers="", stream_canary_percent=100):
            self.assertFalse(use_stream_path("CA1", "5551234567"))

    def test_stream_for_everyone_by_default(self):
        with _cfg(voice_path="stream", stream_numbers="", stream_canary_percent=100):
            self.assertTrue(use_stream_path("CA1", "+15551234567"))

    def test_allow_list_streams_only_listed_when_percent_zero(self):
        with _cfg(voice_path="stream", stream_numbers="5551234567, client:browser_tester",
                  stream_canary_percent=0):
            self.assertTrue(use_stream_path("CA1", "+15551234567"))   # last 10 digits
            self.assertTrue(use_stream_path("CA2", "client:browser_tester"))
            self.assertFalse(use_stream_path("CA3", "+15559999999"))

    def test_percentage_is_deterministic_and_roughly_right(self):
        with _cfg(voice_path="stream", stream_numbers="", stream_canary_percent=30):
            first = [use_stream_path(f"CA{i}", "") for i in range(400)]
            second = [use_stream_path(f"CA{i}", "") for i in range(400)]
        self.assertEqual(first, second)
        self.assertTrue(60 <= sum(first) <= 180)

    def test_gather_flag_beats_allow_list(self):
        with _cfg(voice_path="gather", stream_numbers="5551234567", stream_canary_percent=100):
            self.assertFalse(use_stream_path("CA1", "5551234567"))


class IncomingCallTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, request):
        with patch.object(tv, "SessionService") as sess:
            sess.return_value.init_session = AsyncMock()
            return await tv.incoming_call(request, BackgroundTasks())

    async def test_default_returns_gather_greeting(self):
        with _cfg(voice_path="gather"):
            resp = await self._call(FakeRequest({"CallSid": "CA_G1", "From": ""}))
        body = resp.body.decode()
        self.assertIn("<Gather", body)
        self.assertNotIn("<Stream", body)

    async def test_stream_twiml_has_stream_then_redirect_fallback(self):
        with _cfg(voice_path="stream", stream_numbers="", stream_canary_percent=100, ws_auth_token=""), \
             patch.object(tv, "_public_base_url", "https://example.ngrok-free.dev"):
            resp = await self._call(FakeRequest({"CallSid": "CA_S1", "From": "+15551234567"}))
        body = resp.body.decode()
        self.assertIn('<Connect><Stream url="wss://example.ngrok-free.dev/stream">', body)
        self.assertIn('name="callSid"', body)
        self.assertLess(body.index("<Connect>"), body.index("<Redirect"))
        self.assertIn("/webhook/stream-fallback", body)
        self.assertNotIn("<Gather", body)

    async def test_stream_url_carries_ws_token(self):
        with _cfg(voice_path="stream", stream_canary_percent=100, ws_auth_token="s3cret&x"), \
             patch.object(tv, "_public_base_url", "https://example.ngrok-free.dev"):
            resp = await self._call(FakeRequest({"CallSid": "CA_S2", "From": ""}))
        self.assertIn("/stream?token=s3cret%26x", resp.body.decode())

    async def test_unknown_public_url_falls_back_to_gather(self):
        with _cfg(voice_path="stream", stream_canary_percent=100), \
             patch.object(tv, "_public_base_url", ""):
            resp = await self._call(FakeRequest({"CallSid": "CA_S3", "From": ""}, headers={}))
        self.assertIn("<Gather", resp.body.decode())


class StreamFallbackEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, request):
        with patch.object(tv, "SessionService") as sess:
            sess.return_value.init_session = AsyncMock()
            return await tv.stream_fallback(request)

    async def test_stream_never_started_greets_on_gather(self):
        sid = "CA_FB_NEW"
        cs._calls.pop(sid, None)
        resp = await self._call(FakeRequest({"CallSid": sid, "From": "+15551234567"}))
        body = resp.body.decode()
        self.assertIn("<Gather", body)
        self.assertIn("How can I help you today", body)
        self.assertEqual(cs.get_call(sid)["turns"][0]["node"], "greeting")

    async def test_mid_call_resumes_with_last_bot_prompt(self):
        sid = "CA_FB_MID"
        cs.start_call(sid, "")
        cs.add_call_turn(sid, "bot", "Greeting.", node="greeting")
        cs.add_call_turn(sid, "human", "my policy status")
        cs.add_call_turn(sid, "bot", "What is the insured date of birth?", node="auth")
        resp = await self._call(FakeRequest({"CallSid": sid}))
        self.assertIn("date of birth", resp.body.decode())
        self.assertEqual(len(cs.get_call(sid)["turns"]), 3)   # nothing re-added or reset


class EndedCallFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_after_a_finished_call_hangs_up_instead_of_reprompting(self):
        sid = "CA_FB_ENDED"
        cs.start_call(sid, "")
        cs.add_call_turn(sid, "bot", "Thank you for calling. Have a great day. Goodbye.", node="goodbye")
        cs.end_call(sid)
        turns_before = len(cs.get_call(sid)["turns"])
        with patch.object(tv, "SessionService") as sess:
            sess.return_value.init_session = AsyncMock()
            resp = await tv.stream_fallback(FakeRequest({"CallSid": sid}))
        body = resp.body.decode()
        self.assertIn("<Hangup", body)
        self.assertNotIn("<Gather", body)
        self.assertNotIn("Goodbye", body)
        self.assertEqual(len(cs.get_call(sid)["turns"]), turns_before)


class FakeWS:
    def __init__(self):
        self.close = AsyncMock()
        self.sent = []

    async def send_text(self, t):
        self.sent.append(t)


class StreamHandlerFallbackTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.session = MagicMock()
        h.session.init_session = AsyncMock()
        return h

    async def test_stt_start_failure_closes_stream_without_greeting(self):
        h = self._handler()
        stt = MagicMock()
        stt.start = AsyncMock(side_effect=RuntimeError("deepgram_start_failed"))
        h._speak = AsyncMock()
        start = {"streamSid": "MZ1", "start": {"callSid": "CA_H1", "customParameters": {}}}
        with patch.object(ts, "STTService", return_value=stt), _cfg(auth_mode="standard"):
            await h._on_start(start)
        h.ws.close.assert_awaited_once_with(code=1011)
        self.assertTrue(h._fell_back)
        h._speak.assert_not_awaited()

    async def test_mid_call_stt_failure_closes_stream_once(self):
        h = self._handler()
        await h._on_stt_failure()
        await h._on_stt_failure()
        h.ws.close.assert_awaited_once_with(code=1011)

    async def test_no_speech_after_fallback(self):
        h = self._handler()
        h._stream_tts = AsyncMock()
        await h._fallback_to_gather("test")
        await h._speak("hello")
        h._stream_tts.assert_not_called()


class StreamTerminalTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_turn_marks_the_call_ended_before_hanging_up(self):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.call_sid = "CA_TERM1"
        h.session = MagicMock()
        h.session.save_state = AsyncMock()
        h._speak = AsyncMock()
        order = []
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"tts_text": "Goodbye.", "current_node": "goodbye"})
        twilio_client = MagicMock()
        twilio_client.return_value.calls.return_value.update.side_effect = lambda **k: order.append("hangup")
        with patch.object(ts._graph_module, "cno_graph", graph),              patch.object(ts, "_cs_end_call", side_effect=lambda sid: order.append("end_call")) as end,              patch.object(ts, "update_call_metadata"), patch.object(ts, "add_call_turn"),              patch("twilio.rest.Client", twilio_client):
            await h._invoke_graph_and_respond({"messages": []})
        end.assert_called_once_with("CA_TERM1")
        self.assertEqual(order, ["end_call", "hangup"])


class StreamGoodbyeOnceTests(unittest.IsolatedAsyncioTestCase):
    async def _terminal_turn(self, result):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.call_sid = "CA_BYE1"
        h.session = MagicMock()
        h.session.save_state = AsyncMock()
        h._speak = AsyncMock()
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value=result)
        with patch.object(ts._graph_module, "cno_graph", graph),              patch.object(ts, "_cs_end_call"), patch.object(ts, "update_call_metadata"),              patch.object(ts, "add_call_turn"), patch("twilio.rest.Client"):
            await h._invoke_graph_and_respond({"messages": []})
        return h._speak

    async def test_goodbye_node_text_is_not_followed_by_a_second_goodbye(self):
        speak = await self._terminal_turn({
            "tts_text": "Thank you for calling. Have a great day. Goodbye.",
            "current_node": "goodbye",
        })
        speak.assert_awaited_once_with("Thank you for calling. Have a great day. Goodbye.")

    async def test_terminal_turn_with_no_text_still_says_goodbye(self):
        speak = await self._terminal_turn({"tts_text": "", "current_node": "goodbye"})
        speak.assert_awaited_once_with("Thank you for calling. Goodbye.")


class SttServiceFailureTests(unittest.IsolatedAsyncioTestCase):
    def _svc(self, on_failure=None):
        from services.stt import STTService
        svc = STTService(on_transcript=AsyncMock(), on_failure=on_failure)
        conn = MagicMock()
        conn.start = AsyncMock(return_value=False)
        conn.finish = AsyncMock()
        svc._dg = MagicMock()
        svc._dg.listen.asynclive.v.return_value = conn
        return svc, conn

    async def test_start_raises_when_sdk_reports_connect_failure(self):
        svc, _ = self._svc()
        with self.assertRaises(RuntimeError):
            await svc.start()

    async def test_failed_reconnect_notifies_once(self):
        cb = AsyncMock()
        svc, conn = self._svc(on_failure=cb)
        svc._connection = conn
        with patch("asyncio.sleep", new=AsyncMock()):
            await svc._reconnect()      # start() raises -> failure callback
            await svc._reconnect()      # already given up -> no second callback
        cb.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
