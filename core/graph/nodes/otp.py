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
# "collecting_card_dtmf"    → card number via DTMF or voice (full or partial)
# "confirming_card_group"   → FEAT-006: confirm a partial group, ask for next
# "confirming_card"         → FEAT-005: confirm full card number with caller
# "collecting_card_expiry"  → expiry via normal STT/TTS
# "confirming_expiry"       → FEAT-005: confirm expiry with caller
# "collecting_card_cvv"     → CVV via normal STT/TTS
# "confirming_cvv"          → FEAT-005: confirm CVV with caller
# "collecting_card_amount"  → payment amount via normal STT/TTS
# "confirming_amount"       → FEAT-005: confirm amount with caller
# "ach_auth_script"         → read ACH authorization, wait for "I authorize"
# "collecting_bank_dtmf"    → account number via DTMF or voice
# "confirming_bank_account" → FEAT-005: confirm account number with caller
# "collecting_bank_routing" → routing number via normal STT/TTS
# "confirming_routing"      → FEAT-005: confirm routing with caller
# "collecting_bank_amount"  → payment amount via normal STT/TTS
# "confirming_bank_amount"  → FEAT-005: confirm bank amount with caller
# "dtmf_complete"           → sensitive number collected, validate + proceed
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

    # ── BUG-028: Cancel/exit from OTP flow at any step ─────────────────────
    if otp_step not in ("start", "complete") and last_human and _wants_cancel(last_human):
        log_event(call_sid, "otp_cancelled", step=otp_step)
        return merge_auth_state(auth_state, {
            "otp_step": "start",
            "otp_data": {},
            "tts_text": "No problem, I've cancelled the payment. Is there anything else I can help you with?",
            "current_node": "otp", "active_flow": "",
        })

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

    # ── Step: Collecting card/bank number via DTMF or voice ───────────────────
    # BUG-024 + FEAT-006: Progressive capture — accept partial digits, confirm in groups
    if otp_step in ("collecting_card_dtmf", "collecting_bank_dtmf"):
        from utils.card_extractor import extract_card_digits
        is_card = otp_data.get("payment_type") == "card"
        collected = extract_card_digits(last_human)

        if not collected:
            existing = "".join(otp_data.get("card_groups", []))
            if existing:
                prompt = (f"I didn't catch that. I have {len(existing)} digits so far. "
                          f"Please say the next {16 - len(existing)} digits." if is_card
                          else "I didn't catch that. Please provide the remaining digits of your account number.")
            else:
                prompt = ("I'm sorry, I didn't catch that. Could you please provide your card number?"
                          if is_card
                          else "I'm sorry, I didn't catch that. Could you please provide your account number?")
            return {
                "otp_step":    otp_step,
                "otp_data":    otp_data,
                "tts_text":    prompt,
                "current_node": "otp", "active_flow": "otp",
            }

        if not is_card:
            # Bank account: no progressive capture (variable length)
            otp_data["account_number"] = collected
            otp_step = "dtmf_complete"
        else:
            # FEAT-006: Card progressive capture
            existing_groups = list(otp_data.get("card_groups", []))
            existing_digits = "".join(existing_groups)
            all_digits = existing_digits + collected

            if len(all_digits) >= 16:
                # Got enough — go straight to validation
                otp_data["card_number"] = all_digits
                otp_data.pop("card_groups", None)
                otp_step = "dtmf_complete"
            else:
                # Partial: store group and confirm
                existing_groups.append(collected)
                otp_data["card_groups"] = existing_groups
                total_so_far = len(all_digits)
                ordinal = _group_ordinal(total_so_far)
                return {
                    "otp_step": "confirming_card_group",
                    "otp_data": otp_data,
                    "tts_text": f"I received {ordinal}: {_spell_digits(collected)}. Is that correct?",
                    "current_node": "otp", "active_flow": "otp",
                }

    # ── Step: Confirm partial card group ────────────────────────────────────
    # FEAT-006: Per-group confirmation with correction handling
    if otp_step == "confirming_card_group":
        from utils.card_extractor import extract_card_digits
        groups = list(otp_data.get("card_groups", []))
        total_so_far = len("".join(groups))

        if _is_yes(last_human):
            remaining = 16 - total_so_far
            if remaining <= 0:
                # All 16 collected via groups — assemble and validate
                otp_data["card_number"] = "".join(groups)[:16]
                otp_data.pop("card_groups", None)
                otp_step = "dtmf_complete"
                # Fall through to dtmf_complete below
            else:
                if remaining <= 4:
                    prompt = f"Now the last {remaining} digits?"
                else:
                    prompt = f"Can you tell me the next {min(remaining, 4)} digits?"
                return {
                    "otp_step": "collecting_card_dtmf",
                    "otp_data": otp_data,
                    "tts_text": prompt,
                    "current_node": "otp", "active_flow": "otp",
                }
        elif _is_no(last_human):
            # Check if caller provided corrected digits in same utterance: "no, it's 1234"
            correction = extract_card_digits(last_human)
            if correction and groups:
                # Replace last group with correction
                groups[-1] = correction
                otp_data["card_groups"] = groups
                new_total = len("".join(groups))
                ordinal = _group_ordinal(new_total)
                return {
                    "otp_step": "confirming_card_group",
                    "otp_data": otp_data,
                    "tts_text": f"I have {ordinal}: {_spell_digits(correction)}. Is that correct?",
                    "current_node": "otp", "active_flow": "otp",
                }
            else:
                # No correction digits — discard last group and re-ask
                if groups:
                    groups.pop()
                otp_data["card_groups"] = groups
                prev_total = len("".join(groups))
                if prev_total > 0:
                    prompt = f"No problem. I have {prev_total} digits so far. Please say the next digits again."
                else:
                    prompt = "No problem. Let's start over. Please say the first digits of your card number."
                return {
                    "otp_step": "collecting_card_dtmf",
                    "otp_data": otp_data,
                    "tts_text": prompt,
                    "current_node": "otp", "active_flow": "otp",
                }
        else:
            return {
                "otp_step": "confirming_card_group",
                "otp_data": otp_data,
                "tts_text": "Please say yes if those digits are correct, or no to correct them.",
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
            # FEAT-005: Confirm card number with caller before proceeding
            return {
                "otp_step": "confirming_card",
                "otp_data": otp_data,
                "tts_text": f"I have your card number ending in {_spell_digits(last4)}. Is that correct?",
                "current_node": "otp", "active_flow": "otp",
            }
        else:
            # FEAT-005: Confirm bank account number before proceeding
            acct = otp_data.get("account_number", "")
            last4 = acct[-4:] if len(acct) >= 4 else acct
            return {
                "otp_step": "confirming_bank_account",
                "otp_data": otp_data,
                "tts_text": f"I have your account number ending in {_spell_digits(last4)}. Is that correct?",
                "current_node": "otp", "active_flow": "otp",
            }

    # ── Step: Confirm card number ──────────────────────────────────────────
    # FEAT-005: Per-field confirmation
    if otp_step == "confirming_card":
        if _is_yes(last_human):
            return {
                "otp_step": "collecting_card_expiry",
                "otp_data": otp_data,
                "tts_text": "Great. What is the expiry date? Please say the month and year, like June 2028.",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            otp_data.pop("card_number", None)
            otp_data.pop("card_retry_count", None)
            return {
                "otp_step": "collecting_card_dtmf",
                "otp_data": otp_data,
                "tts_text": "No problem. Please enter your 16-digit card number again.",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_card",
            "otp_data": otp_data,
            "tts_text": "Please say yes if the card number is correct, or no to re-enter it.",
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
        # FEAT-005: Confirm expiry
        return {
            "otp_step": "confirming_expiry",
            "otp_data": otp_data,
            "tts_text": f"Expiry date {_format_expiry_spoken(expiry)}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm expiry ───────────────────────────────────────────────
    if otp_step == "confirming_expiry":
        if _is_yes(last_human):
            return {
                "otp_step": "collecting_card_cvv",
                "otp_data": otp_data,
                "tts_text": "What is the 3-digit security code on the back of your card?",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            otp_data.pop("expiry", None)
            return {
                "otp_step": "collecting_card_expiry",
                "otp_data": otp_data,
                "tts_text": "No problem. What is the expiry date? Please say the month and year, like June 2028.",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_expiry",
            "otp_data": otp_data,
            "tts_text": "Please say yes if the expiry date is correct, or no to re-enter it.",
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
        # FEAT-005: Confirm CVV
        return {
            "otp_step": "confirming_cvv",
            "otp_data": otp_data,
            "tts_text": f"Security code {_spell_digits(cvv)}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm CVV ──────────────────────────────────────────────────
    if otp_step == "confirming_cvv":
        if _is_yes(last_human):
            return {
                "otp_step": "collecting_card_amount",
                "otp_data": otp_data,
                "tts_text": "How much would you like to pay today?",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            otp_data.pop("cvv", None)
            return {
                "otp_step": "collecting_card_cvv",
                "otp_data": otp_data,
                "tts_text": "No problem. What is the 3-digit security code on the back of your card?",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_cvv",
            "otp_data": otp_data,
            "tts_text": "Please say yes if the security code is correct, or no to re-enter it.",
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
        # FEAT-005: Confirm amount before processing
        return {
            "otp_step": "confirming_amount",
            "otp_data": otp_data,
            "tts_text": f"Payment amount ${amount:.2f}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm amount (card) ────────────────────────────────────────
    if otp_step == "confirming_amount":
        if _is_yes(last_human):
            # BUG-025: Process payment inline — don't wait for next turn
            result = await _process_payment(otp_data, policy_number, access_token)
            return _build_payment_result(result, otp_data)
        elif _is_no(last_human):
            otp_data.pop("amount", None)
            return {
                "otp_step": "collecting_card_amount",
                "otp_data": otp_data,
                "tts_text": "No problem. How much would you like to pay?",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_amount",
            "otp_data": otp_data,
            "tts_text": "Please say yes to confirm the amount, or no to change it.",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm bank account number ──────────────────────────────────
    if otp_step == "confirming_bank_account":
        if _is_yes(last_human):
            return {
                "otp_step": "collecting_bank_routing",
                "otp_data": otp_data,
                "tts_text": "Great. Now, what is your 9-digit routing number?",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            otp_data.pop("account_number", None)
            return {
                "otp_step": "collecting_bank_dtmf",
                "otp_data": otp_data,
                "tts_text": "No problem. Please enter your account number again.",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_bank_account",
            "otp_data": otp_data,
            "tts_text": "Please say yes if the account number is correct, or no to re-enter it.",
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
        # FEAT-005: Confirm routing number
        return {
            "otp_step": "confirming_routing",
            "otp_data": otp_data,
            "tts_text": f"Routing number {_spell_digits(routing)}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm routing number ───────────────────────────────────────
    if otp_step == "confirming_routing":
        if _is_yes(last_human):
            return {
                "otp_step": "collecting_bank_amount",
                "otp_data": otp_data,
                "tts_text": "How much would you like to pay today?",
                "current_node": "otp", "active_flow": "otp",
            }
        elif _is_no(last_human):
            otp_data.pop("routing_number", None)
            return {
                "otp_step": "collecting_bank_routing",
                "otp_data": otp_data,
                "tts_text": "No problem. What is your 9-digit routing number?",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_routing",
            "otp_data": otp_data,
            "tts_text": "Please say yes if the routing number is correct, or no to re-enter it.",
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
        # FEAT-005: Confirm bank amount
        return {
            "otp_step": "confirming_bank_amount",
            "otp_data": otp_data,
            "tts_text": f"Payment amount ${amount:.2f}. Is that correct?",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Confirm bank amount ──────────────────────────────────────────
    if otp_step == "confirming_bank_amount":
        if _is_yes(last_human):
            # BUG-025: Process payment inline — don't wait for next turn
            result = await _process_payment(otp_data, policy_number, access_token)
            return _build_payment_result(result, otp_data)
        elif _is_no(last_human):
            otp_data.pop("amount", None)
            return {
                "otp_step": "collecting_bank_amount",
                "otp_data": otp_data,
                "tts_text": "No problem. How much would you like to pay?",
                "current_node": "otp", "active_flow": "otp",
            }
        return {
            "otp_step": "confirming_bank_amount",
            "otp_data": otp_data,
            "tts_text": "Please say yes to confirm the amount, or no to change it.",
            "current_node": "otp", "active_flow": "otp",
        }

    # ── Step: Complete — handle repeat/follow-up requests ────────────────────
    if otp_step == "complete":
        # Allow caller to ask for confirmation number again
        lower = last_human.lower()
        if any(w in lower for w in ["repeat", "again", "confirmation", "number", "reference", "save"]):
            stored_tts = otp_data.get("last_confirmation_tts", "")
            if stored_tts:
                return {
                    "otp_step": "complete",
                    "otp_data": otp_data,
                    "tts_text": f"Let me repeat that for you. {stored_tts}",
                    "current_node": "otp", "active_flow": "otp",
                }
        # Anything else — clear flow and let router handle
        return {
            "otp_step": "start",
            "otp_data": {},
            "tts_text": "",
            "current_node": "otp", "active_flow": "",
        }

    tts_fallback = "Is there anything else I can help you with?"
    log_event(call_sid, "node_exit", node="otp",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts_fallback))
    return merge_auth_state(auth_state, {"tts_text": tts_fallback, "current_node": "otp", "active_flow": ""})


def _build_payment_result(result: dict, otp_data: dict) -> dict:
    """BUG-025: Build the final payment result inline after processing."""
    if result["success"]:
        confirmation = result.get("confirmation", "")
        payment_id = result.get("payment_id", "")
        tts = "Your payment has been processed successfully."
        if confirmation:
            tts += f" Your confirmation number is {_spell_alphanumeric(confirmation)}."
        if payment_id:
            tts += f" Your payment reference ID is {_spell_alphanumeric(payment_id)}."
        tts += " Please save these numbers for your records."
        tts += f" {PROMPTS['payment_disclosure']}"
        # Store confirmation text so caller can ask to repeat
        return {
            "otp_step": "complete",
            "otp_data": {"last_confirmation_tts": tts},
            "tts_text": tts,
            "current_node": "otp", "active_flow": "otp",
        }
    else:
        tts = f"I'm sorry, the payment could not be processed. {result.get('error', '')} Please try again or call back."
        return {
            "otp_step": "start",
            "otp_data": {},
            "tts_text": tts,
            "current_node": "otp", "active_flow": "",
        }


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
    FEAT-005: Smart 17→16 digit correction — try removing each duplicate digit.
    Returns a state dict to send back to the caller if validation fails,
    or None if the card is valid.
    """
    import re
    from utils.payment_validator import validate_card_number, luhn_check

    digits = re.sub(r"\D", "", card_number)
    retry_count = otp_data.get("card_retry_count", 0)

    ok, err = validate_card_number(card_number)
    if ok:
        otp_data.pop("card_retry_count", None)
        return None

    # FEAT-005: Smart correction for 17 digits (1 extra) — try removing each digit
    if len(digits) == 17:
        corrected = _try_correct_extra_digit(digits)
        if corrected:
            otp_data["card_number"] = corrected
            last4 = corrected[-4:]
            # Ask caller to confirm the corrected number
            return {
                "otp_step": "confirming_card",
                "otp_data": otp_data,
                "tts_text": f"I received 17 digits. I think you meant the card ending in {_spell_digits(last4)}. Is that correct?",
                "current_node": "otp", "active_flow": "otp",
            }

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
        hint = f"The number ending in {digits[-4:]} didn't pass verification. One or more digits may be incorrect."

    if retry_count >= 1:
        hint += " You can also enter the number using your keypad."

    return {
        "otp_step": "collecting_card_dtmf",
        "otp_data": otp_data,
        "tts_text": f"{hint} Please try entering your 16-digit card number again.",
        "current_node": "otp", "active_flow": "otp",
    }


def _try_correct_extra_digit(digits_17: str) -> str:
    """FEAT-005: Try removing one extra digit from a 17-digit string to get valid 16.

    Strategy: find consecutive duplicate digits and try removing one.
    If exactly one removal passes Luhn, return the corrected number.
    """
    from utils.payment_validator import luhn_check
    candidates = set()
    for i in range(len(digits_17)):
        # Only try removing a digit if it's the same as its neighbor (likely STT double-tap)
        if i > 0 and digits_17[i] == digits_17[i - 1]:
            candidate = digits_17[:i] + digits_17[i + 1:]
            if luhn_check(candidate):
                candidates.add(candidate)
        elif i < len(digits_17) - 1 and digits_17[i] == digits_17[i + 1]:
            candidate = digits_17[:i] + digits_17[i + 1:]
            if luhn_check(candidate):
                candidates.add(candidate)
    if len(candidates) == 1:
        return candidates.pop()
    return ""


def _spell_digits(digits: str) -> str:
    """Spell out digits for TTS clarity: '4444' → '4, 4, 4, 4'."""
    return ", ".join(digits)


def _extract_digits(utterance: str) -> str:
    """Extract digits from an utterance, converting STT word-digit confusions.

    Common STT mishearings: "to"→2, "too"→2, "for"→4, "won"→1, "ate"→8,
    "oh"→0, "zero"→0, "one"→1, "two"→2, etc.
    """
    import re
    text = _convert_word_digits(utterance)
    return re.sub(r"\D", "", text)


# Map of STT-confused words to digits — covers both number words and homophones
_WORD_TO_DIGIT = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2",
    "three": "3", "tree": "3",
    "four": "4", "for": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9",
}


def _convert_word_digits(utterance: str) -> str:
    """Replace spoken/misheard number words with digit characters."""
    import re
    words = re.split(r"(\s+)", utterance.lower())
    result = []
    for w in words:
        stripped = re.sub(r"[^a-z]", "", w)
        if stripped in _WORD_TO_DIGIT:
            result.append(_WORD_TO_DIGIT[stripped])
        else:
            result.append(w)
    return "".join(result)


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


def _group_ordinal(total_digits: int) -> str:
    """Return a contextual label for the digit group position."""
    if total_digits <= 4:
        return f"the first {total_digits} digits"
    elif total_digits < 16:
        return f"the next digits"
    else:
        return "the last digits"


def _format_expiry_spoken(expiry: str) -> str:
    """Convert MM/YY to spoken form: '06/28' → 'June 2028'."""
    import re
    m = re.match(r"(\d{1,2})/(\d{2,4})", expiry)
    if not m:
        return expiry
    month_num = int(m.group(1))
    year = m.group(2)
    if len(year) == 2:
        year = f"20{year}"
    months = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    if 1 <= month_num <= 12:
        return f"{months[month_num]} {year}"
    return expiry


def _spell_alphanumeric(code: str) -> str:
    """Spell out a confirmation code for TTS clarity: 'CNF-ABC123' → 'C N F dash A B C 1 2 3'."""
    parts = []
    for ch in code:
        if ch == "-":
            parts.append("dash")
        elif ch.isalpha():
            parts.append(ch.upper())
        elif ch.isdigit():
            parts.append(ch)
        else:
            parts.append(ch)
    return " ".join(parts)


def _wants_cancel(utterance: str) -> bool:
    """BUG-028: Detect if caller wants to cancel/exit the payment flow."""
    import re
    words = set(re.findall(r"[a-z]+", utterance.lower()))
    cancel_words = {"cancel", "stop", "exit", "quit", "nevermind", "abort"}
    if words & cancel_words:
        return True
    lower = utterance.lower()
    if "never mind" in lower or "don't want" in lower or "forget it" in lower:
        return True
    return False


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
