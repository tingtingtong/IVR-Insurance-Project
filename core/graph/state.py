from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


class CNOState(TypedDict):
    # ── Conversation ──────────────────────────────────────────────────────────
    messages:        Annotated[list, add_messages]  # full turn history
    call_sid:        str
    stream_sid:      str

    # ── Authentication ────────────────────────────────────────────────────────
    authenticated:   bool
    auth_step:       str   # collecting_phone | collecting_policy | collecting_dob
                           # collecting_name  | validating | complete | failed
                           # collecting_caller_name  ← post-auth persona identification
    auth_attempts:   int   # total failed auth attempts (max 3 → escalate)

    # ── Caller persona (populated after name collection post-auth) ─────────────
    caller_name:     str   # what the caller said their name is
    caller_persona:  str   # "payor" | "insured" | "owner" | "other" | "" (not yet set)

    # ── Customer (populated after successful auth) ────────────────────────────
    customer: dict
    # {
    #   firstName, lastName, policyNumber, dateOfBirth,
    #   phoneNumber, partyKey, companyCode, authStatus,
    #   noOfPII (int — how many PII pieces matched)
    # }
    access_token:    str
    finalized_party: dict  # raw API party record post-auth

    # ── PII being collected during auth ───────────────────────────────────────
    pii_collected: dict
    # { phoneNumber: "5551234567", policyNumber: "P300123456", ... }
    candidate_party: dict  # party returned by phone/policy search before full auth

    # ── Flow control ──────────────────────────────────────────────────────────
    current_intent:  str   # policy_info | payment | otp | loan | beneficiary
                           # contact_change | document | privacy | faq | escalate
    current_node:    str   # last node that ran
    active_flow:     str   # node currently "owning" the conversation
                           # context switch behaviour is governed by CONTEXT_SWITCH_CONFIG[active_flow]

    # ── Per-slot retry counters ───────────────────────────────────────────────
    slot_attempts: dict
    # { "slot_name": { "invalid": 0, "blank": 0, "no": 0 } }

    # ── Response fields ───────────────────────────────────────────────────────
    tts_text:        str   # text to speak to caller (after TTS normalization)
    transfer_to:     str   # phone number for Twilio <Dial> (escalation)

    # ── OTP / PCI state ──────────────────────────────────────────────────────
    otp_step:        str   # collecting_card | collecting_bank | confirming | processing
    otp_data:        dict  # { payment_type, amount, ... }

    # ── Metrics ───────────────────────────────────────────────────────────────
    metric_data: dict
    # { intentList: [...], apiCallsList: [...], piiSuccessList: [...] }
