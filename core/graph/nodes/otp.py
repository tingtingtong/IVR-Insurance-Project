"""
One-Time Payment node.

BUG-016: Dual-input architecture:
  - Card/account numbers: collected via DTMF keypad OR spoken voice (OpenAI Realtime / STT)
    These sensitive numbers never pass through the LLM — PCI compliant.
  - All other fields (expiry, CVV, amount, routing): collected via normal STT/TTS
    through the LangGraph conversation flow.
  - ACH authorization script is read verbatim before bank collection.
"""
import time
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.tools.payment_api import process_card_payment, process_ach_payment, get_ach_script
from core.prompts.retry_prompts import PROMPTS
from utils.idk_detector import is_idk
from utils.call_logger import log_event

# otp_step state machine
# "start"                   → ask card or bank
# "choosing_method"         → detect card vs bank
# "collecting_card_dtmf"    → card number via DTMF or voice (webhook/stream handles input)
# "collecting_card_expiry"  → expiry via normal STT/TTS
# "collecting_card_cvv"     → CVV via normal STT/TTS
# "collecting_card_amount"  → payment amount via normal STT/TTS
# "ach_auth_script"         → read ACH authorization, wait for "I authorize"
# "collecting_bank_dtmf"    → account number via DTMF or voice
# "collecting_bank_routing" → routing number via normal STT/TTS
# "collecting_bank_amount"  → payment amount via normal STT/TTS
# "dtmf_complete"           → sensitive number collected, validate + proceed
# "confirming"              → confirm all details
# "processing"              → call payment API
# "complete"                → done


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
            # BUG-016: Offer both DTMF and voice for card number
            return {
                "otp_step":   "collecting_card_dtmf",
                "otp_data":   otp_data,
                "tts_text":   "Please enter your 16-digit card number using your keypad, or you can read it out loud.",
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
            # BUG-016: Collect account number first (sensitive), routing via normal STT later
            return {
                "otp_step":   "collecting_bank_dtmf",
                "otp_data":   otp_data,
                "tts_text":   "Please enter your account number using your keypad, or you can read it out loud.",
                "current_node": "otp", "active_flow": "otp",
            }
        else:
            return {
                "otp_step":   "start",
                "otp_data":   {},
                "tts_text":   "No problem. Is there anything else I can help you with today?",
                "current_node": "otp", "active_flow": "otp",
            }

    # ── Step: DTMF/voice collection complete — sensitive number captured ──────
    # BUG-016: Only the card/account number is collected via DTMF or voice.
    # Remaining fields are collected via normal STT/TTS below.
    if otp_step == "dtmf_complete":
        method = otp_data.get("payment_type", "card")

        if method == "card":
            # BUG-017: Validate card number with intelligent self-correction
            card_number = otp_data.get("card_number", "")
            validation_result = _validate_card_with_feedback(card_number, otp_data)
            if validation_result is not None:
                return validation_result
            last4 = card_number[-4:]
            return {
                "otp_step": "collecting_card_expiry",
                "otp_data": otp_data,
                "tts_text": f"Thank you. Card ending in {last4}. Now, what is the expiry date? Please say the month and year, like June 2028.",
                "current_node": "otp", "active_flow": "otp",
            }
        else:
            # Bank account number collected — now ask for routing
            return {
                "otp_step": "collecting_bank_routing",
                "otp_data": otp_data,
                "tts_text": "Thank you. Now, what is your 9-digit routing number?",
                "current_node": "otp", "active_flow": "otp",
            }

    # ── Step: Collect card expiry via normal STT ───────────────────────────
    if otp_step == "collecting_card_expiry":
        expiry = _extract_expiry(last_human)
        if not expiry:
            return {
                "otp_step": "collecting_card_expiry",
                "otp_data": otp_data,
                "tts_text": "I didn't catch the expiry date. Please say the month and year, like June 2028, or enter it as four digits on your keypad.",
                "current_node": "otp", "active_flow": "otp",
            }
        from utils.payment_validator import validate_expiry
        ok, err = validate_expiry(expiry)
        if not ok:
            return {
                "otp_step": "collecting_card_expiry",
                "otp_data": otp_data,
                "tts_text": f"{err}. Please provide a valid future expiry date.",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["expiry"] = expiry
        return {
            "otp_step": "collecting_card_cvv",
            "otp_data": otp_data,
            "tts_text": "What is the 3-digit security code on the back of your card?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Collect CVV via normal STT ───────────────────────────────────
    if otp_step == "collecting_card_cvv":
        cvv = _extract_digits(last_human)
        from utils.payment_validator import validate_cvv
        ok, err = validate_cvv(cvv)
        if not ok:
            return {
                "otp_step": "collecting_card_cvv",
                "otp_data": otp_data,
                "tts_text": "I need the 3 or 4 digit security code from the back of your card. Please try again.",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["cvv"] = cvv
        return {
            "otp_step": "collecting_card_amount",
            "otp_data": otp_data,
            "tts_text": "How much would you like to pay today?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Collect payment amount (card) via normal STT ─────────────────
    if otp_step == "collecting_card_amount":
        amount = _extract_amount(last_human)
        if amount is None or amount <= 0:
            return {
                "otp_step": "collecting_card_amount",
                "otp_data": otp_data,
                "tts_text": "I didn't catch the amount. How much would you like to pay, in dollars?",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["amount"] = amount
        last4 = otp_data.get("card_number", "")[-4:]
        return {
            "otp_step": "confirming",
            "otp_data": otp_data,
            "tts_text": f"I have a card payment of ${amount:.2f} for policy {policy_number}, card ending in {last4}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Collect routing number (bank) via normal STT ─────────────────
    if otp_step == "collecting_bank_routing":
        routing = _extract_digits(last_human)
        from utils.payment_validator import validate_routing_number
        ok, err = validate_routing_number(routing)
        if not ok:
            return {
                "otp_step": "collecting_bank_routing",
                "otp_data": otp_data,
                "tts_text": "I need a 9-digit routing number. Please try again.",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["routing_number"] = routing
        return {
            "otp_step": "collecting_bank_amount",
            "otp_data": otp_data,
            "tts_text": "How much would you like to pay today?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Collect payment amount (bank) via normal STT ─────────────────
    if otp_step == "collecting_bank_amount":
        amount = _extract_amount(last_human)
        if amount is None or amount <= 0:
            return {
                "otp_step": "collecting_bank_amount",
                "otp_data": otp_data,
                "tts_text": "I didn't catch the amount. How much would you like to pay, in dollars?",
                "current_node": "otp", "active_flow": "otp",
            }
        otp_data["amount"] = amount
        return {
            "otp_step": "confirming",
            "otp_data": otp_data,
            "tts_text": f"I have a bank payment of ${amount:.2f} for policy {policy_number}. Is that correct?",
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


def _extract_digits(utterance: str) -> str:
    """Extract only digits from an utterance (for CVV, routing, etc.)."""
    import re
    return re.sub(r"\D", "", utterance)


def _extract_expiry(utterance: str) -> str:
    """
    Extract expiry date from speech. Handles:
    - "06/28", "06/2028", "0628"
    - "June 2028", "june twenty twenty eight"
    - "6 28", "6 2028"
    Returns MM/YY format or empty string.
    """
    import re

    # Direct numeric: 06/28, 06/2028, 06-28
    m = re.search(r"(\d{1,2})[/\-](\d{2,4})", utterance)
    if m:
        month, year = m.group(1), m.group(2)
        if len(year) == 4:
            year = year[2:]
        return f"{int(month):02d}/{year}"

    # 4-digit block: 0628
    m = re.search(r"\b(\d{4})\b", utterance)
    if m:
        val = m.group(1)
        month, year = int(val[:2]), val[2:]
        if 1 <= month <= 12:
            return f"{month:02d}/{year}"

    # Month name + year: "June 2028", "june 28"
    MONTHS = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    lower = utterance.lower()
    for name, num in MONTHS.items():
        if name in lower:
            # Find a year after the month name
            year_match = re.search(r"(\d{2,4})", lower[lower.index(name) + len(name):])
            if year_match:
                year = year_match.group(1)
                if len(year) == 4:
                    year = year[2:]
                return f"{num:02d}/{year}"

    return ""


def _extract_amount(utterance: str) -> float | None:
    """
    Extract a dollar amount from speech. Handles:
    - "$125.50", "125 dollars", "one hundred twenty five", "125.50", "125"
    """
    import re

    # Direct numeric: $125.50, 125.50, 125
    m = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", utterance)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Word-based numbers (simple cases)
    WORD_NUMS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
        "hundred": 100, "thousand": 1000,
    }
    lower = utterance.lower()
    words = re.findall(r"[a-z]+", lower)
    total = 0
    current = 0
    found_num = False
    for w in words:
        if w in WORD_NUMS:
            found_num = True
            val = WORD_NUMS[w]
            if val == 100:
                current = (current or 1) * 100
            elif val == 1000:
                current = (current or 1) * 1000
                total += current
                current = 0
            else:
                current += val
        elif w == "dollars" or w == "dollar":
            continue
    if found_num:
        total += current
        return float(total) if total > 0 else None

    return None


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
