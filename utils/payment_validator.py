"""
Payment field validation — Luhn check, expiry, CVV, routing number, payment ID generation.
Used by both the mock API (server-side) and payment_api.py (client-side pre-flight).
"""
import re
from datetime import date, datetime


def luhn_check(card_number: str) -> bool:
    """Validate a card number using the Luhn algorithm."""
    digits = re.sub(r"\D", "", card_number)
    if not digits or len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def validate_card_number(card_number: str) -> tuple[bool, str]:
    """Return (valid, error_message) for a card number."""
    digits = re.sub(r"\D", "", card_number)
    if len(digits) != 16:
        return False, f"Card number must be 16 digits (got {len(digits)})"
    if not luhn_check(digits):
        return False, "Card number failed validation check"
    return True, ""


def validate_expiry(expiry: str) -> tuple[bool, str]:
    """Validate MM/YY or MM/YYYY expiry and check it's in the future."""
    m = re.match(r"^(\d{1,2})[/\-](\d{2,4})$", expiry.strip())
    if not m:
        return False, "Expiry must be in MM/YY or MM/YYYY format"
    month = int(m.group(1))
    year = int(m.group(2))
    if year < 100:
        year += 2000
    if month < 1 or month > 12:
        return False, "Invalid month in expiry date"
    today = date.today()
    if year < today.year or (year == today.year and month < today.month):
        return False, "Card has expired"
    return True, ""


def validate_cvv(cvv: str) -> tuple[bool, str]:
    """CVV must be exactly 3 digits (business rule; Amex 4-digit CID is out of scope)."""
    digits = re.sub(r"\D", "", cvv)
    if len(digits) != 3:
        return False, "CVV must be 3 digits"
    return True, ""


def try_trim_extra_digits(digits: str, expected_len: int,
                          checksum_fn=None, max_extra: int = 4) -> str:
    """Silently drop STT-duplicated digits to reach expected_len.

    If a checksum is supplied, the unique surviving candidate must pass it.
    Returns the corrected string, or "" if none / more than one candidate.
    """
    extras = len(digits) - expected_len
    if extras < 1 or extras > max_extra:
        return ""

    candidates: set[str] = set()

    def _search(s: str, removals_left: int):
        if removals_left == 0:
            if len(s) == expected_len:
                if checksum_fn is None or checksum_fn(s):
                    candidates.add(s)
            return
        if len(s) <= expected_len:
            return
        if len(s) - removals_left < expected_len:
            return
        for i in range(len(s)):
            if (i > 0 and s[i] == s[i - 1]) or (i < len(s) - 1 and s[i] == s[i + 1]):
                _search(s[:i] + s[i + 1:], removals_left - 1)
                if len(candidates) > 1:
                    return

    _search(digits, extras)
    if len(candidates) == 1:
        return candidates.pop()
    return ""


def validate_routing_number(routing: str) -> tuple[bool, str]:
    """ABA routing number must be exactly 9 digits."""
    digits = re.sub(r"\D", "", routing)
    if len(digits) != 9:
        return False, f"Routing number must be 9 digits (got {len(digits)})"
    return True, ""


def validate_account_number(account: str) -> tuple[bool, str]:
    """Bank account number: 4-17 digits."""
    digits = re.sub(r"\D", "", account)
    if len(digits) < 4 or len(digits) > 17:
        return False, f"Account number must be 4-17 digits (got {len(digits)})"
    return True, ""


def generate_payment_id() -> str:
    """Generate a unique payment ID: PAY-YYYYMMDD-XXXXXX."""
    import random
    import string
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PAY-{date_part}-{random_part}"
