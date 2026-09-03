from langchain_core.messages import AIMessage
from core.graph.state import CNOState
from core.tools.party_search import party_search, check_auth_success
from core.tools.auth_token import acquire_access_token
from core.prompts.retry_prompts import get_retry_prompt, PROMPTS
from utils.pii_validator import normalize_phone_with_hint, normalize_policy_number, normalize_dob
from utils.name_extractor import extract_name
from utils.date_utils import format_date_natural
from utils.idk_detector import is_idk
from utils.call_logger import log_event
from config import settings

MAX_AUTH_ATTEMPTS = settings.max_auth_attempts

_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def _spell_digits_tts(s: str) -> str:
    """Spell digits as words for TTS (e.g. '3456' → 'three four five six')."""
    return " ".join(_DIGIT_WORDS.get(c, c) for c in s)


async def auth_node(state: CNOState) -> dict:
    """
    Multi-turn authentication node.

    State machine:
      collecting_phone       → phone captured → search by phone
                               found  → collecting_dob
                               not found → confirming_phone
      confirming_phone       → yes → collecting_policy | no → collecting_phone
      collecting_policy      → policy captured → search by policy
                               found → collecting_dob | not found → escalate
      collecting_dob         → DOB parsed → confirming_dob
      confirming_dob         → yes → check auth (phone/policy + DOB)
                               match → complete
                               no match → collecting_name
                               no  → collecting_dob
      collecting_name        → LLM extract → check auth (phone/policy + name)
                               match → complete | no match → escalate
      collecting_caller_name → post-auth: ask caller's name → match against personas
                               matched  → caller_persona set → auth fully done
                               no match → caller_persona = "other" → offer transfer
    """
    auth_step       = state.get("auth_step", "collecting_phone")
    pii_collected   = dict(state.get("pii_collected", {}))
    auth_attempts   = state.get("auth_attempts", 0)
    messages        = state.get("messages", [])
    candidate_party = dict(state.get("candidate_party", {}))
    call_sid        = state.get("call_sid", "unknown")

    log_event(call_sid, "node_enter", node="auth", auth_step=auth_step)

    # ── Terminal states ───────────────────────────────────────────────────────
    if auth_step == "complete":
        # If caller_persona is already set this is a re-entry from router — nothing to do.
        # If not set yet, transition to caller-name collection immediately.
        caller_persona = state.get("caller_persona", "")
        if caller_persona:
            return {"active_flow": ""}
        # Persona not yet collected — pivot to name-collection sub-step
        auth_step = "collecting_caller_name"

    if auth_step == "failed" or auth_attempts >= MAX_AUTH_ATTEMPTS:
        return {
            "auth_step":    "failed",
            "tts_text":     PROMPTS["escalation"]["max_attempts"],
            "transfer_to":  "",
            "current_node": "auth",
            "active_flow":  "",
        }

    # ── Last human utterance ──────────────────────────────────────────────────
    last_human = ""
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            last_human = msg.content.strip()
            break

    # ── Idempotency guard: re-prompt current step if input is empty ───────────
    # Prevents parsing blank input when auth_step is already set (e.g. after
    # a timeout or empty utterance mid-flow). Each step maps to its ask prompt.
    _STEP_REPROMPT = {
        "collecting_phone":          lambda: get_retry_prompt("phone", "ask"),
        "confirming_phone":          lambda: PROMPTS["confirming_phone"]["ask"].format(
            phone=_fmt_phone(pii_collected.get("phoneNumber", ""))),
        "collecting_policy":         lambda: get_retry_prompt("policy_number", "ask"),
        "collecting_dob":            lambda: get_retry_prompt("date_of_birth", "ask"),
        "confirming_dob":            lambda: PROMPTS["confirming_dob"]["ask"].format(
            dob=pii_collected.get("dateOfBirth", "")),
        "collecting_name":           lambda: get_retry_prompt("insured_name", "ask"),
        "collecting_policy_selection": lambda: "Please say the last 4 digits of your policy number.",
        "collecting_caller_name":    lambda: "May I ask your name please?",
    }
    if not last_human and auth_step in _STEP_REPROMPT:
        reprompt = _STEP_REPROMPT[auth_step]()
        return {
            "auth_step":       auth_step,
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        reprompt,
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # ── Dispatch ─────────────────────────────────────────────────────────────
    if auth_step == "collecting_phone":
        result = await _collecting_phone(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "confirming_phone":
        result = _confirming_phone(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "collecting_policy":
        result = await _collecting_policy(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "collecting_dob":
        result = _collecting_dob(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "confirming_dob":
        result = _confirming_dob(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "collecting_name":
        result = await _collecting_name(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "collecting_policy_selection":
        result = _collecting_policy_selection(state, last_human, pii_collected, auth_attempts, candidate_party)
    elif auth_step == "collecting_caller_name":
        # Post-auth persona identification — ask caller's name and match against policy personas
        result = _collecting_caller_name(state, last_human)
    elif auth_step == "failed":
        # Already escalated — re-confirm transfer (don't restart from phone)
        return {
            "auth_step":    "failed",
            "tts_text":     PROMPTS["escalation"]["max_attempts"],
            "current_node": "auth",
            "active_flow":  "",
            "transfer_to":  settings.twilio_agent_phone_number,
        }
    else:
        # Unknown state — restart from phone
        result = _ask("collecting_phone", pii_collected, get_retry_prompt("phone", "ask"))

    # ── Structured logging for auth events ───────────────────────────────────
    new_step = result.get("auth_step", auth_step)
    if new_step != auth_step:
        log_event(call_sid, "auth_step_change", from_step=auth_step, to_step=new_step)
    if new_step == "complete" or (new_step == "collecting_caller_name" and result.get("authenticated")):
        caller_name = result.get("caller_name", "") or state.get("caller_name", "")
        persona = result.get("caller_persona", "") or state.get("caller_persona", "")
        if result.get("authenticated") or state.get("authenticated"):
            log_event(call_sid, "auth_complete", caller_name=caller_name, persona=persona)
    if new_step == "failed":
        log_event(call_sid, "auth_failed", attempts=auth_attempts)

    # ── Token acquisition on identity verification ────────────────────────────
    # Acquire a session access token as soon as PII identity is verified.
    # This happens at the "collecting_caller_name" transition (formerly "complete"),
    # before the caller-name step runs. Every downstream API call requires the token.
    # Treat a failed token request as an escalation — better to transfer than
    # proceed with empty credentials and silently fail on every API call.
    #
    # We also acquire on "complete" for the re-entry guard path (safety net), but
    # only if no token is already stored in state.
    needs_token = (
        result.get("auth_step") == "collecting_caller_name" and result.get("authenticated")
        or result.get("auth_step") == "complete" and result.get("authenticated") and not state.get("access_token")
    )
    if needs_token:
        customer = result.get("customer") or state.get("customer", {})
        token = await acquire_access_token(
            party_key=customer.get("partyKey", ""),
            company_code=customer.get("companyCode", ""),
        )
        if not token:
            return {
                "auth_step":    "failed",
                "tts_text":     PROMPTS["escalation"]["error"],
                "current_node": "auth",
                "active_flow":  "",
                "transfer_to":  "",
            }
        result["access_token"] = token

    return result


# ── Step handlers ─────────────────────────────────────────────────────────────

async def _collecting_phone(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "phone"
    call_sid = state.get("call_sid", "unknown")

    if not last_human:
        log_event(call_sid, "auth_detail", step="collecting_phone", action="reprompt_empty")
        return _ask("collecting_phone", pii_collected, get_retry_prompt(slot, "ask"))

    # IDK: caller doesn't know their phone → skip straight to policy number
    if is_idk(last_human):
        log_event(call_sid, "auth_detail", step="collecting_phone", action="idk_skip_to_policy")
        tts = "No problem. " + get_retry_prompt("policy_number", "ask")
        return {
            "auth_step":       "collecting_policy",
            "pii_collected":   pii_collected,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    digits, hint = normalize_phone_with_hint(last_human)
    log_event(call_sid, "auth_detail", step="collecting_phone", action="parse_phone",
              input=last_human[:30], digits=digits[:6] + "****" if digits else "", parsed=bool(digits))

    if not digits:
        blank_count  = _get_slot(state, slot, "blank")
        asked_before = _get_slot(state, slot, "asked")

        if blank_count >= 2:
            return _escalate(state)

        if not asked_before:
            tts = get_retry_prompt(slot, "ask")
            return {**_ask("collecting_phone", pii_collected, tts),
                    "slot_attempts": _inc_slot(state, slot, "asked")}

        # Use length-aware hint when available
        tts = hint if hint else get_retry_prompt(slot, "blank", blank_count)
        return {
            "auth_step":     "collecting_phone",
            "pii_collected": pii_collected,
            "candidate_party": {},
            "tts_text":      tts,
            "slot_attempts": _inc_slot(state, slot, "blank"),
            "current_node":  "auth",
            "active_flow":   "auth",
        }

    pii_collected["phoneNumber"] = digits
    result = await party_search(phone=digits)
    log_event(call_sid, "auth_detail", step="collecting_phone", action="party_search",
              found=bool(result["success"] and result.get("parties")),
              party_count=len(result.get("parties", [])))

    if result["success"] and result["parties"]:
        found = result["parties"][0]
        tts = "Thank you. " + get_retry_prompt("date_of_birth", "ask")
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   pii_collected,
            "candidate_party": found,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    if not result["success"]:
        # API error (network, timeout, etc.) — skip phone confirmation and go straight
        # to policy number so the caller isn't asked to confirm digits we couldn't check.
        tts = "No problem. " + get_retry_prompt("policy_number", "ask")
        return {
            "auth_step":       "collecting_policy",
            "pii_collected":   pii_collected,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # Phone not in system — read it back and ask to confirm (caller may have mis-spoken)
    formatted = _fmt_phone(digits)
    tts = PROMPTS["confirming_phone"]["ask"].format(phone=formatted)
    return {
        "auth_step":       "confirming_phone",
        "pii_collected":   pii_collected,
        "candidate_party": {},
        "tts_text":        tts,
        "slot_attempts":   {},
        "current_node":    "auth",
        "active_flow":     "auth",
    }


def _confirming_phone(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "confirming_phone"

    if not last_human:
        formatted = _fmt_phone(pii_collected.get("phoneNumber", ""))
        tts = PROMPTS["confirming_phone"]["ask"].format(phone=formatted)
        return _ask("confirming_phone", pii_collected, tts)

    # IDK at confirming_phone → caller unsure if phone is right → go to policy
    if is_idk(last_human):
        tts = "No problem. " + get_retry_prompt("policy_number", "ask")
        return {
            "auth_step":       "collecting_policy",
            "pii_collected":   pii_collected,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    answer = _yes_no(last_human)

    if answer == "yes":
        tts = "Thank you. " + get_retry_prompt("policy_number", "ask")
        return {
            "auth_step":       "collecting_policy",
            "pii_collected":   pii_collected,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    if answer == "no":
        new_pii = {k: v for k, v in pii_collected.items() if k != "phoneNumber"}
        tts = PROMPTS["confirming_phone"]["no"][0] + " " + get_retry_prompt("phone", "ask")
        return {
            "auth_step":       "collecting_phone",
            "pii_collected":   new_pii,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # Unclear response
    blank_count  = _get_slot(state, slot, "blank")
    asked_before = _get_slot(state, slot, "asked")

    if blank_count >= 2:
        return _escalate(state)

    if not asked_before:
        formatted = _fmt_phone(pii_collected.get("phoneNumber", ""))
        tts = PROMPTS["confirming_phone"]["ask"].format(phone=formatted)
        return {**_ask("confirming_phone", pii_collected, tts),
                "slot_attempts": _inc_slot(state, slot, "asked")}

    tts = PROMPTS["confirming_phone"]["blank"][min(blank_count, 1)]
    return {
        "auth_step":       "confirming_phone",
        "pii_collected":   pii_collected,
        "candidate_party": candidate_party,
        "tts_text":        tts,
        "slot_attempts":   _inc_slot(state, slot, "blank"),
        "current_node":    "auth",
        "active_flow":     "auth",
    }


async def _collecting_policy(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "policy_number"

    if not last_human:
        return _ask("collecting_policy", pii_collected, get_retry_prompt(slot, "ask"))

    # IDK: caller doesn't know their policy number → escalate to agent
    if is_idk(last_human):
        return _escalate(state)

    parsed = normalize_policy_number(last_human)

    if not parsed:
        blank_count  = _get_slot(state, slot, "blank")
        asked_before = _get_slot(state, slot, "asked")

        if blank_count >= 2:
            return _escalate(state)

        if not asked_before:
            tts = get_retry_prompt(slot, "ask")
            return {**_ask("collecting_policy", pii_collected, tts),
                    "slot_attempts": _inc_slot(state, slot, "asked")}

        tts = get_retry_prompt(slot, "blank", blank_count)
        return {
            "auth_step":       "collecting_policy",
            "pii_collected":   pii_collected,
            "candidate_party": {},
            "tts_text":        tts,
            "slot_attempts":   _inc_slot(state, slot, "blank"),
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    pii_collected["policyNumber"] = parsed
    result = await party_search(policy_number=parsed)

    if result["success"] and result["parties"]:
        found = result["parties"][0]
        tts = "Thank you. " + get_retry_prompt("date_of_birth", "ask")
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   pii_collected,
            "candidate_party": found,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # Neither phone nor policy found → escalate
    return _escalate(state)


def _collecting_dob(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "date_of_birth"
    call_sid = state.get("call_sid", "unknown")

    if not last_human:
        log_event(call_sid, "auth_detail", step="collecting_dob", action="reprompt_empty")
        return {**_ask("collecting_dob", pii_collected, get_retry_prompt(slot, "ask")),
                "candidate_party": candidate_party}

    # IDK: caller doesn't know DOB → fall through to name verification
    if is_idk(last_human):
        tts = "No problem. " + get_retry_prompt("insured_name", "ask")
        return {
            "auth_step":       "collecting_name",
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    parsed = normalize_dob(last_human)
    log_event(call_sid, "auth_detail", step="collecting_dob", action="parse_dob",
              input=last_human[:40], parsed=parsed or "FAILED")

    if not parsed:
        blank_count  = _get_slot(state, slot, "blank")
        asked_before = _get_slot(state, slot, "asked")

        if blank_count >= 2:
            return _escalate(state)

        if not asked_before:
            tts = get_retry_prompt(slot, "ask")
            return {**_ask("collecting_dob", pii_collected, tts, candidate_party),
                    "slot_attempts": _inc_slot(state, slot, "asked")}

        tts = get_retry_prompt(slot, "blank", blank_count)
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   _inc_slot(state, slot, "blank"),
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # DOB parsed — check auth immediately (no read-back confirmation step)
    pii_collected["dateOfBirth"] = parsed
    match = check_auth_success(candidate_party, pii_collected)
    expected_dob = candidate_party.get("DOB", "unknown")
    log_event(call_sid, "auth_detail", step="collecting_dob", action="dob_verify",
              given=parsed, expected=expected_dob[:7] + "**", match=match)
    if match:
        policy_numbers = _get_policy_numbers(candidate_party)
        if len(policy_numbers) > 1:
            return _ask_policy_selection(candidate_party, pii_collected, policy_numbers)
        return _auth_complete(candidate_party, pii_collected)

    # DOB doesn't match — allow one retry before falling through to name
    dob_mismatch_count = _get_slot(state, "dob_mismatch", "invalid")
    log_event(call_sid, "auth_detail", step="collecting_dob", action="dob_mismatch",
              attempt=dob_mismatch_count + 1, will_retry=dob_mismatch_count == 0)
    if dob_mismatch_count == 0:
        # First mismatch — give the caller another chance
        new_pii = {k: v for k, v in pii_collected.items() if k != "dateOfBirth"}
        tts = (
            "That date of birth doesn't match our records. "
            "Could you please try again? What is the insured's date of birth?"
        )
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   new_pii,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   _inc_slot(state, "dob_mismatch", "invalid"),
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # Second mismatch — fall through to name verification
    tts = "I still wasn't able to verify that date. " + get_retry_prompt("insured_name", "ask")
    return {
        "auth_step":       "collecting_name",
        "pii_collected":   pii_collected,
        "candidate_party": candidate_party,
        "tts_text":        tts,
        "slot_attempts":   {},
        "current_node":    "auth",
        "active_flow":     "auth",
    }


def _confirming_dob(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "confirming_dob"

    if not last_human:
        formatted = format_date_natural(pii_collected.get("dateOfBirth", ""))
        tts = PROMPTS["confirming_dob"]["ask"].format(dob=formatted)
        return {**_ask("confirming_dob", pii_collected, tts), "candidate_party": candidate_party}

    # IDK at confirming_dob → caller unsure if the date read back is right → re-ask DOB
    if is_idk(last_human):
        new_pii = {k: v for k, v in pii_collected.items() if k != "dateOfBirth"}
        tts = "No problem, please say the date of birth again."
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   new_pii,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    answer = _yes_no(last_human)

    if answer == "yes":
        if check_auth_success(candidate_party, pii_collected):
            policy_numbers = _get_policy_numbers(candidate_party)
            if len(policy_numbers) > 1:
                return _ask_policy_selection(candidate_party, pii_collected, policy_numbers)
            return _auth_complete(candidate_party, pii_collected)
        # DOB confirmed but doesn't match → try name
        tts = "I wasn't able to verify that date. " + get_retry_prompt("insured_name", "ask")
        return {
            "auth_step":       "collecting_name",
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    if answer == "no":
        new_pii = {k: v for k, v in pii_collected.items() if k != "dateOfBirth"}
        tts = PROMPTS["confirming_dob"]["no"][0] + " " + get_retry_prompt("date_of_birth", "ask")
        return {
            "auth_step":       "collecting_dob",
            "pii_collected":   new_pii,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   {},
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    # Unclear
    blank_count  = _get_slot(state, slot, "blank")
    asked_before = _get_slot(state, slot, "asked")

    if blank_count >= 2:
        return _escalate(state)

    if not asked_before:
        formatted = format_date_natural(pii_collected.get("dateOfBirth", ""))
        tts = PROMPTS["confirming_dob"]["ask"].format(dob=formatted)
        return {**_ask("confirming_dob", pii_collected, tts, candidate_party),
                "slot_attempts": _inc_slot(state, slot, "asked")}

    tts = PROMPTS["confirming_dob"]["blank"][min(blank_count, 1)]
    return {
        "auth_step":       "confirming_dob",
        "pii_collected":   pii_collected,
        "candidate_party": candidate_party,
        "tts_text":        tts,
        "slot_attempts":   _inc_slot(state, slot, "blank"),
        "current_node":    "auth",
        "active_flow":     "auth",
    }


async def _collecting_name(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "insured_name"

    if not last_human:
        return {**_ask("collecting_name", pii_collected, get_retry_prompt(slot, "ask")),
                "candidate_party": candidate_party}

    # IDK: caller doesn't know name (last fallback step) → escalate
    if is_idk(last_human):
        return _escalate(state)

    first, last = await extract_name(last_human)

    if not first or not last:
        blank_count  = _get_slot(state, slot, "blank")
        asked_before = _get_slot(state, slot, "asked")

        if blank_count >= 2:
            return _escalate(state)

        if not asked_before:
            tts = get_retry_prompt(slot, "ask")
            return {**_ask("collecting_name", pii_collected, tts, candidate_party),
                    "slot_attempts": _inc_slot(state, slot, "asked")}

        tts = get_retry_prompt(slot, "blank", blank_count)
        return {
            "auth_step":       "collecting_name",
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   _inc_slot(state, slot, "blank"),
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    pii_collected["firstName"] = first
    pii_collected["lastName"]  = last

    if check_auth_success(candidate_party, pii_collected):
        policy_numbers = _get_policy_numbers(candidate_party)
        if len(policy_numbers) > 1:
            return _ask_policy_selection(candidate_party, pii_collected, policy_numbers)
        return _auth_complete(candidate_party, pii_collected)

    # Both DOB and name failed → escalate
    return _escalate(state)


# ── Caller persona step ───────────────────────────────────────────────────────

import re as _re
_NAME_PREFIX_RE = _re.compile(
    r"^(?:it'?s|this\s+is|i\s+am|i'?m|my\s+name\s+is|the\s+name\s+is|"
    r"name\s+is|it\s+is|this\s+here\s+is|speaking\s+is|this\s+would\s+be)\s+",
    _re.IGNORECASE,
)


def _clean_caller_name(utterance: str) -> str:
    """Strip conversational lead-ins like 'It\\'s', 'I am', 'My name is' from name input."""
    clean = _NAME_PREFIX_RE.sub("", utterance.strip())
    return clean.rstrip(".").strip()


def _collecting_caller_name(state: CNOState, last_human: str) -> dict:
    """
    Post-auth step: ask the caller their name, then match it against the policy
    persona list (finalized_party["Personas"]).

    On first entry last_human is typically empty (we just set auth_step mid-node),
    so we ask the question. On the second turn we receive the name and match it.

    Distinct from "collecting_name" (which is the PII-auth identity fallback).
    """
    finalized_party = state.get("finalized_party", {})
    personas = finalized_party.get("Personas", [])

    # No utterance yet on this turn — ask the caller their name
    if not last_human:
        return {
            "auth_step":    "collecting_caller_name",
            "tts_text":     "May I ask your name please?",
            "current_node": "auth",
            "active_flow":  "auth",
        }

    # IDK / refuses to give name → treat as "other" persona
    if is_idk(last_human) or any(p in last_human.lower() for p in (
        "i don't want", "i dont want", "prefer not", "rather not", "won't say", "wont say"
    )):
        return _persona_identified("other", last_human, personas)

    # Strip conversational prefixes before storing and matching the name
    clean_name = _clean_caller_name(last_human)
    role = _match_persona(clean_name, personas)
    return _persona_identified(role, clean_name, personas)


def _match_persona(caller_name: str, personas: list) -> str:
    """
    Match caller_name against each persona in the list.
    Normalizes both strings: lowercase, strips punctuation.
    Partial match OK — "John" matches "John Smith".
    Returns the role of the first match, or "other" if none.
    """
    import re

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

    caller_norm = _norm(caller_name)
    caller_parts = caller_norm.split()

    for persona in personas:
        persona_norm = _norm(persona.get("name", ""))
        persona_parts = persona_norm.split()
        # Match if any word the caller said appears in the persona name
        # (e.g. "John" matches "john smith"; "Smith Corp" matches "smith corp")
        if any(part in persona_parts for part in caller_parts if len(part) > 1):
            return persona.get("role", "other")

    return "other"


def _persona_identified(role: str, caller_name: str, personas: list) -> dict:
    """Build the state update after persona identification is complete."""
    if role in ("payor", "insured", "owner"):
        tts = f"Thank you, {caller_name.title()}."
        return {
            "auth_step":      "complete",
            "caller_name":    caller_name,
            "caller_persona": role,
            "tts_text":       tts,
            "current_node":   "auth",
            "active_flow":    "",
        }
    else:
        # "other" — cannot access restricted policy information
        tts = (
            "Thank you. For security purposes, I can only share detailed policy "
            "information with the policyholder, owner, or payor on the account. "
            "I can connect you with a representative who can assist you. "
            "Would you like me to transfer you?"
        )
        return {
            "auth_step":      "complete",
            "caller_name":    caller_name,
            "caller_persona": "other",
            "tts_text":       tts,
            "current_node":   "auth",
            "active_flow":    "",
        }


# ── Shared helpers ────────────────────────────────────────────────────────────

def _ask(step: str, pii_collected: dict, tts: str, candidate_party: dict = None) -> dict:
    return {
        "auth_step":       step,
        "pii_collected":   pii_collected,
        "candidate_party": candidate_party or {},
        "tts_text":        tts,
        "current_node":    "auth",
        "active_flow":     "auth",
    }


def _auth_complete(party: dict, pii_collected: dict, policy_number: str = "") -> dict:
    customer = _build_customer(party, policy_number)
    # Transition directly to caller-name collection rather than sending the old
    # "authenticated" greeting. The persona step will greet the caller by name once
    # their identity on the policy is established. auth_step stays "collecting_caller_name"
    # so _route_after_auth holds in the auth node for the next turn.
    name_ask = "I've verified your identity. May I ask your name please?"
    return {
        "authenticated":    True,
        "auth_step":        "collecting_caller_name",
        "caller_name":      "",
        "caller_persona":   "",
        "customer":         customer,
        "finalized_party":  party,
        "candidate_party":  {},
        "pii_collected":    pii_collected,
        "tts_text":         name_ask,
        "current_node":     "auth",
        "active_flow":      "auth",
        "messages":         [AIMessage(content=name_ask)],
    }


def _escalate(state: CNOState) -> dict:
    return {
        "auth_step":    "failed",
        "auth_attempts": settings.max_auth_attempts,  # prevent re-entry restart
        "tts_text":     PROMPTS["escalation"]["max_attempts"],
        "current_node": "auth",
        "active_flow":  "",
        "transfer_to":  settings.twilio_agent_phone_number,
    }


def _get_policy_numbers(party: dict) -> list:
    return [p.get("PolicyNumber", "") for p in party.get("Policies", [])]


def _ask_policy_selection(candidate_party: dict, pii_collected: dict, policy_numbers: list) -> dict:
    last_fours = ", or ".join(_spell_digits_tts(p[-4:]) for p in policy_numbers if len(p) >= 4)
    tts = (
        "We found multiple policies on your account. "
        f"Please say the last 4 digits of the policy you'd like to access. "
        f"Your options are: {last_fours}."
    )
    return {
        "auth_step":       "collecting_policy_selection",
        "pii_collected":   pii_collected,
        "candidate_party": candidate_party,
        "tts_text":        tts,
        "slot_attempts":   {},
        "current_node":    "auth",
        "active_flow":     "auth",
    }


def _collecting_policy_selection(state, last_human, pii_collected, auth_attempts, candidate_party):
    slot = "policy_selection"
    policy_numbers = _get_policy_numbers(candidate_party)

    if not last_human:
        return _ask_policy_selection(candidate_party, pii_collected, policy_numbers)

    digits = "".join(c for c in last_human if c.isdigit())
    matched = next(
        (p for p in policy_numbers if len(digits) >= 4 and p.endswith(digits[-4:])),
        None,
    )

    if not matched:
        blank_count = _get_slot(state, slot, "blank")
        if blank_count >= 2:
            return _escalate(state)
        last_fours = ", or ".join(_spell_digits_tts(p[-4:]) for p in policy_numbers if len(p) >= 4)
        tts = f"I didn't catch that. Please say the last 4 digits of the policy. Your options are: {last_fours}."
        return {
            "auth_step":       "collecting_policy_selection",
            "pii_collected":   pii_collected,
            "candidate_party": candidate_party,
            "tts_text":        tts,
            "slot_attempts":   _inc_slot(state, slot, "blank"),
            "current_node":    "auth",
            "active_flow":     "auth",
        }

    return _auth_complete(candidate_party, pii_collected, policy_number=matched)


def _fmt_phone(digits: str) -> str:
    """Format 10-digit phone for TTS read-back: '5551234567' → '555-123-4567'."""
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def _yes_no(utterance: str) -> str:
    """Parse yes/no from caller utterance. Returns 'yes', 'no', or ''."""
    import re
    lower = utterance.lower().strip()
    words = set(re.sub(r"[^a-z0-9 ]", "", lower).split())
    yes_words = {"yes", "correct", "right", "yeah", "yep", "sure", "confirm", "yup", "affirmative"}
    no_words  = {"no", "wrong", "incorrect", "nope", "negative"}
    if words & yes_words:
        return "yes"
    if words & no_words:
        return "no"
    if any(p in lower for p in ("that's right", "that is correct", "sounds right", "sounds good")):
        return "yes"
    if any(p in lower for p in ("that's wrong", "that is wrong", "not right", "not correct")):
        return "no"
    return ""


def _build_customer(party: dict, policy_number: str = "") -> dict:
    policies = party.get("Policies", [])
    policy_numbers = [p.get("PolicyNumber", "") for p in policies]
    phones = party.get("PhoneNumbers") or [{}]
    # Always a single string — caller passes selected policy when multi-policy
    resolved = policy_number if policy_number else (policy_numbers[0] if policy_numbers else "")
    return {
        "firstName":    party.get("FirstName", ""),
        "lastName":     party.get("LastName", ""),
        "policyNumber": resolved,
        "dateOfBirth":  party.get("DOB", ""),
        "phoneNumber":  phones[0].get("PhoneNumber", "") if phones else "",
        "partyKey":     party.get("PartyCalrKeyCode", ""),
        "companyCode":  party.get("CompanyCode", ""),
        "authStatus":   "authenticated",
        "noOfPII":      0,
    }


def _get_slot(state: CNOState, slot: str, key: str) -> int:
    return state.get("slot_attempts", {}).get(slot, {}).get(key, 0)


def _inc_slot(state: CNOState, slot: str, key: str) -> dict:
    attempts = dict(state.get("slot_attempts", {}))
    if slot not in attempts:
        attempts[slot] = {"invalid": 0, "blank": 0, "asked": 0}
    attempts[slot][key] = attempts[slot].get(key, 0) + 1
    return attempts
