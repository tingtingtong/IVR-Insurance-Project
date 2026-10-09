"""#108: card/CVV survive the stream handler's per-turn Redis round trip (which masks them)."""
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_stream as ts
from services.session import SessionService

CARD = "4111111111111111"


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def setex(self, key, ttl, value):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


class FakeWS:
    def __init__(self):
        self.close = AsyncMock()
        self.send_text = AsyncMock()


class SensitiveOtpStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        p = patch.object(SessionService, "_get_redis", new=AsyncMock(return_value=self.redis))
        p.start()
        self.addCleanup(p.stop)

    async def _handler(self):
        with patch.object(ts, "TTSService"), patch.object(ts, "VADService"):
            h = ts.CallHandler(FakeWS())
        h.call_sid = "CA_SENS1"
        h._speak = AsyncMock()
        await h.session.init_session(h.call_sid)
        return h

    async def _turn(self, h, graph_result):
        """One graph turn: load state like _process_turn does, run the (mock) graph."""
        seen = {}
        graph = MagicMock()

        async def ainvoke(state, config=None):
            seen["otp_data"] = dict(state.get("otp_data") or {})
            return graph_result
        graph.ainvoke = ainvoke
        state = await h.session.get_state(h.call_sid)
        seen["redis_otp_data"] = dict(state.get("otp_data") or {})
        with patch.object(ts._graph_module, "cno_graph", graph), \
             patch.object(ts, "update_call_metadata"), patch.object(ts, "add_call_turn"):
            await h._invoke_graph_and_respond(state)
        return seen

    async def test_card_and_cvv_reach_the_payment_turn_unmasked(self):
        h = await self._handler()
        # turn 1: card collected by keypad and read back (full digits exist only in memory)
        await self._turn(h, {"tts_text": "confirm", "otp_step": "confirming_card",
                             "otp_data": {"card_number": CARD, "payment_type": "card"}})
        # turn 2: expiry; Redis now holds the masked card, the graph must still get the full one
        seen = await self._turn(h, {"tts_text": "cvv?", "otp_step": "collecting_card_cvv",
                                    "otp_data": {"card_number": CARD, "expiry": "0628"}})
        self.assertEqual(seen["redis_otp_data"]["card_number"], "****1111")   # the masking is real
        self.assertEqual(seen["otp_data"]["card_number"], CARD)               # and is undone for the graph
        # turn 3: CVV said; both card and CVV must arrive intact
        await self._turn(h, {"tts_text": "confirm cvv", "otp_step": "confirming_cvv",
                             "otp_data": {"card_number": CARD, "expiry": "0628", "cvv": "225"}})
        seen = await self._turn(h, {"tts_text": "paid", "otp_step": "complete",
                                    "otp_data": {"card_number": "", "cvv": ""}})
        self.assertEqual(seen["otp_data"]["card_number"], CARD)
        self.assertEqual(seen["otp_data"]["cvv"], "225")

    async def test_values_cleared_after_the_payment_are_forgotten(self):
        h = await self._handler()
        await self._turn(h, {"tts_text": "x", "otp_data": {"card_number": CARD, "cvv": "225"}})
        await self._turn(h, {"tts_text": "paid", "otp_data": {"card_number": "", "cvv": ""}})
        self.assertEqual(h._sensitive_otp, {})
        seen = await self._turn(h, {"tts_text": "anything else?", "otp_data": {}})
        self.assertNotIn(CARD, str(seen["otp_data"]))

    async def test_a_card_the_caller_rejected_is_not_resurrected(self):
        h = await self._handler()
        await self._turn(h, {"tts_text": "confirm", "otp_data": {"card_number": CARD}})
        # caller says "no": the node pops card_number
        await self._turn(h, {"tts_text": "enter again", "otp_data": {"payment_type": "card"}})
        seen = await self._turn(h, {"tts_text": "x", "otp_data": {"payment_type": "card"}})
        self.assertNotIn("card_number", seen["otp_data"])

    async def test_a_newly_entered_card_beats_the_remembered_one(self):
        h = await self._handler()
        await self._turn(h, {"tts_text": "confirm", "otp_data": {"card_number": CARD}})
        state = await h.session.get_state(h.call_sid)
        new_card = "5500005555555559"
        state["otp_data"] = {**state.get("otp_data", {}), "card_number": new_card}   # as _on_dtmf_complete does
        h._restore_sensitive_otp(state)
        self.assertEqual(state["otp_data"]["card_number"], new_card)

    async def test_nothing_sensitive_is_written_to_redis(self):
        h = await self._handler()
        await self._turn(h, {"tts_text": "x", "otp_data": {"card_number": CARD, "cvv": "225"}})
        stored = " ".join(self.redis.data.values())
        self.assertNotIn(CARD, stored)
        self.assertNotIn('"225"', stored)

    async def test_memory_is_cleared_when_the_call_ends(self):
        h = await self._handler()
        await self._turn(h, {"tts_text": "x", "otp_data": {"card_number": CARD}})
        await h._cleanup()
        self.assertEqual(h._sensitive_otp, {})


if __name__ == "__main__":
    unittest.main()
