"""Post-payment confirmation, repeat, and goodbye handling.

Run:  .venv/Scripts/python tests/test_otp_post_payment.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import HumanMessage
from core.graph.nodes.otp import (
    otp_node,
    _build_payment_result,
    _spell_alphanumeric,
    _wants_repeat_confirmation,
    _is_done_after_payment,
)
from core.graph.nodes.router import _caller_wants_goodbye, router_node


def _paid_state(utterance: str) -> dict:
    return {
        "call_sid": "CA_TEST_PAYMENT",
        "authenticated": True,
        "caller_persona": "insured",
        "auth_step": "complete",
        "customer": {"policyNumber": "P300123456"},
        "access_token": "tok",
        "otp_step": "complete",
        "otp_data": {
            "last_confirmation_tts": (
                "Your confirmation number is C. N. F. seven. five. "
                "Your payment reference ID is P. A. Y. dash. two."
            )
        },
        "active_flow": "otp",
        "messages": [HumanMessage(content=utterance)],
        "tts_text": "",
        "current_intent": "otp",
        "current_node": "otp",
        "pending_intents": [],
    }


def test_slow_spell():
    spoken = _spell_alphanumeric("CNF75521900", slow=True)
    assert "seven" in spoken, spoken
    assert ". " in spoken, spoken
    assert spoken.endswith("."), spoken
    fast = _spell_alphanumeric("CNF75521900")
    assert "seven" not in fast
    print("  PASS  slow alphanumeric spelling")


def test_repeat_detection():
    assert _wants_repeat_confirmation("I want to know the confirmation number")
    assert _wants_repeat_confirmation("please repeat that")
    assert _wants_repeat_confirmation("what is the reference ID")
    assert not _wants_repeat_confirmation("thank you")
    print("  PASS  repeat-confirmation detection")


def test_done_after_payment():
    assert _is_done_after_payment("Thank you.")
    assert _is_done_after_payment("thanks")
    assert _is_done_after_payment("no")
    assert _is_done_after_payment("nothing else")
    assert not _is_done_after_payment("I want the confirmation number")
    print("  PASS  thank-you / nothing-else after payment")


def test_goodbye_keyword():
    assert _caller_wants_goodbye("thank you")
    assert _caller_wants_goodbye("Thanks.")
    assert _caller_wants_goodbye("thank you so much")
    assert not _caller_wants_goodbye("thank you for the policy number")
    print("  PASS  router treats bare thank-you as goodbye")


def test_payment_result_offers_repeat():
    result = _build_payment_result(
        {"success": True, "confirmation": "CNF75521900", "payment_id": "PAY-20260918-9A0MJJ"},
        {},
    )
    tts = result["tts_text"]
    assert "Would you like me to repeat those numbers" in tts
    assert "anything else I can help you with today" in tts
    assert result["otp_step"] == "complete"
    assert result["active_flow"] == "otp"
    assert "CNF" not in result["otp_data"]["last_confirmation_tts"] or "C." in result["otp_data"]["last_confirmation_tts"]
    assert result["tts_text"] != ""
    print("  PASS  payment success offers repeat + anything else")


async def test_complete_paths():
    r = await otp_node(_paid_state("I want to know the confirmation number"))
    assert "Let me repeat that slowly" in r["tts_text"]
    assert r["otp_step"] == "complete"
    assert r["active_flow"] == "otp"
    print("  PASS  complete + confirmation number repeats")

    r = await otp_node(_paid_state("Yes."))
    assert "Let me repeat that slowly" in r["tts_text"]
    print("  PASS  complete + yes repeats the numbers")

    r = await otp_node(_paid_state("Thank you."))
    assert r["current_node"] == "goodbye"
    assert "Goodbye" in r["tts_text"]
    print("  PASS  complete + thank you hangs up")

    r = await otp_node(_paid_state("policy status"))
    assert r["tts_text"]
    assert "anything else" in r["tts_text"].lower()
    assert r["otp_data"].get("last_confirmation_tts")
    print("  PASS  complete + other utterance keeps numbers and asks anything else")


async def test_router_thank_you_bypasses_otp_lock():
    state = _paid_state("Thank you.")
    result = await router_node(state)
    assert result.get("current_intent") == "goodbye", result
    print("  PASS  router goodbye bypasses OTP lock on thank you")


async def main():
    print("\n--- Post-payment confirmation ---")
    test_slow_spell()
    test_repeat_detection()
    test_done_after_payment()
    test_goodbye_keyword()
    test_payment_result_offers_repeat()
    await test_complete_paths()
    await test_router_thank_you_bypasses_otp_lock()
    print("\nAll post-payment tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
