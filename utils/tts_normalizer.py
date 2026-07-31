"""
TTS text normalizer — Python port of TTSPreProcessor.groovy
Applies ElevenLabs / IVR TTS best practices before sending to TTS engine.
"""
import re

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

_ADDRESS_ABBREVS = {
    r"\bNE\b": "North East",  r"\bNW\b": "North West",
    r"\bSE\b": "South East",  r"\bSW\b": "South West",
    r"(?i)\bBlvd\.?\b": "Boulevard",  r"(?i)\bPkwy\.?\b": "Parkway",
    r"(?i)\bHwy\.?\b":  "Highway",    r"(?i)\bFwy\.?\b":  "Freeway",
    r"(?i)\bTrl\.?\b":  "Trail",      r"(?i)\bCir\.?\b":  "Circle",
    r"(?i)\bCt\.?\b":   "Court",      r"(?i)\bPl\.?\b":   "Place",
    r"(?i)\bRd\.?\b":   "Road",       r"(?i)\bLn\.?\b":   "Lane",
    r"(?i)\bDr\.?\b":   "Drive",      r"(?i)\bAve\.?\b":  "Avenue",
    r"(?i)\bSt\.(?=\s|,|$)": "Street",
    r"(?i)\bApt\.?\b":  "Apartment",  r"(?i)\bSte\.?\b":  "Suite",
    r"(?i)\bP\.?O\.?\s*Box\b": "P O Box",
}

_TITLE_ABBREVS = {
    r"(?i)\bMr\.?\b":    "Mister",       r"(?i)\bMrs\.?\b":   "Missus",
    r"(?i)\bMs\.?\b":    "Miss",          r"(?i)\bDr\.?\b":    "Doctor",
    r"(?i)\bJr\.?\b":    "Junior",        r"(?i)\bSr\.?\b":    "Senior",
    r"(?i)\betc\.?\b":   "et cetera",     r"(?i)\bvs\.?\b":    "versus",
    r"(?i)\bapprox\.?\b": "approximately",
}


def normalize_tts_text(text: str) -> str:
    if not text:
        return text

    # Step 1: Protect ${template_variables}
    saved: dict[str, str] = {}
    idx = 0

    def _protect(m: re.Match) -> str:
        nonlocal idx
        token = f"__TTSVAR{idx}__"
        saved[token] = m.group(0)
        idx += 1
        return token

    t = re.sub(r"\$\{[^}]+\}", _protect, text)

    # Step 2: Fix double periods (H4)
    t = re.sub(r"\.{2,}", ".", t)

    # Step 3: Fix trailing space before punctuation (H8)
    t = re.sub(r" +([?!])", r"\1", t)

    # Step 4: Fix odd hyphenation (H6)  "both- affiliated" → "both, affiliated"
    t = re.sub(r"(\w)- ([a-z])", r"\1, \2", t)

    # Step 5: Normalize "$ 50" → "$50" (H1)
    t = re.sub(r"\$\s+(\d)", r"$\1", t)

    # Step 6: Currency → words
    t = re.sub(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", lambda m: _currency_to_words(m.group(1)), t)

    # Step 7: Percentages
    t = re.sub(r"\b(\d+(?:\.\d+)?)%", lambda m: f"{_number_to_words(int(float(m.group(1))))} percent", t)

    # Step 8: Phone numbers NXX-NXX-XXXX
    t = re.sub(
        r"\b(\d{3})-(\d{3})-(\d{4})\b",
        lambda m: f"{_spell_digits(m.group(1))}, {_spell_digits(m.group(2))}, {_spell_digits(m.group(3))}",
        t,
    )

    # Step 9: 10-digit phone numbers (no dashes)
    t = re.sub(
        r"\b(\d{10})\b",
        lambda m: f"{_spell_digits(m.group(1)[:3])}, {_spell_digits(m.group(1)[3:6])}, {_spell_digits(m.group(1)[6:])}",
        t,
    )

    # Step 10: Policy/case numbers (letter + digits)
    t = re.sub(r"\b([A-Z]{1,3}\d{5,12})\b", lambda m: " ".join(list(m.group(1))), t)

    # Step 11: Dates MM/DD/YYYY
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
               lambda m: _format_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), t)

    # Step 12: Dates YYYY-MM-DD
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b",
               lambda m: _format_date(int(m.group(2)), int(m.group(3)), int(m.group(1))), t)

    # Step 13: Address abbreviations
    for pattern, replacement in _ADDRESS_ABBREVS.items():
        t = re.sub(pattern, replacement, t)

    # Step 14: Title abbreviations
    for pattern, replacement in _TITLE_ABBREVS.items():
        t = re.sub(pattern, replacement, t)

    # Step 15: Fix missing space after sentence-ending punctuation
    t = re.sub(r"([.!?])([A-Z])", r"\1 \2", t)

    # Step 16: Normalize whitespace
    t = re.sub(r" {2,}", " ", t).strip()

    # Step 17: Restore ${template_variables}
    for token, original in saved.items():
        t = t.replace(token, original)

    return t


# ── Helpers ───────────────────────────────────────────────────────────────────

def _spell_digits(s: str) -> str:
    return " ".join(_DIGIT_WORDS.get(c, c) for c in s)


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _format_date(month: int, day: int, year: int) -> str:
    if not (1 <= month <= 12):
        return f"{month}/{day}/{year}"
    return f"{_MONTHS[month - 1]} {_ordinal(day)}, {year}"


def _number_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    def _convert(x: int) -> str:
        if x < 20:
            return ones[x]
        if x < 100:
            return tens[x // 10] + ("-" + ones[x % 10] if x % 10 else "")
        if x < 1000:
            return ones[x // 100] + " hundred" + (" " + _convert(x % 100) if x % 100 else "")
        if x < 1_000_000:
            return _convert(x // 1000) + " thousand" + (" " + _convert(x % 1000) if x % 1000 else "")
        return _convert(x // 1_000_000) + " million" + (" " + _convert(x % 1_000_000) if x % 1_000_000 else "")

    return _convert(n)


def _currency_to_words(amount_str: str) -> str:
    cleaned = amount_str.replace(",", "")
    parts = cleaned.split(".")
    dollars = int(parts[0])
    cents = int(parts[1].ljust(2, "0")[:2]) if len(parts) > 1 else 0
    result = f"{_number_to_words(dollars)} dollar{'s' if dollars != 1 else ''}"
    if cents:
        result += f" and {_number_to_words(cents)} cent{'s' if cents != 1 else ''}"
    return result
