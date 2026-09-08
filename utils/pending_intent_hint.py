"""
Append a natural-language hint about pending intents to TTS output.
Called after graph invocation in chat.py and twilio_voice.py so callers
know there's a queued intent and can confirm to proceed.
"""

_INTENT_FRIENDLY = {
    "policy_info":    "your policy information",
    "payment":        "your payment history",
    "otp":            "making a payment",
    "loan":           "your loan details",
    "beneficiary":    "your beneficiary information",
    "contact_change": "updating your contact information",
    "document":       "requesting a document",
    "privacy":        "your privacy question",
    "faq":            "your question",
    "escalate":       "connecting you to an agent",
}


def append_pending_hint(tts_text: str, result: dict) -> str:
    """If the service node just finished (active_flow empty) and there are
    pending intents, replace the generic 'anything else?' with a specific
    prompt about the next queued intent."""
    active_flow = result.get("active_flow", "")
    pending = result.get("pending_intents", [])

    if active_flow or not pending:
        return tts_text

    next_intent = pending[0]
    friendly = _INTENT_FRIENDLY.get(next_intent, next_intent.replace("_", " "))
    hint = f" You also asked about {friendly}. Would you like me to look into that?"

    # Remove trailing "Is there anything else..." if the LLM added it
    import re
    tts_text = re.sub(
        r"\s*(Is there anything else I can help you with( today)?\??)\s*$",
        "",
        tts_text,
        flags=re.IGNORECASE,
    ).rstrip()

    return tts_text + hint
