"""Name capture (labeled + NATO) and escalate-must-hangup.

Run:  .venv/Scripts/python tests/test_name_and_escalate.py
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from utils.name_extractor import decode_phonetics, parse_name_deterministic, extract_name
from core.graph.nodes.otp import _validate_card_with_feedback, MAX_CARD_RETRIES
from webhooks.twilio_voice import _is_terminal, _hangup_response


class PhoneticTests(unittest.TestCase):
    def test_a_for_alpha(self):
        self.assertIn("A", decode_phonetics("A for Alpha"))
        self.assertIn("B", decode_phonetics("B as in Bravo"))

    def test_spelled_john(self):
        decoded = decode_phonetics(
            "J for Juliet O for Oscar H for Hotel N for November"
        )
        self.assertEqual(decoded.replace(" ", "").upper(), "JOHN")


class DeterministicNameTests(unittest.TestCase):
    def test_bare_two_words(self):
        self.assertEqual(parse_name_deterministic("John Smith"), ("John", "Smith"))

    def test_my_name_is(self):
        self.assertEqual(parse_name_deterministic("My name is John Smith"), ("John", "Smith"))

    def test_first_and_last_labeled(self):
        self.assertEqual(
            parse_name_deterministic("My first name is John, last name is Smith"),
            ("John", "Smith"),
        )

    def test_last_then_first_labeled(self):
        self.assertEqual(
            parse_name_deterministic("Last name is Smith, first name is John"),
            ("John", "Smith"),
        )

    def test_i_am(self):
        self.assertEqual(parse_name_deterministic("I am Jane Doe"), ("Jane", "Doe"))


class ExtractNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_labeled_does_not_need_llm(self):
        first, last = await extract_name("My first name is John, last name is Smith")
        self.assertEqual((first, last), ("John", "Smith"))

    async def test_nato_first_name(self):
        first, last = await extract_name(
            "J for Juliet, O for Oscar, H for Hotel, N for November"
        )
        self.assertEqual(first.upper(), "JOHN")


class CallerNameNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_labeled_name_matches_insured(self):
        from core.graph.nodes.auth import _collecting_caller_name
        state = {
            "call_sid": "CA_NAME",
            "finalized_party": {
                "Personas": [{"name": "John Smith", "role": "insured"}],
            },
            "slot_attempts": {},
            "messages": [HumanMessage(content="My first name is John, last name is Smith")],
        }
        result = await _collecting_caller_name(
            state, "My first name is John, last name is Smith"
        )
        self.assertEqual(result["caller_persona"], "insured")
        self.assertEqual(result["caller_name"], "John Smith")
        self.assertEqual(result["auth_step"], "complete")


class EscalateHangupTests(unittest.TestCase):
    def test_card_max_retries_sets_transfer_terminal(self):
        otp_data = {"card_retry_count": MAX_CARD_RETRIES, "payment_type": "card"}
        result = _validate_card_with_feedback("1234", otp_data)
        self.assertIsNotNone(result)
        self.assertEqual(result["current_node"], "escalation")
        self.assertEqual(result["current_intent"], "escalate")
        self.assertTrue(_is_terminal(result))
        self.assertIn("transfer you to a representative", result["tts_text"])

    def test_escalate_without_agent_number_still_hangs_up(self):
        twiml = _hangup_response("Let me transfer you.", "").body.decode()
        self.assertIn("<Hangup", twiml)
        self.assertNotIn("<Gather", twiml)

    def test_is_terminal_on_intent_alone(self):
        self.assertTrue(_is_terminal({"current_intent": "escalate", "current_node": "otp"}))
        self.assertFalse(_is_terminal({"current_intent": "otp", "current_node": "otp"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
