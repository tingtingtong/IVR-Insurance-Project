import asyncio
from langchain_core.messages import SystemMessage, HumanMessage
from core.graph.state import CNOState
from core.prompts.system_prompt import ROUTER_PROMPT
from core.llm_factory import get_router_llm
from config import settings
from utils.call_logger import log_event

_llm = get_router_llm()

VALID_INTENTS = {
    "policy_info", "payment", "otp", "loan", "beneficiary",
    "owner_change", "document", "contact_change", "privacy",
    "faq", "escalate", "goodbye",
}

# ── Per-flow context switch configuration ────────────────────────────────────
#
#  "open"          — caller can freely pivot to any intent at any time
#  "escalate_only" — only transfer-to-agent is allowed out of this flow;
#                    all other pivots are suppressed and the flow continues
#  "locked"        — no context switch at all; escalation is still always
#                    honoured as a safety exit
#
# To change a flow's behaviour, edit the value below.
# ─────────────────────────────────────────────────────────────────────────────
CONTEXT_SWITCH_CONFIG: dict[str, str] = {
    "otp":         "locked",         # PCI payment DTMF entry — no interruption
    "payment":     "escalate_only",  # payment history shown; block random pivots
    "policy":      "escalate_only",
    "loan":        "escalate_only",
    "beneficiary": "escalate_only",
    "contact":     "escalate_only",
    "document":    "escalate_only",
    "privacy":     "escalate_only",
    "faq":         "open",
    "escalation":  "open",
}

# Maps active_flow name → intent string that routes back to that flow
_FLOW_TO_INTENT: dict[str, str] = {
    "otp":         "otp",
    "payment":     "payment",
    "policy":      "policy_info",
    "loan":        "loan",
    "beneficiary": "beneficiary",
    "contact":     "contact_change",
    "document":    "document",
    "privacy":     "privacy",
    "faq":         "faq",
    "goodbye":      "goodbye",
}

# Fast keyword check — avoids an LLM call for escalation detection in locked flows
_ESCALATION_KEYWORDS = {
    "agent", "representative", "person", "human", "operator",
    "transfer", "supervisor", "help", "speak to someone",
}

# Keywords indicating the caller wants to end the call — must bypass auth wall
# ISSUE-2-002 fix: goodbye must not be blocked by the unauthenticated auth redirect
_GOODBYE_KEYWORDS = {"goodbye", "bye", "hang up", "goodbye", "that's all", "thats all",
                     "thank you goodbye", "thanks goodbye", "no thank you", "no thanks"}

# Privacy phrasings the LLM tends to mis-classify as "escalate" — catch before LLM call
_PRIVACY_PHRASES = (
    "how do you use my", "how is my personal", "what do you do with my",
    "privacy policy", "privacy question", "my information", "my data",
    "do not share", "don't share", "opt out", "opt-out",
)

# Keywords indicating a confirmation "no" — used to prevent misrouting "that is wrong"
# as an escalation when the caller is in a multi-step confirmation flow
# ISSUE-2-001 fix: "no that is wrong" / "that is wrong" in a confirming step must
# stay in the active flow, not leak to escalation via LLM misclassification
_CONFIRMATION_NO_PHRASES = ("that is wrong", "that's wrong", "not right", "not correct", "incorrect")
_CONFIRMATION_NO_WORDS   = {"no", "nope", "wrong", "incorrect"}


def _caller_wants_escalation(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _ESCALATION_KEYWORDS)


def _caller_wants_goodbye(text: str) -> bool:
    """Check if caller wants to end the call — intent that must bypass the auth wall."""
    lower = text.lower().strip()
    return any(kw in lower for kw in _GOODBYE_KEYWORDS)


def _is_confirmation_no(text: str) -> bool:
    """
    Return True when the utterance is a simple negation/correction in a confirmation step.
    Used to prevent the LLM from mis-classifying 'no that is wrong' as 'escalate'.
    Only applied when the active_flow is in escalate_only mode (multi-step flows).
    """
    import re
    lower = text.lower().strip()
    # Explicit correction phrases — highest confidence
    if any(p in lower for p in _CONFIRMATION_NO_PHRASES):
        return True
    # Single-word negation with no escalation keyword present
    words = set(re.sub(r"[^a-z0-9 ]", " ", lower).split())
    if words & _CONFIRMATION_NO_WORDS and not any(kw in lower for kw in _ESCALATION_KEYWORDS):
        return True
    return False


async def _invoke_llm_with_retry(messages: list, max_attempts: int = 3) -> object:
    """
    Invoke the LLM with exponential backoff retry for transient 503/429 errors.
    ISSUE-3-001 fix: Groq returns 503/429 under concurrent load. Without retry,
    the caller is immediately transferred to an agent on what is a recoverable error.
    Delays: 1s after attempt 1, 2s after attempt 2, then raise on attempt 3 failure.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await _llm.ainvoke(messages)
        except Exception as exc:
            err_str = str(exc).lower()
            err_type = type(exc).__name__
            # Retry on transient Groq errors (503, 429) and Bedrock throttling
            retryable_strings = ("503", "429", "overloaded", "capacity", "rate limit")
            retryable_types = ("ThrottlingException", "ModelTimeoutException",
                               "ServiceUnavailableException")
            if any(code in err_str for code in retryable_strings) or err_type in retryable_types:
                last_exc = exc
                if attempt < max_attempts - 1:
                    delay = 2 ** attempt  # 1s, 2s
                    await asyncio.sleep(delay)
                    continue
            # Non-retryable error — raise immediately
            raise
    raise last_exc


async def router_node(state: CNOState) -> dict:
    """
    Classify the caller's latest utterance into an intent.

    Context switch enforcement (checked BEFORE LLM classification):
      locked        → only escalation allowed out; saves an LLM call
      escalate_only → LLM still classifies; non-escalation intents are suppressed
      open          → full LLM classification, no suppression
    """
    messages       = state.get("messages", [])
    authenticated  = state.get("authenticated", False)
    auth_step      = state.get("auth_step", "collecting_phone")
    active_flow    = state.get("active_flow", "")
    caller_persona = state.get("caller_persona", "")

    # ── Get last human utterance ──────────────────────────────────────────────
    last_human = ""
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            last_human = msg.content.strip()
            break

    if not last_human:
        # No speech — keep routing to the active service node if mid-auth/flow
        if active_flow and (not authenticated or not caller_persona):
            return {"current_intent": _FLOW_TO_INTENT.get(active_flow, "faq"), "current_node": "router"}
        return {"current_intent": "", "current_node": "router"}

    # ── Goodbye bypasses auth wall — callers who dial by mistake must be able to leave
    # ISSUE-2-002 fix: goodbye was being blocked by the unauthenticated auth redirect
    # below, causing callers to be asked for their phone number instead of hanging up.
    if _caller_wants_goodbye(last_human):
        return {"current_intent": "goodbye", "current_node": "router"}

    # ── Caller-name collection owns the turn entirely — must check before escalation.
    # The auth node collects the caller's name at this step. We must not let escalation
    # keywords that appear in names (e.g. "Person", "Agent") hijack the turn.
    # Goodbye is still honoured above so callers can always hang up.
    if auth_step == "collecting_caller_name":
        return {"current_intent": _FLOW_TO_INTENT.get(active_flow, "faq"), "current_node": "router"}

    # ── Privacy keyword pre-check (before escalation — LLM mis-classifies these) ──
    _lh = last_human.lower()
    if any(p in _lh for p in _PRIVACY_PHRASES):
        return {"current_intent": "privacy", "current_node": "router"}

    # ── Escalation is always honoured — check first before any flow lock ──────
    if _caller_wants_escalation(last_human):
        return {"current_intent": "escalate", "current_node": "router"}

    # ── Auth in progress: route back to the active service node ──────────────
    # Covers PII utterances (phone, DOB, policy number, name) during multi-turn
    # auth so they reach the service node's auth guard, not the LLM classifier.
    if active_flow and (not authenticated or not caller_persona):
        return {"current_intent": _FLOW_TO_INTENT.get(active_flow, "faq"), "current_node": "router"}

    # ── Context switch enforcement ─────────────────────────────────────────────
    cs_mode = CONTEXT_SWITCH_CONFIG.get(active_flow, "open")

    if cs_mode == "locked":
        # Force back to active flow — no LLM call needed (escalation already handled above)
        forced_intent = _FLOW_TO_INTENT.get(active_flow, "faq")
        return {
            "current_intent": forced_intent,
            "current_node":   "router",
        }

    # ── escalate_only: intercept confirmation negations BEFORE LLM call ──────
    # ISSUE-2-001 fix: "no that is wrong" / "that is wrong" in a multi-step
    # confirmation flow (contact, document, etc.) was leaking through to the LLM
    # which mis-classified it as "escalate". We detect the confirmation-no pattern
    # here and force the intent back to the active flow — same outcome as the
    # non-escalation suppression below, but prevents a wasted LLM call and avoids
    # the window where a misclassification could occur.
    if cs_mode == "escalate_only" and _is_confirmation_no(last_human):
        forced_intent = _FLOW_TO_INTENT.get(active_flow, "faq")
        return {
            "current_intent": forced_intent,
            "current_node":   "router",
        }

    # ── LLM intent classification (with retry for transient Groq errors) ──────
    # ISSUE-3-001 fix: added _invoke_llm_with_retry wrapper — see docstring above.
    prompt = ROUTER_PROMPT.format(utterance=last_human)

    try:
        response = await _invoke_llm_with_retry([
            SystemMessage(content="You are a call intent classifier. Reply with ONE word only."),
            HumanMessage(content=prompt),
        ])
        raw = response.content.strip().lower().replace(" ", "_")
        intent = raw if raw in VALID_INTENTS else "faq"
    except Exception:
        # All retry attempts exhausted — fall back to active flow or escalate
        # so the caller is handled gracefully rather than getting a 500 error.
        intent = _FLOW_TO_INTENT.get(active_flow, "escalate") if active_flow else "escalate"

    # ── escalate_only: suppress any non-escalation pivot ─────────────────────
    if cs_mode == "escalate_only" and intent != "escalate":
        intent = _FLOW_TO_INTENT.get(active_flow, intent)

    # ── Persona gate — "other" callers cannot access restricted policy flows ──
    # This check runs after intent classification so goodbye/faq still work.
    # RESTRICTED_INTENTS mirrors the service flows that expose personal policy data.
    RESTRICTED_INTENTS = {
        "policy_info", "payment", "otp", "loan", "beneficiary",
        "contact_change", "document", "privacy",
    }
    caller_persona = state.get("caller_persona", "")
    if authenticated and caller_persona == "other" and intent in RESTRICTED_INTENTS:
        # Override to escalation — agent can assist callers without policy relationship
        return {"current_intent": "escalate", "current_node": "router"}

    call_sid = state.get("call_sid", "unknown")
    log_event(call_sid, "intent_detected", intent=intent, input=last_human[:80])
    return {"current_intent": intent, "current_node": "router"}
