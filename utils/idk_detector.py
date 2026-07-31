"""
Shared utility: detect "I don't know / not sure / can't remember" utterances.

Used across auth, contact, document, and otp nodes so the phrase list is
maintained in one place. Import _is_idk wherever you need it.
"""

_IDK_PHRASES = (
    "i don't know", "i dont know", "idk", "not sure", "i'm not sure",
    "i am not sure", "i have no idea", "no idea", "can't remember",
    "cannot remember", "don't remember", "dont remember", "i forget",
    "i forgot", "not available", "don't have it", "dont have it",
    "i don't have", "i dont have", "unknown", "no clue",
)


def is_idk(text: str) -> bool:
    """Return True if the caller's utterance signals they don't know the answer."""
    lower = text.lower()
    return any(p in lower for p in _IDK_PHRASES)
