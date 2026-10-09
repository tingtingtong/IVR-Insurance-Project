"""Due-amount OTP payment and name-on-card vs authenticated name.

Run:  .venv/Scripts/python tests/test_otp_due_and_card_name.py
"""
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from core.graph.nodes.otp import (
    otp_node,
    _due_and_premium,
    _authenticated_name,
    _spoken_full_name,
    _card_name_prompt,
)


def _state(step, utterance, otp_data=None, **extra):
    st = {
        "call_sid": "CA_DUE",
        "authenticated": True,
        "caller_persona": "insured",
        "caller_name": "John Smith",
        "auth_step": "complete",
        "customer": {"policyNumber": "P300123456", "firstName": "John", "lastName": "Smith"},
        "access_token": "tok",
        "otp_step": step,
        "otp_data": dict(otp_data or {}),
        "active_flow": "otp",
        "messages": [HumanMessage(content=utterance)] if utterance else [],
    }
    st.update(extra)
    return st


class DueAmountHelpers(unittest.TestCase):
    def test_amount_due_field_wins(self):
        due, premium = _due_and_premium({"AmountDue": "80.00", "PremiumAmount": "125.50"})
        self.assertEqual(due, 80.0)
        self.assertEqual(premium, 125.50)

    def test_past_paid_to_date_uses_premium(self):
        due, premium = _due_and_premium({
            "PremiumAmount": "125.50",
            "PaidToDate": "2020-01-01",
        })
        self.assertEqual(due, 125.50)
        self.assertEqual(premium, 125.50)

    def test_future_paid_to_date_is_zero_due(self):
        due, premium = _due_and_premium({
            "PremiumAmount": "75.00",
            "PaidToDate": "2099-01-01",
        })
        self.assertEqual(due, 0.0)
        self.assertEqual(premium, 75.00)

    def test_auth_name_prefers_caller_name(self):
        self.assertEqual(_authenticated_name({
            "caller_name": "john smith",
            "customer": {"firstName": "Jane", "lastName": "Doe"},
        }), "John Smith")

    def test_spoken_full_name(self):
        self.assertEqual(_spoken_full_name("Jane Doe"), "Jane Doe")
        self.assertEqual(_spoken_full_name("my name is Jane Doe"), "Jane Doe")
        self.assertEqual(_spoken_full_name("yes"), "")


class DueAmountFlowTests(unittest.IsolatedAsyncioTestCase):
    @patch("core.graph.nodes.otp.holding_inquiry", new_callable=AsyncMock)
    async def test_start_quotes_due_amount(self, holding):
        holding.return_value = {
            "success": True,
            "data": {"AmountDue": "125.50", "PremiumAmount": "125.50", "PaidToDate": "2026-07-01"},
        }
        result = await otp_node(_state("start", "I want to make a payment"))
        self.assertEqual(result["otp_step"], "confirming_due_amount")
        self.assertIn("125.50", result["tts_text"])
        self.assertIn("May we proceed", result["tts_text"])
        self.assertEqual(result["otp_data"]["due_amount"], 125.50)

    @patch("core.graph.nodes.otp.holding_inquiry", new_callable=AsyncMock)
    async def test_blank_otp_step_from_stream_session_starts_payment(self, holding):
        """#96: the stream path seeds otp_step="" (session init); it must act like "start"."""
        holding.return_value = {
            "success": True,
            "data": {"AmountDue": "125.50", "PremiumAmount": "125.50", "PaidToDate": "2026-07-01"},
        }
        result = await otp_node(_state("", "I want to do a one time payment."))
        self.assertEqual(result["otp_step"], "confirming_due_amount")
        self.assertIn("May we proceed", result["tts_text"])
        self.assertNotIn("anything else", result["tts_text"].lower())

    @patch("core.graph.nodes.otp.holding_inquiry", new_callable=AsyncMock)
    async def test_start_no_due_offers_premium(self, holding):
        holding.return_value = {
            "success": True,
            "data": {"AmountDue": "0.00", "PremiumAmount": "75.00", "PaidToDate": "2027-06-01"},
        }
        result = await otp_node(_state("start", "pay my bill"))
        self.assertEqual(result["otp_step"], "confirming_due_amount")
        self.assertIn("no premium due", result["tts_text"].lower())
        self.assertIn("75.00", result["tts_text"])

    async def test_yes_locks_due_and_asks_method(self):
        result = await otp_node(_state("confirming_due_amount", "Yes", {
            "due_amount": 125.50, "premium_amount": 125.50, "quoted_amount": 125.50,
        }))
        self.assertEqual(result["otp_step"], "choosing_method")
        self.assertEqual(result["otp_data"]["amount"], 125.50)
        self.assertIn("card or bank", result["tts_text"].lower())

    async def test_no_on_due_asks_custom_amount(self):
        result = await otp_node(_state("confirming_due_amount", "No, a different amount", {
            "due_amount": 125.50, "quoted_amount": 125.50,
        }))
        self.assertEqual(result["otp_step"], "collecting_custom_amount")
        self.assertIn("How much would you like to pay", result["tts_text"])

    async def test_spoken_amount_on_due_confirm(self):
        result = await otp_node(_state("confirming_due_amount", "I want to pay 50 dollars", {
            "due_amount": 125.50, "quoted_amount": 125.50,
        }))
        self.assertEqual(result["otp_step"], "confirming_custom_amount")
        self.assertEqual(result["otp_data"]["amount"], 50.0)
        self.assertIn("50.00", result["tts_text"])

    async def test_custom_amount_confirm_yes_goes_to_method(self):
        result = await otp_node(_state("confirming_custom_amount", "Yes", {
            "due_amount": 125.50, "amount": 50.0,
        }))
        self.assertEqual(result["otp_step"], "choosing_method")
        self.assertEqual(result["otp_data"]["amount"], 50.0)

    async def test_no_due_no_exits(self):
        result = await otp_node(_state("confirming_due_amount", "No", {
            "due_amount": 0.0, "premium_amount": 75.0, "quoted_amount": 75.0,
        }))
        self.assertEqual(result["otp_step"], "start")
        self.assertEqual(result["active_flow"], "")
        self.assertIn("anything else", result["tts_text"].lower())

    @patch("core.graph.nodes.otp.holding_inquiry", new_callable=AsyncMock)
    async def test_holding_failure_transfers(self, holding):
        holding.return_value = {"success": False, "data": {}, "error": "down"}
        result = await otp_node(_state("start", "pay"))
        self.assertEqual(result["current_node"], "escalation")
        self.assertEqual(result["current_intent"], "escalate")


class CardNameFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_method_asks_name_on_card(self):
        result = await otp_node(_state("choosing_method", "card", {
            "amount": 125.50, "due_amount": 125.50,
        }))
        self.assertEqual(result["otp_step"], "confirming_card_name")
        self.assertIn("John Smith", result["tts_text"])
        self.assertIn("same as it appears on the card", result["tts_text"])
        self.assertIn("full name", result["tts_text"])

    async def test_name_yes_collects_card_number(self):
        result = await otp_node(_state("confirming_card_name", "Yes", {
            "amount": 125.50, "payment_type": "card", "authenticated_name": "John Smith",
        }))
        self.assertEqual(result["otp_step"], "collecting_card_dtmf")
        self.assertEqual(result["otp_data"]["cardholder_name"], "John Smith")

    async def test_different_name_spoken_inline(self):
        result = await otp_node(_state("confirming_card_name", "Jane Doe", {
            "amount": 125.50, "payment_type": "card", "authenticated_name": "John Smith",
        }))
        self.assertEqual(result["otp_step"], "collecting_card_dtmf")
        self.assertEqual(result["otp_data"]["cardholder_name"], "Jane Doe")

    async def test_no_then_collect_full_name(self):
        result = await otp_node(_state("confirming_card_name", "No", {
            "amount": 125.50, "payment_type": "card", "authenticated_name": "John Smith",
        }))
        self.assertEqual(result["otp_step"], "collecting_cardholder_name")
        self.assertIn("full name as it appears on the card", result["tts_text"])

        result2 = await otp_node(_state("collecting_cardholder_name", "Mary Johnson", result["otp_data"]))
        self.assertEqual(result2["otp_step"], "collecting_card_dtmf")
        self.assertEqual(result2["otp_data"]["cardholder_name"], "Mary Johnson")

    async def test_prepaid_unnamed_escalates(self):
        result = await otp_node(_state(
            "confirming_card_name",
            "card does not have a name",
            {"amount": 125.50, "payment_type": "card", "authenticated_name": "John Smith"},
        ))
        self.assertEqual(result["current_node"], "escalation")
        self.assertIn("prepaid", result["tts_text"].lower())

    @patch("core.graph.nodes.otp._process_payment", new_callable=AsyncMock)
    async def test_cvv_yes_uses_locked_due_amount(self, process):
        process.return_value = {"success": True, "confirmation": "CNF1", "payment_id": "PAY-1"}
        result = await otp_node(_state("confirming_cvv", "Yes", {
            "payment_type": "card",
            "amount": 125.50,
            "card_number": "4111111111111111",
            "expiry": "07/32",
            "cvv": "543",
        }))
        self.assertEqual(result["otp_step"], "complete")
        self.assertNotIn("How much would you like to pay", result["tts_text"])
        process.assert_awaited()
        kwargs = process.await_args.args[0]
        self.assertEqual(kwargs["amount"], 125.50)

    def test_card_name_prompt_mentions_auth_name(self):
        tts = _card_name_prompt("John Smith")
        self.assertIn("John Smith", tts)
        self.assertIn("If not, please say the full name", tts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
