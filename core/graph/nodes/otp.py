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

    log_event(call_sid, "node_enter", node="otp", step=otp_step,
              input=last_human[:40] if last_human else "")

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
        method = otp_data.get("payment_type", "card")

        # BUG-017: Validate card number with intelligent self-correction
        if method == "card":
            card_number = otp_data.get("card_number", "")
            validation_result = _validate_card_with_feedback(card_number, otp_data)
            if validation_result is not None:
                return validation_result

        amount = otp_data.get("amount", 0)
        # Read back last 4 digits for card payments so caller can verify
        if method == "card":
            last4 = otp_data.get("card_number", "")[-4:]
            tts = f"I have a card payment of ${amount:.2f} for policy {policy_number}, card ending in {last4}. Is that correct?"
        else:
            tts = f"I have a bank payment of ${amount:.2f} for policy {policy_number}. Is that correct?"
        return {
            "otp_step":   "confirming",
            "otp_data":   otp_data,
            "tts_text":   tts,
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
            payment_id = result.get("payment_id", "")
            # BUG-018: Include payment ID so callers can reference it later
            id_part = f" Your payment reference ID is {payment_id}." if payment_id else ""
            tts = f"Your payment has been processed. Confirmation number: {confirmation}.{id_part} {PROMPTS['payment_disclosure']}"
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


MAX_CARD_RETRIES = 3


def _validate_card_with_feedback(card_number: str, otp_data: dict) -> dict | None:
    """
    BUG-017: Validate card number with intelligent self-correction.
    Returns a state dict to send back to the caller if validation fails,
    or None if the card is valid.
    """
    import re
    from utils.payment_validator import validate_card_number

    digits = re.sub(r"\D", "", card_number)
    retry_count = otp_data.get("card_retry_count", 0)

    ok, err = validate_card_number(card_number)
    if ok:
        # Reset retry count on success
        otp_data.pop("card_retry_count", None)
        return None

    # Max retries exceeded — escalate to agent
    if retry_count >= MAX_CARD_RETRIES:
        return {
            "otp_step": "start",
            "otp_data": {},
            "tts_text": "I'm sorry, I wasn't able to validate your card number after several attempts. "
                        "Let me transfer you to a representative who can assist you.",
            "current_node": "otp", "active_flow": "",
            "current_intent": "escalate",
        }

    otp_data["card_retry_count"] = retry_count + 1

    # Build intelligent feedback based on what went wrong
    if len(digits) == 0:
        hint = "I didn't receive any digits."
    elif len(digits) < 16:
        hint = f"I only received {len(digits)} digits, but a card number should be 16 digits."
    elif len(digits) > 16:
        hint = f"I received {len(digits)} digits, but a card number should be 16 digits."
    else:
        # 16 digits but failed Luhn — one or more digits are wrong
        hint = f"The number ending in {digits[-4:]} didn't pass verification. One or more digits may be incorrect."

    # On 2nd+ retry, explicitly mention DTMF option
    if retry_count >= 1:
        hint += " You can also enter the number using your keypad."

    return {
        "otp_step": "collecting_card_dtmf",
        "otp_data": otp_data,
        "tts_text": f"{hint} Please try entering your 16-digit card number again.",
        "current_node": "otp", "active_flow": "otp",
    }


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
