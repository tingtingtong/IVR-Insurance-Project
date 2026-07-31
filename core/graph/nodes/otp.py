"""
One-Time Payment node.
PCI compliance: card/bank number collection happens via Twilio DTMF
(never spoken aloud / never processed by LLM).
ACH authorization script is read verbatim.
"""
import time
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.tools.payment_api import process_card_payment, process_ach_payment, get_ach_script
from core.prompts.retry_prompts import PROMPTS
from utils.idk_detector import is_idk
from utils.call_logger import log_event

# otp_step state machine
# "start"         → ask card or bank
# "collecting_card_dtmf"  → Twilio DTMF flow (handled by webhooks/twilio_stream.py)
# "collecting_bank_dtmf"  → Twilio DTMF flow
# "ach_auth_script"       → read ACH script, wait for "I authorize"
# "confirming"            → confirm amount and method
# "processing"            → call payment API
# "complete"              → done


async def otp_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "otp")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0           = time.time()
    customer     = state.get("customer", {})
    otp_step     = state.get("otp_step", "start")
    otp_data     = dict(state.get("otp_data", {}))
    messages     = state.get("messages", [])
    access_token = state.get("access_token", "")
    call_sid     = state.get("call_sid", "unknown")

    policy_number = customer.get("policyNumber", "")
    last_human = _last_human(messages)

    # ── Step: Start — ask payment type ───────────────────────────────────────
    if otp_step == "start":
        return merge_auth_state(auth_state, {
            "otp_step":    "choosing_method",
            "tts_text":    "Would you like to make a payment by card or by bank account?",
            "current_node": "otp", "active_flow": "otp",
        })

    # ── Step: Choosing method ─────────────────────────────────────────────────
    if otp_step == "choosing_method":
        # IDK: caller unsure how to pay → offer rep
        if is_idk(last_human):
            return {
                "otp_step":   "start",
                "otp_data":   {},
                "tts_text":   "A representative can help you with your payment. Is there anything else I can help you with today?",
                "current_node": "otp", "active_flow": "",
            }

        method = _detect_payment_method(last_human)
        if not method:
            return {
                "otp_step":   "choosing_method",
                "tts_text":   "I'm sorry, did you say card or bank account?",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["payment_type"] = method
        if method == "card":
            return {
                "otp_step":   "collecting_card_dtmf",
                "otp_data":   otp_data,
                "tts_text":   "Please enter your card number using your keypad, followed by the pound sign.",
                "current_node": "otp", "active_flow": "otp",
            }
        else:
            # ACH — read authorization script first
            return {
                "otp_step":   "ach_auth_script",
                "otp_data":   otp_data,
                "tts_text":   get_ach_script(),
                "current_node": "otp", "active_flow": "otp",
            }

    # ── Step: ACH auth — wait for "I authorize" ───────────────────────────────
    if otp_step == "ach_auth_script":
        if "authorize" in last_human.lower():
            otp_data["ach_authorized"] = True
            return {
                "otp_step":   "collecting_bank_dtmf",
                "otp_data":   otp_data,
                "tts_text":   "Please enter your routing number using your keypad, followed by the pound sign.",
                "current_node": "otp", "active_flow": "otp",
            }
        else:
            return {
                "otp_step":   "start",
                "otp_data":   {},
                "tts_text":   "No problem. Is there anything else I can help you with today?",
                "current_node": "otp", "active_flow": "otp",
            }

    # ── Step: DTMF collection complete (handled by webhook, data in otp_data) ─
    if otp_step == "dtmf_complete":
        amount = otp_data.get("amount", 0)
        method = otp_data.get("payment_type", "card")
        return {
            "otp_step":   "confirming",
            "otp_data":   otp_data,
            "tts_text":   f"I have a {method} payment of ${amount:.2f} for policy {policy_number}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirming ──────────────────────────────────────────────────────
    if otp_step == "confirming":
        if _is_yes(last_human):
            return {
                "otp_step":   "processing",
                "otp_data":   otp_data,
                "tts_text":   "Please hold while I process your payment.",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            return {
                "otp_step":   "start",
                "otp_data":   {},
                "tts_text":   "No problem. Let's start over. Would you like to pay by card or bank account?",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step":   "confirming",
            "tts_text":   "I'm sorry, please say yes to confirm or no to cancel.",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Processing ──────────────────────────────────────────────────────
    if otp_step == "processing":
        result = await _process_payment(otp_data, policy_number, access_token)
        if result["success"]:
            confirmation = result.get("confirmation", "")
            tts = f"Your payment has been processed. Confirmation number: {confirmation}. {PROMPTS['payment_disclosure']}"
        else:
            tts = f"I'm sorry, the payment could not be processed. {result.get('error', '')} Please try again or call back."
        return {
            "otp_step":   "complete",
            "otp_data":   {},
            "tts_text":   tts,
            "current_node": "otp", "active_flow": "",
        }

    tts_fallback = "Is there anything else I can help you with?"
    log_event(call_sid, "node_exit", node="otp",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts_fallback))
    return merge_auth_state(auth_state, {"tts_text": tts_fallback, "current_node": "otp", "active_flow": ""})


async def _process_payment(otp_data: dict, policy_number: str, access_token: str) -> dict:
    method = otp_data.get("payment_type", "card")
    amount = float(otp_data.get("amount", 0))

    if method == "card":
        return await process_card_payment(
            policy_number=policy_number,
            access_token=access_token,
            amount=amount,
            card_number=otp_data.get("card_number", ""),
            expiry=otp_data.get("expiry", ""),
            cvv=otp_data.get("cvv", ""),
        )
    else:
        return await process_ach_payment(
            policy_number=policy_number,
            access_token=access_token,
            amount=amount,
            routing_number=otp_data.get("routing_number", ""),
            account_number=otp_data.get("account_number", ""),
        )


def _detect_payment_method(utterance: str) -> str:
    u = utterance.lower()
    if any(w in u for w in ["card", "credit", "debit", "visa", "mastercard"]):
        return "card"
    if any(w in u for w in ["bank", "checking", "savings", "account", "ach"]):
        return "bank"
    return ""


def _is_yes(utterance: str) -> bool:
    u = utterance.lower()
    return any(w in u for w in ["yes", "correct", "right", "sure", "ok", "okay", "confirm", "yep", "yeah"])


def _is_no(utterance: str) -> bool:
    u = utterance.lower()
    return any(w in u for w in ["no", "wrong", "cancel", "nope", "incorrect"])


def _last_human(messages: list) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            return msg.content.strip()
    return ""
