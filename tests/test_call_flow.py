"""
Full call-flow integration test.
Drives the LangGraph directly with text utterances - no audio/Deepgram/ElevenLabs needed.
Requires the mock CNO API running on port 8001 (started automatically).

Usage:
  venv/Scripts/python tests/test_call_flow.py
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid

import httpx
from langchain_core.messages import HumanMessage

# ── path fix so imports resolve from project root ─────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.checkpoint.memory import MemorySaver
from core.graph.graph import build_graph, set_graph, cno_graph
import core.graph.graph as _graph_module

# Initialise the graph once for tests (MemorySaver — no Postgres needed)
if _graph_module.cno_graph is None:
    set_graph(build_graph().compile(checkpointer=MemorySaver()))

# ── terminal colours ──────────────────────────────────────────────────────────
IVR    = "\033[96m"   # cyan   - IVR speaks
CALLER = "\033[93m"   # yellow - caller says
STEP   = "\033[90m"   # grey   - step label
OK     = "\033[92m"   # green
FAIL   = "\033[91m"   # red
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ivr(text: str):
    print(f"  {IVR}IVR   >{RESET} {text}")


def caller(text: str):
    print(f"  {CALLER}CALLER>{RESET} {text}")


def step(label: str):
    print(f"\n{STEP}-- {label} {'-' * max(0, 60 - len(label))}{RESET}")


def header(title: str):
    line = "=" * 68
    print(f"\n{BOLD}{line}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{line}{RESET}")


def ok(msg: str):
    print(f"  {OK}[PASS] {msg}{RESET}")


def fail(msg: str):
    print(f"  {FAIL}[FAIL] {msg}{RESET}")


# ── Mock CNO API lifecycle ─────────────────────────────────────────────────────

def start_mock_api() -> subprocess.Popen:
    """Start mock_cno_api.py as a subprocess. Returns the process."""
    # Check if already running
    try:
        httpx.get("http://localhost:8001/health", timeout=1)
        print(f"{OK}Mock CNO API already running on :8001{RESET}")
        return None
    except Exception:
        pass

    proc = subprocess.Popen(
        [sys.executable, "mock_cno_api.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    # Wait up to 5s for it to come up
    for _ in range(10):
        time.sleep(0.5)
        try:
            httpx.get("http://localhost:8001/health", timeout=1)
            print(f"{OK}Mock CNO API started on :8001 (pid {proc.pid}){RESET}")
            return proc
        except Exception:
            continue

    proc.terminate()
    raise RuntimeError("Mock CNO API failed to start within 5 seconds")


def stop_mock_api(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
        print(f"{STEP}Mock CNO API stopped{RESET}")


# ── Graph turn helper ─────────────────────────────────────────────────────────

async def turn(state: dict, utterance: str) -> dict:
    """
    Append one human utterance to state, invoke the graph, return the updated state.
    Mirrors the logic in CallHandler._process_turn.
    """
    caller(utterance)
    state = dict(state)
    state["messages"] = list(state.get("messages", [])) + [HumanMessage(content=utterance)]

    result = await _graph_module.cno_graph.ainvoke(
        state,
        config={"configurable": {"thread_id": state["call_sid"]}},
    )

    tts = result.get("tts_text", "")
    if tts:
        ivr(tts)

    # Merge result into state
    merged = {**state, **result}
    return merged


def new_state(call_sid: str | None = None) -> dict:
    """Create a blank initial call state (mirrors SessionService.init_session)."""
    sid = call_sid or f"CAtest{uuid.uuid4().hex[:24]}"
    return {
        "call_sid":       sid,
        "stream_sid":     "",
        "authenticated":  False,
        "auth_step":      "collecting_phone",
        "auth_attempts":  0,
        "customer":       {},
        "access_token":   "",
        "finalized_party": {},
        "candidate_party": {},
        "pii_collected":  {},
        "current_intent": "",
        "current_node":   "",
        "active_flow":    "",
        "slot_attempts":  {},
        "tts_text":       "",
        "transfer_to":    "",
        "otp_step":       "",
        "otp_data":       {},
        "metric_data":    {"intentList": [], "apiCallsList": [], "piiSuccessList": [], "piiFailureList": []},
        "messages":       [],
    }


# ── Scenario helpers ──────────────────────────────────────────────────────────

def assert_auth(state: dict, scenario: str) -> bool:
    if state.get("authenticated") and state.get("auth_step") == "complete":
        ok(f"[{scenario}] Authenticated as {state['customer'].get('firstName')} {state['customer'].get('lastName')}")
        ok(f"[{scenario}] Policy: {state['customer'].get('policyNumber')}")
        ok(f"[{scenario}] Access token: {state['access_token'][:30]}...")
        return True
    fail(f"[{scenario}] Auth failed - auth_step={state.get('auth_step')}")
    return False


def assert_node(state: dict, expected_node: str, scenario: str) -> bool:
    got = state.get("current_node", "")
    if got == expected_node:
        ok(f"[{scenario}] Reached node: {expected_node}")
        return True
    fail(f"[{scenario}] Expected node '{expected_node}', got '{got}'")
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO A - Phone + DOB auth -> policy inquiry
# Caller: John Smith | Phone 5551234567 | DOB July 15, 1965
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_a():
    header("SCENARIO A - Phone + DOB auth -> Policy inquiry")
    passed = True
    state  = new_state()

    step("Turn 1 - greeting opens auth, caller says phone number")
    # Initial turn with no utterance triggers auth ask
    state = await turn(state, "five five five one two three four five six seven")

    step("Turn 2 - caller gives date of birth")
    state = await turn(state, "July fifteenth nineteen sixty five")

    step("Turn 3 - caller gives their name")
    state = await turn(state, "John Smith")

    if not assert_auth(state, "A"):
        passed = False
    else:
        step("Turn 4 - caller asks about policy status")
        state = await turn(state, "I want to check my policy status")

        if not assert_node(state, "policy", "A"):
            passed = False
        else:
            tts = state.get("tts_text", "")
            if "active" in tts.lower() or "premium" in tts.lower() or "125" in tts:
                ok("[A] Policy response contains policy data")
            else:
                fail(f"[A] Policy response looks wrong: {tts[:100]}")
                passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO B - Phone + DOB auth -> Loan inquiry
# Caller: John Smith - same caller, checking loan balance
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_b():
    header("SCENARIO B - Phone + DOB auth -> Loan inquiry")
    passed = True
    state  = new_state()

    step("Auth - phone")
    state = await turn(state, "five five five one two three four five six seven")

    step("Auth - DOB")
    state = await turn(state, "July 15th 1965")

    step("Auth - caller name")
    state = await turn(state, "John Smith")

    if not assert_auth(state, "B"):
        return False

    step("Turn 4 - caller asks about loan balance")
    state = await turn(state, "What is my loan balance?")

    if not assert_node(state, "loan", "B"):
        passed = False
    else:
        tts = state.get("tts_text", "")
        if any(w in tts.lower() for w in ["loan", "balance", "5000", "interest", "payoff"]):
            ok(f"[B] Loan response contains loan data")
        else:
            fail(f"[B] Loan response looks wrong: {tts[:100]}")
            passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO C - Policy number auth (phone not found) -> Beneficiary inquiry
# Caller: Mary Johnson | Policy P300654321 | DOB March 22, 1950
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_c():
    header("SCENARIO C - Policy number auth -> Beneficiary inquiry")
    passed = True
    state  = new_state()

    # Phone not in mock DB -> IVR reads it back: "I heard 555-999-8888. Is that correct?"
    # Caller says YES (confirms the number is right but wasn't found)
    # -> auth_node transitions to collecting_policy
    step("Turn 1 - caller gives a phone not in the mock DB")
    state = await turn(state, "five five five nine nine nine eight eight eight eight")

    step("Turn 2 - caller confirms the phone (yes -> policy path)")
    state = await turn(state, "yes")
    # IVR: "What is your policy number?"

    step("Turn 3 - caller gives policy number")
    state = await turn(state, "P three zero zero six five four three two one")

    step("Turn 4 - caller gives DOB")
    state = await turn(state, "March 22nd 1950")

    step("Turn 5 - caller gives their name")
    state = await turn(state, "Mary Johnson")

    if not assert_auth(state, "C"):
        # Mary Johnson's policy - fallback: check if we're at least past auth step
        current_step = state.get("auth_step", "")
        print(f"  {STEP}auth_step={current_step}{RESET}")
        passed = False
    else:
        step("Turn 6 - caller asks about beneficiaries")
        state = await turn(state, "Who are my beneficiaries?")

        if not assert_node(state, "beneficiary", "C"):
            passed = False
        else:
            tts = state.get("tts_text", "")
            if any(w in tts.lower() for w in ["michael", "lisa", "johnson", "beneficiar"]):
                ok(f"[C] Beneficiary response contains beneficiary names")
            else:
                fail(f"[C] Beneficiary response looks wrong: {tts[:100]}")
                passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO D - Multi-policy caller -> policy selection -> payment history
# Caller: Robert Williams | Phone 5553334444 | DOB Nov 8, 1945 | 2 policies
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_d():
    header("SCENARIO D - Multi-policy caller -> policy selection -> payment history")
    passed = True
    state  = new_state()

    step("Auth - phone")
    state = await turn(state, "five five five three three three four four four four")

    step("Auth - DOB")
    state = await turn(state, "November eighth nineteen forty five")
    # Multi-policy → IVR asks for last 4 digits of policy number

    auth_step = state.get("auth_step", "")
    if auth_step == "collecting_policy_selection":
        ok("[D] Multi-policy selection triggered")
        tts = state.get("tts_text", "")
        ok(f"[D] IVR offered policy selection: {tts[:80]}")

        step("Turn 3 - caller selects policy ending 1222")
        state = await turn(state, "1222")
        # → collecting_caller_name

        step("Turn 4 - caller gives their name")
        state = await turn(state, "Robert Williams")

        if not assert_auth(state, "D"):
            passed = False
        else:
            ok(f"[D] Selected policy: {state['customer'].get('policyNumber')}")
    elif state.get("authenticated") and state.get("auth_step") == "complete":
        ok(f"[D] Auth complete (single policy path): {state['customer'].get('policyNumber')}")
    else:
        fail(f"[D] Unexpected auth_step after DOB: {auth_step}")
        passed = False

    if passed:
        step("Turn - caller asks for payment history")
        state = await turn(state, "Can you show me my recent payments?")

        if not assert_node(state, "payment", "D"):
            passed = False
        else:
            tts = state.get("tts_text", "")
            if any(w in tts.lower() for w in ["payment", "transaction", "210", "posted"]):
                ok(f"[D] Payment history response contains transaction data")
            else:
                fail(f"[D] Payment history response looks wrong: {tts[:100]}")
                passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO E - Auth failure (wrong DOB + wrong name) -> escalation
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_e():
    header("SCENARIO E - Wrong DOB -> wrong name -> escalation")
    passed = True
    state  = new_state()

    step("Auth - phone (John Smith)")
    state = await turn(state, "five five five one two three four five six seven")

    step("Auth - WRONG DOB (no match -> IVR asks for name directly)")
    state = await turn(state, "January first two thousand")
    # DOB doesn't match -> IVR immediately asks for insured name (no confirm step)

    step("Auth - WRONG name")
    state = await turn(state, "my name is Donald Duck")
    # Name doesn't fuzzy-match "John Smith" -> should escalate

    auth_step = state.get("auth_step", "")
    if auth_step == "failed":
        ok("[E] Escalated as expected after wrong DOB + wrong name")
        tts = state.get("tts_text", "")
        if "transfer" in tts.lower() or "representative" in tts.lower():
            ok("[E] Escalation verbiage includes transfer message")
        else:
            fail(f"[E] Unexpected escalation text: {tts[:100]}")
            passed = False
    else:
        fail(f"[E] Expected escalation, got auth_step={auth_step}")
        passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO F - FAQ (unauthenticated) -> escalate intent
# ═══════════════════════════════════════════════════════════════════════════════

async def scenario_f():
    header("SCENARIO F - Unauthenticated FAQ + escalation request")
    passed = True
    state  = new_state()

    step("Caller asks a general FAQ before auth")
    state = await turn(state, "What is the grace period for a missed payment?")

    # Should go to FAQ or auth node (depends on router)
    node = state.get("current_node", "")
    tts  = state.get("tts_text", "")
    if node in ("faq", "router", "auth"):
        ok(f"[F] Router handled FAQ/auth request - node={node}")
        if tts:
            ok(f"[F] IVR responded: {tts[:80]}")
    else:
        fail(f"[F] Unexpected node={node} for FAQ turn")
        passed = False

    step("Caller asks to speak to a representative")
    state = await turn(state, "I want to speak to a representative")

    node = state.get("current_node", "")
    tts  = state.get("tts_text", "")
    if node == "escalation" or "transfer" in tts.lower() or "representative" in tts.lower():
        ok(f"[F] Escalation triggered correctly - node={node}")
    else:
        fail(f"[F] Escalation not triggered - node={node}, tts={tts[:80]}")
        passed = False

    return passed


# ═══════════════════════════════════════════════════════════════════════════════
# Main runner
# ═══════════════════════════════════════════════════════════════════════════════

async def run_all():
    mock_proc = start_mock_api()

    print(f"\n{BOLD}Running 6 call-flow scenarios against live LangGraph + Mock CNO API{RESET}\n")

    scenarios = [
        ("A", "Phone+DOB auth -> policy inquiry",          scenario_a),
        ("B", "Phone+DOB auth -> loan inquiry",            scenario_b),
        ("C", "Policy number auth -> beneficiary inquiry", scenario_c),
        ("D", "Multi-policy selection -> payment history", scenario_d),
        ("E", "Wrong DOB + wrong name -> escalation",      scenario_e),
        ("F", "FAQ + escalation request",                 scenario_f),
    ]

    results = []
    for sid, label, fn in scenarios:
        try:
            passed = await fn()
            results.append((sid, label, passed))
        except Exception as exc:
            fail(f"[{sid}] EXCEPTION: {exc}")
            import traceback; traceback.print_exc()
            results.append((sid, label, False))

    # ── Summary ───────────────────────────────────────────────────────────────
    header("RESULTS SUMMARY")
    total  = len(results)
    passed_count = sum(1 for _, _, p in results if p)

    for sid, label, p in results:
        tag = f"{OK}PASS{RESET}" if p else f"{FAIL}FAIL{RESET}"
        print(f"  [{tag}]  Scenario {sid}: {label}")

    print()
    if passed_count == total:
        print(f"  {OK}{BOLD}All {total} scenarios passed.{RESET}")
    else:
        print(f"  {FAIL}{BOLD}{total - passed_count}/{total} scenarios FAILED.{RESET}")

    stop_mock_api(mock_proc)
    return passed_count == total


if __name__ == "__main__":
    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)
