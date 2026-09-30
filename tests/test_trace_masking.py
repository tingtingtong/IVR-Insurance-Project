"""#55: LangSmith traces must never carry card numbers, CVV, tokens or caller identity."""
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage

from utils.trace_masking import mask_pii, install_masked_langsmith_client

CARD = "4111111111111111"
CVV = "543"
TOKEN = "mock-access-token-PKY123456-9876543210"
DOB = "1965-01-15"
PHONE = "3175551234"
STREET = "742 Maple Ave"


def _flagged_state():
    """Shape of a real LangGraph output flagged by the pii_leakage evaluator."""
    return {
        "call_sid": "CA735e5cdefad5e50ac9dbb09600c021e5",
        "current_intent": "otp",
        "access_token": TOKEN,
        "caller_name": "John Smith",
        "customer": {"firstName": "John", "lastName": "Smith", "phoneNumber": PHONE},
        "finalized_party": {
            "FirstName": "John", "LastName": "Smith", "DOB": DOB,
            "Addresses": [{"Street": STREET, "City": "Indianapolis", "Zip": "46204"}],
            "PhoneNumbers": [{"PhoneNumber": PHONE}],
        },
        "pii_collected": {"phoneNumber": PHONE},
        "otp_data": {
            "amount": "125.50", "card_number": CARD, "cvv": CVV, "expiry": "08/29",
            "account_number": "123456789", "routing_number": "021000021",
            "last_confirmation_tts": "Your confirmation number is, C, N, F, 7, 5, 3",
        },
        "messages": [
            HumanMessage(content="i want to make a payment"),
            HumanMessage(content=CARD),
            HumanMessage(content="4 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1"),
            AIMessage(content="The security code I heard is 5, 4, 3. Is that correct?"),
            {"type": "human", "kwargs": {"content": f"my card is {CARD}"}},
        ],
    }


def _dump(value) -> str:
    return json.dumps(value, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))


class TraceMaskingTests(unittest.TestCase):
    def test_flagged_state_has_no_pii_after_masking(self):
        out = _dump(mask_pii(_flagged_state()))
        for secret in (CARD, TOKEN, DOB, PHONE, STREET, "John", "Smith", "08/29",
                       "123456789", "021000021", "CNF", "5, 4, 3", "4 1 1 1 1"):
            self.assertNotIn(secret, out, f"{secret!r} leaked into trace payload")

    def test_non_sensitive_fields_survive(self):
        out = mask_pii(_flagged_state())
        self.assertEqual(out["call_sid"], "CA735e5cdefad5e50ac9dbb09600c021e5")
        self.assertEqual(out["current_intent"], "otp")
        self.assertEqual(out["messages"][0].content, "i want to make a payment")

    def test_messages_stay_langchain_messages(self):
        out = mask_pii(_flagged_state())
        self.assertIsInstance(out["messages"][1], HumanMessage)
        self.assertIsInstance(out["messages"][3], AIMessage)
        self.assertEqual(out["messages"][1].content, "[CARD REDACTED]")

    def test_input_is_not_mutated(self):
        state = _flagged_state()
        mask_pii(state)
        self.assertEqual(state["access_token"], TOKEN)
        self.assertEqual(state["messages"][1].content, CARD)

    def test_installed_client_is_used_by_langchain_tracer(self):
        from langchain_core.tracers.langchain import get_client
        import langsmith.run_trees as run_trees

        old = run_trees._CLIENT
        os.environ.setdefault("LANGCHAIN_API_KEY", "test-key")
        try:
            install_masked_langsmith_client()
            client = get_client()
            masked_in = client._hide_run_inputs({"access_token": TOKEN, "text": CARD})
            masked_out = client._hide_run_outputs(_flagged_state())
            self.assertNotIn(TOKEN, _dump(masked_in))
            self.assertNotIn(CARD, _dump(masked_in))
            self.assertNotIn(CARD, _dump(masked_out))
        finally:
            run_trees._CLIENT = old

    def test_stream_transcript_log_is_redacted(self):
        src = (ROOT / "webhooks" / "twilio_stream.py").read_text(encoding="utf-8")
        self.assertIn('log.info("transcript", call_sid=self.call_sid, text=redact(text))', src)
        self.assertNotIn('log.info("transcript", call_sid=self.call_sid, text=text)', src)


if __name__ == "__main__":
    unittest.main()
