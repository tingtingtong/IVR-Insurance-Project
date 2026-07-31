# Retry verbiages ported from insuranceCompany UAT response pool
# Pattern: PROMPTS[slot][attempt_type][attempt_number]

PROMPTS: dict = {

    # ── Phone Number ──────────────────────────────────────────────────────────
    "phone": {
        "ask":     "What is the 10-digit phone number associated with your policy?",
        "invalid": [
            "I'm sorry, I didn't catch that. Please say your 10-digit phone number, including area code.",
            "I still didn't get that. Please say each digit of your phone number slowly.",
            "I'm having trouble understanding. Let me connect you to a representative.",
        ],
        "blank": [
            "I didn't hear anything. What is your phone number?",
            "I still didn't hear a response. If you need more time, that's okay — what is your phone number?",
        ],
    },

    # ── Policy Number ─────────────────────────────────────────────────────────
    "policy_number": {
        "ask":     "What is your policy number? It's on your policy documents.",
        "invalid": [
            "I'm sorry, I didn't recognize that as a policy number. Please say each character slowly.",
            "I still didn't catch that. Your policy number is on the top of your billing statement.",
            "I'm having trouble with that. Let me transfer you to an agent who can help.",
        ],
        "blank": [
            "I didn't hear a policy number. Please say your policy number.",
            "I still didn't catch a response. Your policy number starts with a letter followed by digits.",
        ],
    },

    # ── Date of Birth ─────────────────────────────────────────────────────────
    "date_of_birth": {
        "ask":     "What is the insured's date of birth?",
        "invalid": [
            "I'm sorry, I didn't catch that. Please say the month, day, and year of birth.",
            "I still didn't get that. For example, say January twenty-second, nineteen seventy-eight.",
            "I'm having trouble with that. Let me transfer you to an agent.",
        ],
        "blank": [
            "I didn't hear a date of birth. What is the insured's date of birth?",
            "I still didn't hear a response. Please say the month, day, and year.",
        ],
    },

    # ── Insured Name ──────────────────────────────────────────────────────────
    "insured_name": {
        "ask":     "What is the first and last name of the insured?",
        "invalid": [
            "I'm sorry, I didn't catch that. Please say the insured's first and last name.",
            "I still didn't get the name. Please spell it out if needed.",
            "I'm having trouble. Let me connect you to a representative.",
        ],
        "blank": [
            "I didn't hear a name. What is the insured's first and last name?",
            "I still didn't catch a response. Please say the insured's full name.",
        ],
    },

    # ── Zip Code ──────────────────────────────────────────────────────────────
    "zipcode": {
        "ask":     "What is the zip code on the policy?",
        "invalid": [
            "I'm sorry, I didn't get that. Please say your 5-digit zip code.",
            "I still didn't catch that. Please say each digit of your zip code.",
            "I'm having trouble. Let me transfer you to an agent.",
        ],
        "blank": [
            "I didn't hear a zip code. What is the zip code on the policy?",
            "I still didn't hear a response. Please say your 5-digit zip code.",
        ],
    },

    # ── Phone read-back confirmation ──────────────────────────────────────────
    # Use as: PROMPTS["confirming_phone"]["ask"].format(phone="555-123-4567")
    "confirming_phone": {
        "ask": "I heard {phone}. Is that correct? Say yes to confirm or no to say it again.",
        "blank": [
            "I didn't hear a response. Please say yes to confirm or no to try again.",
            "I still didn't hear you. Please say yes or no.",
        ],
        "no": [
            "No problem. Let me ask again.",
        ],
    },

    # ── Date of birth read-back confirmation ──────────────────────────────────
    # Use as: PROMPTS["confirming_dob"]["ask"].format(dob="January 22nd, 1978")
    "confirming_dob": {
        "ask": "I heard {dob}. Is that correct? Say yes to confirm or no to say it again.",
        "blank": [
            "I didn't hear a response. Please say yes or no.",
            "I still didn't hear you. Please say yes to confirm or no to try again.",
        ],
        "no": [
            "No problem. Let me ask for the date of birth again.",
        ],
    },

    # ── General confirmation (yes/no) ─────────────────────────────────────────
    "confirm": {
        "ask":     "Is that correct?",
        "invalid": [
            "I'm sorry, I didn't catch that. Please say yes or no.",
            "I still didn't understand. Please say yes to confirm or no to try again.",
            "I'm having trouble. Let me transfer you to a representative.",
        ],
        "blank": [
            "I didn't hear a response. Please say yes or no.",
            "I still didn't hear you. Say yes to confirm or no to try again.",
        ],
        "no": [
            "No problem. Let's try that again.",
            "I understand. Let me transfer you to someone who can assist you.",
        ],
    },

    # ── Draft Day ─────────────────────────────────────────────────────────────
    "draft_day": {
        "ask":     "What day of the month would you like payments drafted? You can choose between the 3rd and 28th.",
        "invalid": [
            "I'm sorry, the draft day must be between the 3rd and 28th of the month. What day would you prefer?",
            "I still didn't get a valid day. Please say a number between 3 and 28.",
            "I'm having trouble. Let me transfer you to an agent.",
        ],
        "blank": [
            "I didn't hear a day. What day between the 3rd and 28th would you like?",
            "I still didn't catch a response. Please say a day between 3 and 28.",
        ],
    },

    # ── Escalation / Max attempts ─────────────────────────────────────────────
    "escalation": {
        "max_attempts": "I'm sorry, I wasn't able to verify your information. Let me transfer you to a representative who can help.",
        "caller_request": "Of course. Let me connect you with a representative right away.",
        "error": "I'm sorry, we're experiencing a technical issue. Let me transfer you to a representative.",
        "out_of_scope": "I'm not able to help with that over the phone. Let me connect you with a representative.",
    },

    # ── Greetings ─────────────────────────────────────────────────────────────
    "greeting": {
        "welcome": "Thank you for calling insuranceCompany. How can I help you today?",
        "authenticated": "I've verified your identity. How can I help you today?",
        "auth_failed": "I'm sorry, I wasn't able to verify your information after several attempts.",
    },

    # ── Closings ──────────────────────────────────────────────────────────────
    "closing": {
        "anything_else": "Is there anything else I can help you with today?",
        "goodbye":       "Thank you for calling insuranceCompany. Have a great day. Goodbye.",
        "transfer_intro": "Please hold while I transfer you to a representative.",
    },

    # ── Payment disclosures (GROUP G4 — read verbatim) ────────────────────────
    "payment_disclosure": (
        "Please allow 24 to 48 hours for your payment to post if made online, "
        "or 7 to 10 business days if mailed."
    ),

    # ── Prepaid card restriction (GROUP G5 / E6 — read verbatim) ─────────────
    "prepaid_card_restriction": (
        "I'm sorry, we're unable to process payments from prepaid cards "
        "that do not have a name on them."
    ),
}


def get_retry_prompt(slot: str, attempt_type: str, attempt_number: int = 0) -> str:
    """
    Returns the appropriate retry verbiage for a slot.

    slot:         e.g. "phone", "policy_number", "date_of_birth"
    attempt_type: "invalid" | "blank" | "no" | "ask"
    attempt_number: 0-based index (0 = first retry)
    """
    slot_prompts = PROMPTS.get(slot, {})
    prompts_for_type = slot_prompts.get(attempt_type, [])

    if isinstance(prompts_for_type, list):
        idx = min(attempt_number, len(prompts_for_type) - 1)
        return prompts_for_type[idx]
    return prompts_for_type or ""
