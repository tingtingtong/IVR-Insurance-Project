"""
PII normalization utilities — ported from insuranceCompany Groovy auth scripts.
Converts spoken/transcribed PII into the format the backend API expects.
"""
import re
from utils.date_utils import parse_spoken_date

# Digit words → digit mapping (STT often returns these)
_SPOKEN_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "oh": "0",  # "oh" is commonly used for zero on phone
}


def normalize_phone(utterance: str) -> str:
    """
    Convert spoken phone number to 10-digit string.
    Handles: "five five five eight six seven five three zero nine"
             "555-867-5309"  "5558675309"
    Returns "" if normalization fails.
    """
    digits, _ = normalize_phone_with_hint(utterance)
    return digits


def normalize_phone_with_hint(utterance: str) -> tuple[str, str]:
    """
    Convert spoken phone number to 10-digit string with a length-specific hint.
    Returns (digits, hint_message):
      - digits = 10-digit string on success, "" on failure
      - hint_message = "" on success, or a length-aware re-prompt on failure
    """
    text = _spoken_digits_to_numbers(utterance)
    digits = re.sub(r"\D", "", text)
    raw_count = len(digits)

    # Strip leading country code
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]

    if len(digits) == 10:
        return (digits, "")

    if raw_count == 9:
        hint = (
            "I only caught 9 digits. "
            "Please say all 10 digits including your area code."
        )
    elif raw_count == 11:
        # Note: 11 digits starting with "1" are stripped to 10 above; this branch
        # only fires for 11-digit numbers NOT starting with "1".
        hint = (
            "I caught 11 digits, but I need exactly 10. "
            "Please say your area code followed by your 7-digit number."
        )
    elif raw_count > 0:
        hint = (
            f"I caught {raw_count} digits, but I need exactly 10. "
            "Please say your area code followed by your 7-digit number."
        )
    else:
        hint = ""

    return ("", hint)


def normalize_policy_number(utterance: str) -> str:
    """
    Normalize spoken policy number to alphanumeric format.
    E.g. "P three zero zero one two three four five six" → "P300123456"
    """
    text = _spoken_digits_to_numbers(utterance)

    # Remove spaces between characters
    cleaned = re.sub(r"\s+", "", text).upper()

    # Match pattern: 1-3 letters followed by 5-12 digits
    # No fallback — generic alphanumeric strings match common English phrases
    # (e.g. "IALREADYG" from "I already gave you") and create a false-positive bypass risk.
    match = re.search(r"[A-Z]{1,3}\d{5,12}", cleaned)
    return match.group(0) if match else ""


def normalize_dob(utterance: str) -> str:
    """
    Convert spoken date of birth to YYYY-MM-DD.
    Handles: "January twenty-second nineteen seventy-eight"
             "01/22/1978"  "01-22-1978"
    Returns "" if parsing fails.
    """
    return parse_spoken_date(utterance)


def normalize_zipcode(utterance: str) -> str:
    """Extract 5-digit zip from spoken input."""
    text = _spoken_digits_to_numbers(utterance)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 5:
        return digits[:5]
    return ""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _spoken_digits_to_numbers(text: str) -> str:
    """Replace spoken digit words with numerals."""
    words = text.lower().split()
    result = []
    for word in words:
        # Strip punctuation
        clean = re.sub(r"[^a-z0-9]", "", word)
        result.append(_SPOKEN_DIGITS.get(clean, word))
    return " ".join(result)
