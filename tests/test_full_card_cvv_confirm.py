"""Full 16-digit card confirm, 3-digit CVV confirm, no persistence of PAN/CVV.

Run:  .venv/Scripts/python tests/test_full_card_cvv_confirm.py
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from utils.payment_validator import validate_cvv, try_trim_extra_digits, luhn_check
from utils.pii_redactor import redact, redact_turn
from core.graph.nodes.otp import (
    otp_node,
    _confirm_card_tts,
    _confirm_cvv_tts,
    _spell_card_number,
    _validate_card_with_feedback,
)


class ValidatorTests(unittest.TestCase):
    def test_cvv_must_be_exactly_three(self):
        self.assertTrue(validate_cvv("543")[0])
        self.assertFalse(validate_cvv("5432")[0])
        self.assertFalse(validate_cvv("54")[0])

    def test_silent_trim_does_not_require_caller_fault(self):
        card = "4111111111111111"
        self.assertTrue(luhn_check(card))
        self.assertEqual(try_trim_extra_digits(card + "1", 16, checksum_fn=luhn_check), card)


class ConfirmSpeechTests(unittest.TestCase):
    def test_card_confirm_reads_all_sixteen(self):
        tts = _confirm_card_tts("4111111111111111")
        self.assertIn("Let me confirm the card number I heard", tts)
        self.assertNotIn("ending in", tts)
        self.assertNotIn("17 digits", tts)
        self.assertEqual(_spell_card_number("4111111111111111").count(","), 12)  # 16 digits, 12 commas in 4 groups of 3

    def test_cvv_confirm_reads_three_digits(self):
        tts = _confirm_cvv_tts("543")
        self.assertIn("5, 4, 3", tts)
        self.assertIn("Is that correct?", tts)


class RedactionTests(unittest.TestCase):
    def test_bot_card_confirm_redacted(self):
        tts = _confirm_card_tts("4111111111111111")
        stored = redact_turn("bot", tts, node="otp")
        self.assertIn("[CARD REDACTED]", stored)
        self.assertNotIn("4111", stored)

    def test_bot_cvv_confirm_redacted(self):
        tts = _confirm_cvv_tts("543")
        stored = redact_turn("bot", tts, node="otp")
        self.assertIn("[CVV REDACTED]", stored)
        self.assertNotIn("5, 4, 3", stored)

    def test_source_never_blames_caller_for_extra_digits(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "core", "graph", "nodes", "otp.py"), encoding="utf-8").read()
        self.assertNotIn("I received 17 digits", src)
        self.assertNotIn("I received {len(digits)} digits", src)


class SilentTrimTests(unittest.TestCase):
    def test_extra_digit_becomes_full_confirm_not_count_message(self):
        otp_data = {"payment_type": "card"}
        result = _validate_card_with_feedback("41111111111111111", otp_data)
        self.assertIsNone(result)
        self.assertEqual(otp_data["card_number"], "4111111111111111")


class OtpFlowTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, step, utterance, otp_data):
        return {
            "call_sid": "CA_CARD",
            "authenticated": True,
            "caller_persona": "insured",
            "auth_step": "complete",
            "customer": {"policyNumber": "P300123456"},
            "access_token": "tok",
            "otp_step": step,
            "otp_data": dict(otp_data),
            "active_flow": "otp",
            "messages": [HumanMessage(content=utterance)],
        }

    async def test_valid_card_confirms_full_number(self):
        state = self._state("dtmf_complete", "4111111111111111", {
            "payment_type": "card",
            "card_number": "4111111111111111",
        })
        result = await otp_node(state)
        self.assertEqual(result["otp_step"], "confirming_card")
        self.assertIn("Let me confirm the card number I heard", result["tts_text"])
        self.assertNotIn("ending in", result["tts_text"])

    async def test_cvv_four_digits_reasked_as_three(self):
        state = self._state("collecting_card_cvv", "5432", {"payment_type": "card"})
        result = await otp_node(state)
        self.assertEqual(result["otp_step"], "collecting_card_cvv")
        self.assertIn("3-digit", result["tts_text"])

    async def test_cvv_three_digits_confirmed_in_full(self):
        state = self._state("collecting_card_cvv", "543", {"payment_type": "card"})
        result = await otp_node(state)
        self.assertEqual(result["otp_step"], "confirming_cvv")
        self.assertIn("5, 4, 3", result["tts_text"])
        self.assertEqual(result["otp_data"]["cvv"], "543")


if __name__ == "__main__":
    unittest.main(verbosity=2)
