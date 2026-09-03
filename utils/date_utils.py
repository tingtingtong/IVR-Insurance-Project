"""Date parsing and formatting utilities for insuranceCompany IVR."""
import re
from datetime import datetime

_MONTH_MAP = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9,"sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11,"nov": 11,
    "december": 12,"dec": 12,
}

_ORDINAL_MAP = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "twenty-first": 21, "twenty-second": 22,
    "twenty-third": 23, "twenty-fourth": 24, "twenty-fifth": 25,
    "twenty-sixth": 26, "twenty-seventh": 27, "twenty-eighth": 28,
    "twenty-ninth": 29, "thirtieth": 30, "thirty-first": 31,
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty one": 21, "twenty two": 22, "twenty three": 23, "twenty four": 24,
    "twenty five": 25, "twenty six": 26, "twenty seven": 27, "twenty eight": 28,
    "twenty nine": 29, "thirty": 30, "thirty one": 31,
}

_DECADE_WORDS = {
    "nineteen": 1900, "two thousand": 2000,
}

_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def parse_spoken_date(utterance: str) -> str:
    """
    Parse a spoken date into YYYY-MM-DD.
    Handles:
      "January twenty-second nineteen seventy-eight"
      "01/22/1978"  "01-22-1978"  "1978-01-22"
    Returns "" on failure.
    """
    text = utterance.lower().strip()

    # Numeric formats: MM/DD/YYYY or MM-DD-YYYY
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _to_iso(year, month, day)

    # ISO: YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Spoken: "January 22nd 1978" or "January twenty-second nineteen seventy-eight"
    month = _extract_month(text)
    day   = _extract_day(text)
    year  = _extract_year(text)

    if month and day and year:
        return _to_iso(year, month, day)

    return ""


def format_date_natural(date_str: str) -> str:
    """
    Format ISO date for voice: "2024-03-15" → "March 15th, 2024"
    Returns original string if parsing fails.
    """
    if not date_str:
        return date_str
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        day = dt.day
        suffix = _ordinal_suffix(day)
        return f"{dt.strftime('%B')} {day}{suffix}, {dt.year}"
    except ValueError:
        return date_str


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_iso(year: int, month: int, day: int) -> str:
    try:
        dt = datetime(year, month, day)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _extract_month(text: str) -> int:
    for name, num in _MONTH_MAP.items():
        if name in text:
            return num
    return 0


def _extract_day(text: str) -> int:
    # Normalise spoken compound ordinals: "twenty second" → "twenty-second"
    text = re.sub(r'\b(twenty|thirty)[ ]+(\w)', r'\1-\2', text)
    # Ordinal words: longest first to avoid "second" matching inside "twenty-second"
    for word, num in sorted(_ORDINAL_MAP.items(), key=lambda x: -len(x[0])):
        if word in text:
            return num
    # Numeric ordinal: "22nd", "1st", "3rd"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", text)
    if m:
        return int(m.group(1))
    # Plain number after month name (fallback)
    m = re.search(r"\b([12]?\d|3[01])\b", text)
    if m:
        return int(m.group(1))
    return 0


def _extract_year(text: str) -> int:
    # 4-digit year
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if m:
        return int(m.group(0))
    # STT garbled: digits separated by commas/spaces/periods (e.g. "1, 9 6 5" or "1 9 6 5.")
    # Collect all digit characters and see if they form a valid year
    digits_only = re.sub(r"[^0-9]", "", text)
    # Look for 19xx or 20xx pattern in the digit string
    m = re.search(r"(19\d{2}|20\d{2})", digits_only)
    if m:
        return int(m.group(0))
    # 3-digit truncated year (e.g. "196" from "1965" cut off by STT)
    # — can't reliably guess the last digit, so skip this case
    # Spoken year: "nineteen seventy-eight" → 1978, "twenty twenty" → 2020
    for decade_word, base in _DECADE_WORDS.items():
        if decade_word in text:
            remaining = text.split(decade_word, 1)[-1].strip()
            # Try tens + ones: "seventy-eight" = 70 + 8 = 78
            for tens_word, tens_val in _TENS_WORDS.items():
                if tens_word in remaining:
                    after_tens = remaining.split(tens_word, 1)[-1].strip().lstrip("-")
                    for ones_word, ones_val in sorted(_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
                        if ones_word in after_tens and ones_val < 10:
                            return base + tens_val + ones_val
                    return base + tens_val
            # Plain 1-19: "nineteen" → 1919, "twelve" → 1912
            for word, val in sorted(_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
                if re.search(r"\b" + re.escape(word) + r"\b", remaining) and val < 20:
                    return base + val
            m = re.search(r"\b(\d{1,2})\b", remaining)
            if m:
                return base + int(m.group(1))
            # Bare decade with nothing after — "two thousand" alone = 2000
            if not remaining:
                return base
    # "twenty twenty" style (base "twenty" = 2000)
    if "twenty" in text:
        remaining = text.split("twenty", 1)[-1].strip()
        for tens_word, tens_val in _TENS_WORDS.items():
            if remaining.startswith(tens_word):
                after_tens = remaining[len(tens_word):].strip().lstrip("-")
                for ones_word, ones_val in sorted(_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
                    if ones_word in after_tens and ones_val < 10:
                        return 2000 + tens_val + ones_val
                return 2000 + tens_val
        for word, val in sorted(_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
            if re.search(r"\b" + re.escape(word) + r"\b", remaining) and val < 20:
                return 2000 + val
    return 0
