"""Post-payment confirmation, repeat, and goodbye handling.

Run:  .venv/Scripts/python tests/test_otp_post_payment.py
"""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage
from core.graph.nodes.otp import (
    otp_node,
    _build_payment_result,
    _spell_alphanumeric,
    _wants_repeat_confirmation,
    _is_done_after_payment,
    _wants_post_payment_repeat,
)
from core.graph.nodes.router import _caller_wants_goodbye, router_node

_REPEAT_OFFER = AIMessage(content=(
    "Your card payment of $125.50 has been processed successfully. "
    "Would you like me to repeat those numbers, or is there anything else I can help you with today?"
))


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
        "active_flow": "",  # released after payment (#42)
        "messages": [_REPEAT_OFFER, HumanMessage(content=utterance)],
        "tts_text": "",
        "current_intent": "otp",
        "current_node": "otp",
        "pending_intents": [],
    }


def test_slow_spell():
    spoken = _spell_alphanumeric("CNF75521900", slow=True)
    assert "seven" in spoken, spoken
    assert ", " in spoken, spoken  # #42: commas force TTS pauses
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
    assert result["active_flow"] == ""  # #42: flow released after payment
    assert "CNF" not in result["otp_data"]["last_confirmation_tts"] or "C." in result["otp_data"]["last_confirmation_tts"]
    assert result["tts_text"] != ""
    print("  PASS  payment success offers repeat + anything else")


async def test_complete_paths():
    r = await otp_node(_paid_state("I want to know the confirmation number"))
    assert "Let me repeat that slowly" in r["tts_text"]
    assert r["otp_step"] == "complete"
    assert r["active_flow"] == ""  # #59: never re-lock the caller in OTP
    print("  PASS  complete + confirmation number repeats")

    r = await otp_node(_paid_state("Yes."))
    assert "Let me repeat that slowly" in r["tts_text"]
    print("  PASS  complete + yes repeats the numbers")

    r = await otp_node(_paid_state("Thank you."))
    assert r["current_node"] == "goodbye"
    assert "Goodbye" in r["tts_text"]
    print("  PASS  complete + thank you hangs up")

    holding = AsyncMock(return_value={"success": True, "data": {}})
    with patch("core.graph.nodes.otp.holding_inquiry", holding):
        r = await otp_node(_paid_state("I'd like to pay on my insurance"))
        assert "Let me repeat" not in r["tts_text"], "substring 'sure' in 'insurance' must not count as yes"
        r = await otp_node(_paid_state("I want to make another payment"))
    assert r["otp_step"] == "confirming_due_amount", r
    assert "last_confirmation_tts" not in r["otp_data"]
    print("  PASS  complete + new payment request restarts the payment flow (#59)")


def test_post_payment_repeat_words():
    assert _wants_post_payment_repeat("Yes.")
    assert _wants_post_payment_repeat("yes please")
    assert _wants_post_payment_repeat("can you repeat that")
    assert not _wants_post_payment_repeat("I want to make another payment on my insurance")
    assert not _wants_post_payment_repeat("look up my loan")
    print("  PASS  post-payment repeat uses whole words")


async def test_router_routes_repeat_back_to_otp():
    for u in ("Yes.", "please repeat that", "I want to know the confirmation number"):
        r = await router_node(_paid_state(u))
        assert r.get("current_intent") == "otp", (u, r)
    # Without the bot's repeat offer, "yes" must not be hijacked
    state = _paid_state("Yes.")
    state["messages"] = [AIMessage(content="Your policy is active."), HumanMessage(content="Yes.")]
    from core.graph.nodes.router import _is_post_payment_repeat
    assert not _is_post_payment_repeat(state, state["messages"], "Yes.")
    print("  PASS  router sends repeat requests after payment back to OTP (#59)")


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
    test_post_payment_repeat_words()
    await test_complete_paths()
    await test_router_routes_repeat_back_to_otp()
    await test_router_thank_you_bypasses_otp_lock()
    print("\nAll post-payment tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
