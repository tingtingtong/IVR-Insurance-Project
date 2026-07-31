"""
Unit tests for individual LangGraph nodes.
No phone, no audio, no Twilio. Uses mock state directly.
Run: python tests/test_nodes.py

Requires:
  - .env with OPENAI_API_KEY
  - Mock insuranceCompany API running: python tests/mock_cno_api.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage, AIMessage


def _base_state(**kwargs) -> dict:
    """Return a minimal authenticated state."""
    state = {
        "call_sid":      "test-001",
        "stream_sid":    "test-stream-001",
        "authenticated": True,
        "auth_step":     "complete",
        "auth_attempts": 0,
        "customer": {
            "firstName":   "John",
            "lastName":    "Doe",
            "policyNumber": "P300123456",
            "dateOfBirth": "1978-01-22",
            "phoneNumber": "5551234567",
            "partyKey":    "PARTY001",
            "companyCode": "insuranceCompany",
            "authStatus":  "authenticated",
        },
        "access_token":    "mock-token",
        "finalized_party": {},
        "pii_collected":   {},
        "current_intent":  "",
        "current_node":    "",
        "slot_attempts":   {},
        "tts_text":        "",
        "transfer_to":     "",
        "otp_step":        "",
        "otp_data":        {},
        "metric_data":     {},
        "messages":        [],
    }
    state.update(kwargs)
    return state


async def test_router_node():
    print("\n--- Router Node ---")
    from core.graph.nodes.router import router_node

    state = _base_state(messages=[HumanMessage(content="I want to check my policy")])
    result = await router_node(state)
    intent = result.get("current_intent")
    ok = intent == "policy_info"
    print(f"  {'PASS' if ok else 'FAIL'}  policy intent detected: got '{intent}'")

    state = _base_state(messages=[HumanMessage(content="Can you transfer me to an agent?")])
    result = await router_node(state)
    intent = result.get("current_intent")
    ok = intent == "escalate"
    print(f"  {'PASS' if ok else 'FAIL'}  escalate intent detected: got '{intent}'")

    state = _base_state(messages=[HumanMessage(content="What is my loan balance?")])
    result = await router_node(state)
    intent = result.get("current_intent")
    ok = intent == "loan"
    print(f"  {'PASS' if ok else 'FAIL'}  loan intent detected: got '{intent}'")


async def test_auth_node():
    print("\n--- Auth Node ---")
    from core.graph.nodes.auth import auth_node

    # Unauthenticated state, asking for phone (first ask — no utterance yet)
    state = _base_state(
        authenticated=False,
        auth_step="collecting_phone",
        messages=[],
    )
    result = await auth_node(state)
    tts = result.get("tts_text", "")
    ok = "phone" in tts.lower()
    print(f"  {'PASS' if ok else 'FAIL'}  auth asks for phone: '{tts[:60]}...'")


async def test_policy_node():
    print("\n--- Policy Node ---")
    from core.graph.nodes.policy import policy_node

    state = _base_state(messages=[HumanMessage(content="What is my policy status?")])
    result = await policy_node(state)
    tts = result.get("tts_text", "")
    ok = bool(tts)
    print(f"  {'PASS' if ok else 'FAIL'}  policy node returned response")
    print(f"         tts: {tts[:100]}")


async def test_payment_node():
    print("\n--- Payment Node ---")
    from core.graph.nodes.payment import payment_node

    state = _base_state(messages=[HumanMessage(content="What are my recent payments?")])
    result = await payment_node(state)
    tts = result.get("tts_text", "")
    ok = bool(tts)
    print(f"  {'PASS' if ok else 'FAIL'}  payment node returned response")
    # Check GROUP G4 disclosure is included
    has_disclosure = "24" in tts or "48" in tts or "business days" in tts.lower()
    print(f"  {'PASS' if has_disclosure else 'FAIL'}  GROUP G4 payment disclosure included")
    print(f"         tts: {tts[:120]}")


async def test_escalation_node():
    print("\n--- Escalation Node ---")
    from core.graph.nodes.escalation import escalation_node

    state = _base_state()
    result = await escalation_node(state)
    has_transfer = bool(result.get("transfer_to"))
    tts = result.get("tts_text", "")
    print(f"  {'PASS' if has_transfer else 'FAIL'}  escalation sets transfer_to: '{result.get('transfer_to')}'")
    print(f"         tts: {tts[:80]}")


async def test_faq_node():
    print("\n--- FAQ Node ---")
    from core.graph.nodes.faq import faq_node

    state = _base_state(messages=[HumanMessage(content="What is Colonial Penn's free look period?")])
    result = await faq_node(state)
    tts = result.get("tts_text", "")
    ok = bool(tts)
    print(f"  {'PASS' if ok else 'FAIL'}  faq node returned response")
    print(f"         tts: {tts[:100]}")


async def main():
    print("=" * 55)
    print("insuranceCompany IVR — Node Unit Tests")
    print("=" * 55)

    await test_router_node()
    await test_auth_node()
    await test_policy_node()
    await test_payment_node()
    await test_escalation_node()
    await test_faq_node()

    print("\n" + "=" * 55)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
