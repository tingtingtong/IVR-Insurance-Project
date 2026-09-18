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

    def test_fillers_around_my_name_is(self):
        # Call CA5589887086f1df8c2aee86daa589c75f stored "My Smith"
        self.assertEqual(
            parse_name_deterministic("Uh, my name is uh, John Smith."),
            ("John", "Smith"),
        )
        self.assertEqual(
            parse_name_deterministic("um my name is john smith"),
            ("John", "Smith"),
        )


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

    async def test_uh_my_name_is_uh_john_smith(self):
        from core.graph.nodes.auth import _collecting_caller_name
        uttered = "Uh, my name is uh, John Smith."
        state = {
            "call_sid": "CA_NAME2",
            "finalized_party": {
                "Personas": [{"name": "John Smith", "role": "insured"}],
            },
            "slot_attempts": {},
            "messages": [HumanMessage(content=uttered)],
        }
        result = await _collecting_caller_name(state, uttered)
        self.assertEqual(result["caller_name"], "John Smith")
        self.assertEqual(result["caller_persona"], "insured")
        self.assertNotIn("My Smith", result["caller_name"])


_JOHN_PARTY = {
    "PartyCalrKeyCode": "PKY100001",
    "CompanyCode": "CNO",
    "FirstName": "John",
    "LastName": "Smith",
    "DOB": "1965-07-15",
    "PhoneNumbers": [{"PhoneNumber": "5551234567", "PhoneType": "Home"}],
    "Policies": [{"PolicyNumber": "P300123456", "ProductType": "Whole Life"}],
    "Personas": [
        {"name": "John Smith", "role": "insured"},
        {"name": "Smith Corp", "role": "payor"},
    ],
}


class InsuredNameRetryTests(unittest.IsolatedAsyncioTestCase):
    def _party(self):
        return {
            "PartyCalrKeyCode": "PKY100001",
            "CompanyCode": "CNO",
            "FirstName": "John",
            "LastName": "Smith",
            "DOB": "1965-07-15",
            "PhoneNumbers": [{"PhoneNumber": "5551234567", "PhoneType": "Home"}],
            "Policies": [{"PolicyNumber": "P300123456"}],
            "Personas": [{"name": "John Smith", "role": "insured"}],
        }

    async def test_one_word_stt_does_not_transfer(self):
        from core.graph.nodes.auth import _collecting_name
        result = await _collecting_name(
            {"call_sid": "CA_JOHNSON", "slot_attempts": {}},
            "Johnson.",
            {"phoneNumber": "5551234567"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "collecting_name")
        self.assertNotEqual(result.get("current_node"), "escalation")
        self.assertIn("only heard", result["tts_text"].lower())
        self.assertIn("spell", result["tts_text"].lower())

    async def test_two_word_name_confirms_before_match(self):
        from core.graph.nodes.auth import _collecting_name
        result = await _collecting_name(
            {"call_sid": "CA_JS", "slot_attempts": {}},
            "John Smith",
            {"phoneNumber": "5551234567"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "confirming_name")
        self.assertIn("I heard John Smith", result["tts_text"])
        self.assertIn("Is that correct", result["tts_text"])

    def test_confirm_yes_matching_name_completes(self):
        from core.graph.nodes.auth import _confirming_name
        result = _confirming_name(
            {"call_sid": "CA_YES", "slot_attempts": {}},
            "Yes",
            {"phoneNumber": "5551234567", "firstName": "John", "lastName": "Smith"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "complete")
        self.assertEqual(result["caller_persona"], "insured")
        self.assertNotIn("May I ask your name", result["tts_text"])

    def test_confirm_yes_mismatch_retries_not_transfer(self):
        from core.graph.nodes.auth import _confirming_name
        result = _confirming_name(
            {"call_sid": "CA_MIS", "slot_attempts": {}},
            "Yes",
            {"phoneNumber": "5551234567", "firstName": "Jane", "lastName": "Doe"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "collecting_name")
        self.assertNotEqual(result.get("current_node"), "escalation")
        self.assertIn("spell", result["tts_text"].lower())

    def test_third_name_failure_transfers(self):
        from core.graph.nodes.auth import _name_retry_or_escalate
        state = {"call_sid": "CA_3", "slot_attempts": {"insured_name": {"invalid": 2}}}
        result = _name_retry_or_escalate(
            state, {"phoneNumber": "5551234567"}, self._party(), "retry"
        )
        self.assertEqual(result["current_node"], "escalation")
        self.assertEqual(result["auth_step"], "failed")


class DobConfirmThenNameTests(unittest.TestCase):
    def _party(self):
        return {
            "PartyCalrKeyCode": "PKY100001",
            "CompanyCode": "CNO",
            "FirstName": "John",
            "LastName": "Smith",
            "DOB": "1965-07-15",
            "PhoneNumbers": [{"PhoneNumber": "5551234567", "PhoneType": "Home"}],
            "Policies": [{"PolicyNumber": "P300123456"}],
            "Personas": [{"name": "John Smith", "role": "insured"}],
        }

    def test_wrong_dob_confirms_before_name(self):
        from core.graph.nodes.auth import _collecting_dob
        result = _collecting_dob(
            {"call_sid": "CA_WRONG_DOB", "slot_attempts": {}},
            "16 July 1938",
            {"phoneNumber": "5551234567"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "confirming_dob")
        self.assertIn("I heard", result["tts_text"])
        self.assertIn("Is that correct", result["tts_text"])
        self.assertNotIn("first and last name", result["tts_text"].lower())

    def test_confirm_yes_on_mismatch_then_asks_name(self):
        from core.graph.nodes.auth import _confirming_dob
        result = _confirming_dob(
            {"call_sid": "CA_DOB_YES", "slot_attempts": {}},
            "Yes",
            {"phoneNumber": "5551234567", "dateOfBirth": "1938-07-16"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "collecting_name")
        self.assertIn("wasn't able to verify that date", result["tts_text"].lower())
        self.assertIn("first and last name of the insured", result["tts_text"].lower())

    def test_confirm_no_reasks_dob(self):
        from core.graph.nodes.auth import _confirming_dob
        result = _confirming_dob(
            {"call_sid": "CA_DOB_NO", "slot_attempts": {}},
            "No",
            {"phoneNumber": "5551234567", "dateOfBirth": "1938-07-16"},
            0,
            self._party(),
        )
        self.assertEqual(result["auth_step"], "collecting_dob")
        self.assertNotIn("dateOfBirth", result["pii_collected"])
        self.assertIn("date of birth", result["tts_text"].lower())

    def test_spoken_correction_captures_new_dob(self):
        from core.graph.nodes.auth import _confirming_dob
        result = _confirming_dob(
            {"call_sid": "CA_DOB_FIX", "slot_attempts": {}},
            "No, July 15 1965",
            {"phoneNumber": "5551234567", "dateOfBirth": "1938-07-16"},
            0,
            self._party(),
        )
        self.assertEqual(result["pii_collected"]["dateOfBirth"], "1965-07-15")
        self.assertIn(result["auth_step"], ("complete", "collecting_caller_name"))
        self.assertTrue(result.get("authenticated"))


class SkipSecondNameAskTests(unittest.IsolatedAsyncioTestCase):
    """DOB fail + insured name match must not ask for the name again."""

    def test_auth_complete_reuses_insured_name(self):
        from core.graph.nodes.auth import _auth_complete
        result = _auth_complete(_JOHN_PARTY, {
            "phoneNumber": "5551234567",
            "firstName": "John",
            "lastName": "Smith",
        })
        self.assertEqual(result["auth_step"], "complete")
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["caller_name"], "John Smith")
        self.assertEqual(result["caller_persona"], "insured")
        self.assertIn("I've verified your identity", result["tts_text"])
        self.assertIn("Thank you, John Smith", result["tts_text"])
        self.assertNotIn("May I ask your name", result["tts_text"])

    def test_auth_complete_without_name_still_asks(self):
        from core.graph.nodes.auth import _auth_complete
        result = _auth_complete(_JOHN_PARTY, {
            "phoneNumber": "5551234567",
            "dateOfBirth": "1965-07-15",
        })
        self.assertEqual(result["auth_step"], "collecting_caller_name")
        self.assertEqual(result["caller_persona"], "")
        self.assertIn("May I ask your name please", result["tts_text"])

    async def test_collecting_name_success_skips_persona_ask(self):
        from core.graph.nodes.auth import _collecting_name, _confirming_name
        state = {
            "call_sid": "CA_DOB_FAIL_NAME",
            "slot_attempts": {},
            "messages": [HumanMessage(content="John Smith")],
        }
        heard = await _collecting_name(
            state,
            "John Smith",
            {"phoneNumber": "5551234567"},
            0,
            _JOHN_PARTY,
        )
        self.assertEqual(heard["auth_step"], "confirming_name")
        result = _confirming_name(
            state, "Yes", heard["pii_collected"], 0, _JOHN_PARTY
        )
        self.assertEqual(result["auth_step"], "complete")
        self.assertEqual(result["caller_persona"], "insured")
        self.assertEqual(result["caller_name"], "John Smith")
        self.assertNotIn("May I ask your name", result["tts_text"])


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
