"""DOB-fail name fallback must not ask for the caller name a second time.

Run:  .venv/Scripts/python tests/test_skip_second_name_ask.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from core.graph.nodes.auth import _auth_complete, _collecting_name


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


class SkipSecondNameAskTests(unittest.IsolatedAsyncioTestCase):
    def test_auth_complete_reuses_insured_name(self):
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
        result = _auth_complete(_JOHN_PARTY, {
            "phoneNumber": "5551234567",
            "dateOfBirth": "1965-07-15",
        })
        self.assertEqual(result["auth_step"], "collecting_caller_name")
        self.assertEqual(result["caller_persona"], "")
        self.assertIn("May I ask your name please", result["tts_text"])

    async def test_collecting_name_success_skips_persona_ask(self):
        state = {
            "call_sid": "CA_DOB_FAIL_NAME",
            "slot_attempts": {},
            "messages": [HumanMessage(content="John Smith")],
        }
        result = await _collecting_name(
            state,
            "John Smith",
            {"phoneNumber": "5551234567"},
            0,
            _JOHN_PARTY,
        )
        self.assertEqual(result["auth_step"], "complete")
        self.assertEqual(result["caller_persona"], "insured")
        self.assertEqual(result["caller_name"], "John Smith")
        self.assertNotIn("May I ask your name", result["tts_text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
