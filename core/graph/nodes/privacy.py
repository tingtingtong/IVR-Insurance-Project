import time
import aiohttp
from langchain_core.messages import AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.call_logger import log_event

# ISSUE-3-002 fix: Add a brief general privacy disclosure before reading the full
# GLBA opt-out script. Previously the node jumped directly to the opt-out menu
# with no context, leaving callers who asked "how do you use my information" with
# no actual answer before being asked a question. The disclosure satisfies common
# regulatory expectations for a brief oral privacy notice.
PRIVACY_DISCLOSURE = (
    "Our privacy policy governs how we use your personal information to service your policy "
    "and as required by law. "
)

# GROUP F5 / GLBA — read verbatim
GLBA_SCRIPT = (
    "Under the Gramm-Leach-Bliley Act, you have the right to limit the sharing of your "
    "personal information. You may opt out of sharing with affiliated companies, "
    "non-affiliated companies, or both. Which would you like to opt out of — "
    "affiliated, non-affiliated, or both?"
)

OPT_OUT_OPTIONS = {"affiliated", "non-affiliated", "both"}


async def privacy_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "privacy")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0           = time.time()
    customer     = state.get("customer", {})
    access_token = state.get("access_token", "")
    messages     = state.get("messages", [])
    otp_data     = dict(state.get("otp_data", {}))
    call_sid     = state.get("call_sid", "unknown")

    last_human = _last_human(messages)
    step = otp_data.get("privacy_step", "read_script")

    # ── Step 1: Read privacy disclosure + GLBA opt-out script ───────────────
    # ISSUE-3-002 fix: prepend the brief general disclosure before the GLBA script
    # so callers who ask "how do you use my information" receive an actual answer
    # before being presented with the opt-out options.
    if step == "read_script":
        return merge_auth_state(auth_state, {
            "otp_data":   {**otp_data, "privacy_step": "collecting_choice"},
            "tts_text":   PRIVACY_DISCLOSURE + GLBA_SCRIPT,
            "current_node": "privacy", "active_flow": "privacy",
        })

    # ── Step 2: Collect choice ────────────────────────────────────────────────
    if step == "collecting_choice":
        choice = _detect_opt_out_choice(last_human)
        if not choice:
            return {
                "tts_text":   "I'm sorry, please say affiliated, non-affiliated, or both.",
                "current_node": "privacy", "active_flow": "privacy",
            }
        return {
            "otp_data":   {**otp_data, "privacy_step": "confirming", "opt_out": choice},
            "tts_text":   f"You'd like to opt out of sharing with {choice} companies. Is that correct?",
            "current_node": "privacy", "active_flow": "privacy",
        }

    # ── Step 3: Confirm and submit ────────────────────────────────────────────
    if step == "confirming":
        if _is_yes(last_human):
            result = await _submit_opt_out(customer, otp_data, access_token)
            if result:
                tts = "Your privacy opt-out request has been recorded. Is there anything else I can help you with?"
            else:
                tts = PROMPTS["escalation"]["error"]
            return {"otp_data": {}, "tts_text": tts, "current_node": "privacy", "active_flow": "",
                    "messages": [AIMessage(content=tts)]}
        return {
            "otp_data":   {**otp_data, "privacy_step": "read_script"},
            "tts_text":   "No problem. Let me read your options again. " + GLBA_SCRIPT,
            "current_node": "privacy", "active_flow": "privacy",
        }

    tts_fallback = "Is there anything else I can help you with?"
    log_event(call_sid, "node_exit", node="privacy",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts_fallback))
    return {"tts_text": tts_fallback, "current_node": "privacy", "active_flow": ""}


async def _submit_opt_out(customer: dict, data: dict, access_token: str) -> bool:
    url = f"{settings.cno_api_base_url}/privacy/optout"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "PolicyNumber": customer.get("policyNumber", ""),
        "OptOutType":   data.get("opt_out", "both"),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status in (200, 201)
    except Exception:
        return False


def _detect_opt_out_choice(utterance: str) -> str:
    u = utterance.lower()
    if "both" in u:            return "both"
    if "non" in u:             return "non-affiliated"
    if "affiliated" in u:      return "affiliated"
    return ""


def _is_yes(utterance: str) -> bool:
    return any(w in utterance.lower() for w in ["yes", "correct", "right", "sure", "ok"])


def _last_human(messages: list) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            return msg.content.strip()
    return ""
