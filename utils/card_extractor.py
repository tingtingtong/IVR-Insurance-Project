"""
Robust card/account number extraction from STT output.

Handles all known Twilio/Google/Whisper STT output patterns:
  - Pure digits, spaced groups, comma/dot separated
  - Word numbers, homophones, mixed formats
  - Repeat prefixes ("double four", "triple one")
  - Noise words filtered out
"""
import re

_WORD_TO_DIGIT = {
    "zero": "0", "oh": "0", "o": "0",
    "one": "1", "won": "1",
    "two": "2", "to": "2", "too": "2", "tu": "2",
    "three": "3", "tree": "3",
    "four": "4", "for": "4", "fore": "4",
    "five": "5",
    "six": "6", "sicks": "6",
    "seven": "7",
    "eight": "8", "ate": "8",
    "nine": "9", "niner": "9",
}

_REPEAT_WORDS = {"double": 2, "triple": 3}


def _expand_repeat_prefixes(text: str) -> str:
    """Expand 'double 4' -> '4 4', 'triple 3' -> '3 3 3'."""
    words = text.lower().split()
    result = []
    i = 0
    while i < len(words):
        w = words[i].strip(".,;:")
        if w in _REPEAT_WORDS and i + 1 < len(words):
            count = _REPEAT_WORDS[w]
            next_w = words[i + 1].strip(".,;:")
            digit = _WORD_TO_DIGIT.get(next_w, next_w)
            if re.match(r"^\d$", digit):
                result.extend([digit] * count)
                i += 2
                continue
        result.append(words[i])
        i += 1
    return " ".join(result)


def extract_card_digits(utterance: str) -> str:
    """Extract digits from STT utterance for card/account number capture.

    Handles all STT variations: pure digits, comma/dot/space separated,
    word numbers, homophones, repeat prefixes, noise words.
    """
    text = utterance.strip()
    if not text:
        return ""

    text = _expand_repeat_prefixes(text)
    tokens = re.split(r"\s+", text.lower())

    digits = []
    for token in tokens:
        clean = token.strip(".,;:!?")
        if not clean:
            continue

        if re.match(r"^\d+$", clean):
            digits.append(clean)
            continue

        if clean in _WORD_TO_DIGIT:
            digits.append(_WORD_TO_DIGIT[clean])
            continue

        # Tokens with embedded punctuation between digits: "1,1,1,1"
        inner_digits = re.sub(r"[^0-9]", "", clean)
        if inner_digits and len(inner_digits) / len(clean) > 0.3:
            digits.append(inner_digits)
            continue

        # Non-digit noise word — skip

    return "".join(digits)
