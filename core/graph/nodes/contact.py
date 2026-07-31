import re
import time
import aiohttp
from langchain_core.messages import AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.prompts.retry_prompts import PROMPTS, get_retry_prompt
from config import settings
from utils.idk_detector import is_idk
from utils.call_logger import log_event

_DONE_TTS = "No problem. Is there anything else I can help you with today?"


def _exit_response() -> dict:
    return {"otp_data": {}, "tts_text": _DONE_TTS, "current_node": "contact", "active_flow": ""}


async def contact_node(state: CNOState) -> dict:
    """Change contact information — address and/or phone number."""
    ok, auth_state = await ensure_authenticated(state, "contact")
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
    step = otp_data.get("contact_step", "ask_what_to_change")

    # ── Cancel / exit detected at any step except confirming ─────────────────
    # At "confirming" step a bare "no" means correction, not cancellation
    if step != "ask_what_to_change" and step != "confirming" and _is_cancel(last_human):
        return _exit_response()

    # ── Ask what they want to change ─────────────────────────────────────────
    if step == "ask_what_to_change":
        return merge_auth_state(auth_state, {
            "otp_data":     {**otp_data, "contact_step": "collecting"},
            "tts_text":     "Would you like to update your address, your phone number, or both?",
            "current_node": "contact", "active_flow": "contact",
        })

    # ── Collecting new details ────────────────────────────────────────────────
    if step == "collecting":
        # IDK at the "what to change?" prompt → caller unsure → offer rep
        if is_idk(last_human):
            tts = "That's okay. I can connect you with a representative who can help update your contact information."
            return {"otp_data": {}, "tts_text": tts, "current_node": "contact", "active_flow": ""}

        lower = last_human.lower()
        # Use word-boundary matching so "address" inside "I don't want to update..." doesn't trigger
        wants_address = bool(re.search(r'\b(address|street|zip)\b', lower))
        wants_phone   = bool(re.search(r'\b(phone|number|mobile|cell)\b', lower))
        if wants_address and not wants_phone:
            return {
                "otp_data":     {**otp_data, "contact_step": "collecting_address"},
                "tts_text":     "What is the new street address including city, state, and zip code?",
                "current_node": "contact", "active_flow": "contact",
            }
        if wants_phone and not wants_address:
            return {
                "otp_data":     {**otp_data, "contact_step": "collecting_phone"},
                "tts_text":     "What is the new 10-digit phone number?",
                "current_node": "contact", "active_flow": "contact",
            }
        if wants_address and wants_phone:
            return {
                "otp_data":     {**otp_data, "contact_step": "collecting_address"},
                "tts_text":     "Let's start with the address. What is the new street address including city, state, and zip code?",
                "current_node": "contact", "active_flow": "contact",
            }
        # Unrecognised — check for cancel words
        if _is_cancel(last_human):
            return _exit_response()
        return {
            "otp_data":     {**otp_data, "contact_step": "collecting"},
            "tts_text":     "I can help update your address or phone number. Which would you like to change, or say cancel to go back?",
            "current_node": "contact", "active_flow": "contact",
        }

    # ── Collecting address ─────────────────────────────────────────────────────
    if step == "collecting_address":
        # IDK: caller doesn't know their new address → offer rep
        if is_idk(last_human):
            tts = "No problem. A representative can help you with that."
            return {"otp_data": {}, "tts_text": tts, "current_node": "contact", "active_flow": ""}

        otp_data["new_address"] = last_human
        return {
            "otp_data":     {**otp_data, "contact_step": "confirming"},
            "tts_text":     f"I have your new address as: {last_human}. Is that correct?",
            "current_node": "contact", "active_flow": "contact",
        }

    # ── Collecting phone ───────────────────────────────────────────────────────
    if step == "collecting_phone":
        # IDK: caller doesn't know new phone → offer rep
        if is_idk(last_human):
            tts = "No problem. A representative can help you with that."
            return {"otp_data": {}, "tts_text": tts, "current_node": "contact", "active_flow": ""}

        digits = "".join(c for c in last_human if c.isdigit())
        if len(digits) == 10:
            otp_data["new_phone"] = digits
            return {
                "otp_data":     {**otp_data, "contact_step": "confirming"},
                "tts_text":     f"I have your new phone number as {' '.join(digits)}. Is that correct?",
                "current_node": "contact", "active_flow": "contact",
            }
        return {
            "tts_text":     get_retry_prompt("phone", "invalid", 0),
            "current_node": "contact", "active_flow": "contact",
        }

    # ── Confirming ────────────────────────────────────────────────────────────
    if step == "confirming":
        if _is_yes(last_human):
            result = await _submit_contact_change(customer, otp_data, access_token)
            if result:
                tts = "Your contact information has been updated. Is there anything else I can help you with?"
            else:
                tts = PROMPTS["escalation"]["error"]
            return {"otp_data": {}, "tts_text": tts, "current_node": "contact", "active_flow": "",
                    "messages": [AIMessage(content=tts)]}

        # IDK at confirming step → treat as "no", let the re-collect logic below handle it
        # (fall through intentionally — same as an unclear/negative answer)

        # ISSUE-2-001 fix (secondary): when the caller says "no" / "that is wrong"
        # at the confirmation prompt, send them directly back to re-enter the specific
        # field they were confirming (address or phone) rather than back to the top-level
        # "address or phone?" choice. This avoids a frustrating extra round-trip.
        if otp_data.get("new_address") and not otp_data.get("new_phone"):
            # They were confirming an address — re-collect address
            new_otp = {k: v for k, v in otp_data.items() if k != "new_address"}
            return {
                "otp_data":     {**new_otp, "contact_step": "collecting_address"},
                "tts_text":     "No problem. Please provide the new street address including city, state, and zip code.",
                "current_node": "contact", "active_flow": "contact",
            }
        if otp_data.get("new_phone") and not otp_data.get("new_address"):
            # They were confirming a phone — re-collect phone
            new_otp = {k: v for k, v in otp_data.items() if k != "new_phone"}
            return {
                "otp_data":     {**new_otp, "contact_step": "collecting_phone"},
                "tts_text":     "No problem. Please provide the new 10-digit phone number.",
                "current_node": "contact", "active_flow": "contact",
            }
        # Fallback — go back to the top-level choice (both fields or unclear state)
        return {
            "otp_data":     {**otp_data, "contact_step": "collecting"},
            "tts_text":     "No problem. Would you like to update your address or phone number, or say cancel to go back?",
            "current_node": "contact", "active_flow": "contact",
        }

    log_event(call_sid, "node_exit", node="contact",
              latency_ms=int((time.time() - t0) * 1000), chars=len(_DONE_TTS))
    return {"otp_data": {}, "tts_text": _DONE_TTS, "current_node": "contact", "active_flow": ""}


async def _submit_contact_change(customer: dict, data: dict, access_token: str) -> bool:
    url = f"{settings.cno_api_base_url}/contact/update"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "PolicyNumber": customer.get("policyNumber", ""),
        "NewAddress":   data.get("new_address", ""),
        "NewPhone":     data.get("new_phone", ""),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status in (200, 201)
    except Exception:
        return False


def _is_cancel(utterance: str) -> bool:
    lower = utterance.lower()
    words = set(re.sub(r"[^a-z0-9 ]", " ", lower).split())
    if words & {"no", "nope", "cancel", "stop", "exit", "quit", "nothing", "nevermind"}:
        return True
    return any(p in lower for p in ["don't want", "dont want", "not now", "never mind", "go back"])


def _is_yes(utterance: str) -> bool:
    return any(w in utterance.lower() for w in ["yes", "correct", "right", "sure", "ok"])


def _last_human(messages: list) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            return msg.content.strip()
    return ""
