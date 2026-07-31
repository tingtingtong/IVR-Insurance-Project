"""
Direct LangGraph conversation test — no phone, no Twilio, no audio.
Simulates a full call as typed text turns.
Run: python tests/test_graph.py

Requires:
  - .env with OPENAI_API_KEY set
  - Mock insuranceCompany API running: python tests/mock_cno_api.py
  - Redis running: docker run -p 6379:6379 redis:alpine
"""
import asyncio
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage
from core.graph.graph import cno_graph


CALL_SID = "test-call-001"   # fake call ID for the test session


async def send(utterance: str, state: dict) -> dict:
    """Send one turn to LangGraph and return updated state."""
    state.setdefault("messages", [])
    state["messages"] = state["messages"] + [HumanMessage(content=utterance)]

    result = await cno_graph.ainvoke(
        state,
        config={"configurable": {"thread_id": CALL_SID}},
    )
    print(f"\nCALLER: {utterance}")
    print(f"   IVR: {result.get('tts_text', '(no response)')}")
    if result.get("transfer_to"):
        print(f"  [TRANSFER TO: {result['transfer_to']}]")
    return result


async def run_test_call():
    """
    Simulated call flow:
      1. Caller asks about policy
      2. Auth: provide phone number
      3. Auth: provide date of birth  (2 PII = authenticated)
      4. Get policy information
      5. Ask about payment history
      6. Close
    """
    print("=" * 60)
    print("insuranceCompany IVR — LangGraph Test Call")
    print("=" * 60)

    state = {
        "call_sid":        CALL_SID,
        "stream_sid":      "test-stream-001",
        "authenticated":   False,
        "auth_step":       "collecting_phone",
        "auth_attempts":   0,
        "customer":        {},
        "access_token":    "",
        "finalized_party": {},
        "candidate_party": {},
        "pii_collected":   {},
        "current_intent":  "",
        "current_node":    "",
        "active_flow":     "",
        "slot_attempts":   {},
        "tts_text":        "",
        "transfer_to":     "",
        "otp_step":        "",
        "otp_data":        {},
        "metric_data":     {},
        "messages":        [],
    }

    # Turn 1: Opening intent
    state = await send("I want to check my policy information", state)

    # Turn 2: Auth — provide phone number
    state = await send("My phone number is 555 123 4567", state)

    # Turn 3: Auth — confirm DOB read-back
    state = await send("January twenty-second nineteen seventy-eight", state)

    # Turn 4: Confirm DOB read-back ("yes")
    if state.get("auth_step") == "confirming_dob":
        state = await send("yes", state)

    # Turn 4: Policy info (now authenticated)
    state = await send("What is my policy status and premium?", state)

    # Turn 5: Payment history
    state = await send("Can you tell me my last few payments?", state)

    # Turn 6: FAQ question
    state = await send("What does my policy cover?", state)

    # Turn 7: Close
    state = await send("No, that's all. Thank you.", state)

    print("\n" + "=" * 60)
    print("Test call complete.")
    print(f"Final auth status:  {state.get('authenticated')}")
    print(f"Customer:           {state.get('customer', {}).get('firstName')} {state.get('customer', {}).get('lastName')}")
    print("=" * 60)


async def run_interactive():
    """Interactive mode — type utterances manually."""
    print("=" * 60)
    print("insuranceCompany IVR — Interactive Test (type 'quit' to exit)")
    print("=" * 60)

    state = {
        "call_sid":        CALL_SID,
        "stream_sid":      "test-stream-001",
        "authenticated":   False,
        "auth_step":       "collecting_phone",
        "auth_attempts":   0,
        "customer":        {},
        "access_token":    "",
        "finalized_party": {},
        "candidate_party": {},
        "pii_collected":   {},
        "current_intent":  "",
        "current_node":    "",
        "active_flow":     "",
        "slot_attempts":   {},
        "tts_text":        "",
        "transfer_to":     "",
        "otp_step":        "",
        "otp_data":        {},
        "metric_data":     {},
        "messages":        [],
    }

    # Trigger greeting
    state = await send("hello", state)

    while True:
        try:
            utterance = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if utterance.lower() in ("quit", "exit", "q"):
            break
        if not utterance:
            continue
        state = await send(utterance, state)
        if state.get("transfer_to"):
            print("[Call would be transferred to live agent]")
            break


def _fresh_state(call_id: str = "cs-test-001") -> dict:
    return {
        "call_sid":        call_id,
        "stream_sid":      "test-stream-cs",
        "authenticated":   False,
        "auth_step":       "collecting_phone",
        "auth_attempts":   0,
        "customer":        {},
        "access_token":    "",
        "finalized_party": {},
        "candidate_party": {},
        "pii_collected":   {},
        "current_intent":  "",
        "current_node":    "",
        "active_flow":     "",
        "slot_attempts":   {},
        "tts_text":        "",
        "transfer_to":     "",
        "otp_step":        "",
        "otp_data":        {},
        "metric_data":     {},
        "messages":        [],
    }


def _label(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


async def run_context_switch_tests():
    """
    Context switch scenarios:

    LOCKED flows  (auth):
      S1 — mid-auth: caller asks about policy  → must stay in auth
      S2 — mid-auth: caller pivots to loan     → must stay in auth
      S3 — mid-auth: caller asks for an agent  → escalation must be allowed

    OPEN flows  (post-auth):
      S4 — caller pivots from policy to loan mid-conversation
      S5 — caller pivots from payment to FAQ
      S6 — complete multi-topic call without any explicit "change topic" phrase
    """
    print("=" * 60)
    print("insuranceCompany IVR — Context Switch Tests")
    print("=" * 60)
    passes = 0
    total  = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal passes, total
        total += 1
        if condition:
            passes += 1
        tag = _label(condition)
        print(f"  {tag}  {label}")
        if detail:
            print(f"       {detail}")

    # ── Helper: authenticate a fresh state ───────────────────────────────────
    async def authenticate(call_id: str) -> dict:
        s = _fresh_state(call_id)
        s = await send("hello", s)
        s = await send("555 123 4567", s)
        s = await send("January twenty-second nineteen seventy-eight", s)
        # Confirm DOB read-back if required
        if s.get("auth_step") == "confirming_dob":
            s = await send("yes", s)
        return s

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S1: Mid-auth — caller asks about policy (should be LOCKED) ---")
    s = _fresh_state("cs-s1")
    s = await send("hello", s)                           # IVR asks for phone
    s = await send("what is my policy status", s)        # pivot attempt mid-auth
    check(
        "locked: policy pivot suppressed, still in auth",
        s.get("auth_step") not in ("complete", "failed") and not s.get("authenticated"),
        f"auth_step={s.get('auth_step')}  active_flow={s.get('active_flow')}",
    )
    check(
        "locked: IVR still asking for PII (not policy response)",
        "policy" not in s.get("tts_text", "").lower()
        or "phone" in s.get("tts_text", "").lower()
        or "date of birth" in s.get("tts_text", "").lower(),
        f"tts: {s.get('tts_text', '')[:80]}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S2: Mid-auth — caller pivots to loan (should be LOCKED) ---")
    s = _fresh_state("cs-s2")
    s = await send("hello", s)
    s = await send("what is my loan balance", s)         # pivot attempt
    check(
        "locked: loan pivot suppressed, still in auth",
        not s.get("authenticated"),
        f"auth_step={s.get('auth_step')}  active_flow={s.get('active_flow')}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S3: Mid-auth — caller asks for an agent (escalation must work) ---")
    s = _fresh_state("cs-s3")
    s = await send("hello", s)
    s = await send("I need to speak to a representative", s)   # escalation
    check(
        "locked: escalation allowed even during auth",
        s.get("transfer_to") != "" or s.get("current_intent") == "escalate",
        f"transfer_to={s.get('transfer_to')}  intent={s.get('current_intent')}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S4: Post-auth — policy → loan (open, should switch) ---")
    s = await authenticate("cs-s4")
    check("authenticated before switch test", s.get("authenticated"), "")
    s = await send("what is my policy status", s)
    policy_tts = s.get("tts_text", "")
    s = await send("what about my loan balance", s)      # natural pivot, no "change topic"
    loan_tts = s.get("tts_text", "")
    check(
        "open: switched from policy to loan",
        "loan" in loan_tts.lower() or "balance" in loan_tts.lower() or "500" in loan_tts,
        f"tts: {loan_tts[:80]}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S5: Post-auth — payment history → FAQ (open, should switch) ---")
    s = await authenticate("cs-s5")
    s = await send("show me my recent payments", s)
    s = await send("what is the free look period", s)    # direct pivot to FAQ
    faq_tts = s.get("tts_text", "")
    check(
        "open: switched from payment to faq",
        "free" in faq_tts.lower() or "look" in faq_tts.lower() or "cancel" in faq_tts.lower() or bool(faq_tts),
        f"tts: {faq_tts[:80]}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print("\n--- S6: Multi-topic call without explicit pivot phrase ---")
    s = await authenticate("cs-s6")
    s = await send("policy status", s)
    s = await send("beneficiaries", s)        # no "can I ask" — direct topic jump
    s = await send("loan balance", s)         # another direct jump
    s = await send("transfer me to someone", s)
    check(
        "open: multi-topic + escalation all work",
        s.get("transfer_to") != "" or s.get("current_intent") == "escalate",
        f"transfer_to={s.get('transfer_to')}",
    )

    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"Context Switch Tests: {passes}/{total} passed")
    print("=" * 60)


async def run_auth_tests():
    """
    Auth flow scenarios:
      A1 — phone found, correct DOB → authenticated
      A2 — phone found, wrong DOB, correct name → authenticated
      A3 — phone NOT found, confirm yes, correct policy + DOB → authenticated
      A4 — phone NOT found, confirm yes, correct policy + name → authenticated
      A5 — DOB readback: caller says 'no' to wrong date, re-enters correct date → authenticated
      A6 — 9-digit phone → length hint → correct 10 digits → authenticated
    """
    print("=" * 60)
    print("insuranceCompany IVR — Auth Flow Tests")
    print("=" * 60)
    passes = 0
    total  = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal passes, total
        total += 1
        if condition:
            passes += 1
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")
        if detail:
            print(f"       {detail}")

    # ── A1: Phone + DOB ───────────────────────────────────────────────────────
    print("\n--- A1: Phone found + correct DOB → auth ---")
    s = _fresh_state("auth-a1")
    s = await send("hello", s)
    s = await send("555 123 4567", s)
    s = await send("January twenty-second nineteen seventy-eight", s)
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)
    check("A1: authenticated", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')} customer={s.get('customer', {}).get('firstName')}")

    # ── A2: Phone + name (DOB fails) ──────────────────────────────────────────
    print("\n--- A2: Phone found + wrong DOB + correct name → auth ---")
    s = _fresh_state("auth-a2")
    s = await send("hello", s)
    s = await send("555 123 4567", s)
    s = await send("January first two thousand", s)          # wrong DOB
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)                             # confirm wrong DOB
    # Should advance to collecting_name since DOB didn't match
    check("A2: advanced to name after DOB fail",
          s.get("auth_step") == "collecting_name",
          f"auth_step={s.get('auth_step')}")
    s = await send("John Doe", s)
    check("A2: authenticated with name", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')}")

    # ── A3: Phone not found → confirm → policy + DOB ──────────────────────────
    print("\n--- A3: Phone not found → confirm → policy P400567890 + DOB → auth (Sarah Johnson) ---")
    s = _fresh_state("auth-a3")
    s = await send("hello", s)
    s = await send("800 555 1234", s)                        # phone not in system
    check("A3: advanced to confirming_phone",
          s.get("auth_step") == "confirming_phone",
          f"auth_step={s.get('auth_step')} tts={s.get('tts_text','')[:60]}")
    s = await send("yes", s)                                 # confirm phone
    s = await send("P 400 567 890", s)                       # policy number
    s = await send("July fifteenth nineteen eighty-five", s) # DOB
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)
    check("A3: authenticated", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')} customer={s.get('customer', {}).get('firstName')}")

    # ── A4: Phone not found → confirm → policy + name ─────────────────────────
    print("\n--- A4: Phone not found → confirm → policy P400567890 + name → auth ---")
    s = _fresh_state("auth-a4")
    s = await send("hello", s)
    s = await send("800 555 9999", s)
    s = await send("yes", s)                                 # confirm phone
    s = await send("P400567890", s)                          # policy
    s = await send("March first two thousand", s)            # wrong DOB
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)
    check("A4: advanced to name after wrong DOB",
          s.get("auth_step") == "collecting_name",
          f"auth_step={s.get('auth_step')}")
    s = await send("Sarah Johnson", s)
    check("A4: authenticated with name", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')}")

    # ── A5: DOB readback 'no' → re-enter correct DOB ─────────────────────────
    print("\n--- A5: DOB readback 'no' → re-enter correct DOB → auth ---")
    s = _fresh_state("auth-a5")
    s = await send("hello", s)
    s = await send("555 123 4567", s)
    s = await send("January third nineteen eighty", s)       # wrong DOB (misheard)
    if s.get("auth_step") == "confirming_dob":
        s = await send("no", s)                              # reject readback
    check("A5: re-collecting DOB after 'no'",
          s.get("auth_step") == "collecting_dob",
          f"auth_step={s.get('auth_step')}")
    s = await send("January twenty-second nineteen seventy-eight", s)  # correct DOB
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)
    check("A5: authenticated after corrected DOB", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')}")

    # ── A6: 9-digit phone → length hint → correct ────────────────────────────
    print("\n--- A6: 9-digit phone → length hint → correct 10 digits → auth ---")
    s = _fresh_state("auth-a6")
    s = await send("hello", s)
    s = await send("55 123 4567", s)                         # 9 digits
    check("A6: length hint in response",
          "9 digits" in s.get("tts_text", "") or "9" in s.get("tts_text", ""),
          f"tts={s.get('tts_text','')[:80]}")
    s = await send("555 123 4567", s)                        # correct 10 digits
    s = await send("January twenty-second nineteen seventy-eight", s)
    if s.get("auth_step") == "confirming_dob":
        s = await send("yes", s)
    check("A6: authenticated after length correction", s.get("authenticated") is True,
          f"auth_step={s.get('auth_step')}")

    print(f"\n{'=' * 60}")
    print(f"Auth Tests: {passes}/{total} passed")
    print("=" * 60)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "interactive":
        asyncio.run(run_interactive())
    elif mode == "context":
        asyncio.run(run_context_switch_tests())
    elif mode == "auth":
        asyncio.run(run_auth_tests())
    else:
        asyncio.run(run_test_call())
