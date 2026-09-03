import time
import aiohttp
from langchain_core.messages import AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.idk_detector import is_idk
from utils.call_logger import log_event

# GROUP G3 — document delivery policy (verbatim)
DOCUMENT_DELIVERY_POLICY = (
    "For security purposes, documents can only be sent to the address on file. "
    "We are unable to send documents by email."
)

DOCUMENT_TYPES = {
    "policy": "Policy Document",
    "billing": "Billing Statement",
    "tax": "Tax Form 1099",
    "beneficiary": "Beneficiary Designation Form",
    "claim": "Claim Form",
    "amendment": "Policy Amendment",
}


async def document_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "document")
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
    step = otp_data.get("doc_step", "ask_doc_type")

    log_event(call_sid, "node_enter", node="document", step=step,
              input=last_human[:40] if last_human else "")

    # ── Ask document type ─────────────────────────────────────────────────────
    if step == "ask_doc_type":
        types = ", ".join(DOCUMENT_TYPES.values())
        return merge_auth_state(auth_state, {
            "otp_data":   {**otp_data, "doc_step": "collecting_type"},
            "tts_text":   f"Which document would you like? Available documents are: {types}.",
            "current_node": "document", "active_flow": "document",
        })

    # ── Detect document type ──────────────────────────────────────────────────
    if step == "collecting_type":
        # IDK: caller doesn't know which document → offer rep
        if is_idk(last_human):
            tts = "A representative can help you request documents. Is there anything else I can help you with?"
            return {"otp_data": {}, "tts_text": tts, "current_node": "document", "active_flow": ""}

        doc_key = _detect_doc_type(last_human)
        if not doc_key:
            return {
                "tts_text":   "I'm sorry, I didn't catch that. Which document type would you like?",
                "current_node": "document", "active_flow": "document",
            }
        otp_data["doc_type"] = doc_key

        # Ask delivery method — but enforce address-on-file only
        return {
            "otp_data":   {**otp_data, "doc_step": "ask_delivery"},
            "tts_text":   f"I can arrange to send you the {DOCUMENT_TYPES[doc_key]}. "
                          f"Would you like it mailed or faxed to the address on file?",
            "current_node": "document", "active_flow": "document",
        }

    # ── Delivery preference ───────────────────────────────────────────────────
    if step == "ask_delivery":
        # IDK: caller unsure about delivery method → offer rep
        if is_idk(last_human):
            tts = "No problem. A representative can help you request documents. Is there anything else I can help you with?"
            return {"otp_data": {}, "tts_text": tts, "current_node": "document", "active_flow": ""}

        delivery = _detect_delivery(last_human)

        # Caller asks for email → enforce GROUP G3
        if "email" in last_human.lower():
            return {
                "tts_text":   DOCUMENT_DELIVERY_POLICY + " Would you like it mailed or faxed instead?",
                "current_node": "document", "active_flow": "document",
            }

        if not delivery:
            return {
                "tts_text":   "Please say mail or fax.",
                "current_node": "document", "active_flow": "document",
            }
        otp_data["delivery"] = delivery
        return {
            "otp_data":   {**otp_data, "doc_step": "confirming"},
            "tts_text":   f"I'll send the {DOCUMENT_TYPES[otp_data['doc_type']]} by {delivery} to the address on file. Is that correct?",
            "current_node": "document", "active_flow": "document",
        }

    # ── Confirm and submit ────────────────────────────────────────────────────
    if step == "confirming":
        if _is_yes(last_human):
            result = await _request_document(customer, otp_data, access_token)
            if result:
                tts = f"Your {DOCUMENT_TYPES[otp_data['doc_type']]} will be sent within 7 to 10 business days. Is there anything else I can help you with?"
            else:
                tts = PROMPTS["escalation"]["error"]
            return {"otp_data": {}, "tts_text": tts, "current_node": "document", "active_flow": "",
                    "messages": [AIMessage(content=tts)]}
        return {
            "otp_data":   {**otp_data, "doc_step": "ask_doc_type"},
            "tts_text":   "No problem. Which document would you like?",
            "current_node": "document", "active_flow": "document",
        }

    tts_fallback = "Is there anything else I can help you with?"
    log_event(call_sid, "node_exit", node="document",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts_fallback))
    return {"tts_text": tts_fallback, "current_node": "document", "active_flow": ""}


async def _request_document(customer: dict, data: dict, access_token: str) -> bool:
    url = f"{settings.cno_api_base_url}/document/request"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "PolicyNumber": customer.get("policyNumber", ""),
        "DocumentType": data.get("doc_type", ""),
        "DeliveryMethod": data.get("delivery", "mail"),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status in (200, 201)
    except Exception:
        return False


def _detect_doc_type(utterance: str) -> str:
    u = utterance.lower()
    for key, label in DOCUMENT_TYPES.items():
        if key in u or label.lower() in u:
            return key
    return ""


def _detect_delivery(utterance: str) -> str:
    u = utterance.lower()
    if "mail" in u: return "mail"
    if "fax" in u:  return "fax"
    return ""


def _is_yes(utterance: str) -> bool:
    return any(w in utterance.lower() for w in ["yes", "correct", "right", "sure", "ok"])


def _last_human(messages: list) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            return msg.content.strip()
    return ""
