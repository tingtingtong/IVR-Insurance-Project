"""
PII and payment info redactor for call transcripts.

Applied to both human and bot turns. Card number and CVV are read back for
confirmation but must never land in the dashboard transcript or event log.
"""
import re
from collections import OrderedDict

# ── Patterns ──────────────────────────────────────────────────────────────────

# 10-digit phone numbers in various formats: 5551234567 / 555-123-4567 / (555) 123-4567
_PHONE = re.compile(r'\b(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})\b')

# Dates of birth: MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD, "January 1 1960", "Jan 1st 1960"
_DATE_NUMERIC  = re.compile(r'\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b')
_DATE_VERBAL   = re.compile(
    r'\b(january|february|march|april|may|june|july|august|september|october|november|december'
    r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)'
    r'[\s,]+\d{1,2}(?:st|nd|rd|th)?[\s,]+\d{4}\b',
    re.IGNORECASE,
)

_MONTHS = (r'(?:january|february|march|april|may|june|july|august|september|october|november|december'
           r'|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)')

# Day-first spoken dates: "15 July 1965", "15. July 1965", "15th of July, 1965"
_DATE_DAY_FIRST = re.compile(
    rf'\b\d{{1,2}}(?:st|nd|rd|th)?\.?\s+(?:of\s+)?{_MONTHS}\.?[\s,]+\d{{4}}\b',
    re.IGNORECASE,
)

# Month + year (card expiry): "July 2029", "Jul, 2029"
_MONTH_YEAR = re.compile(rf'\b{_MONTHS}\.?[\s,]+(?:of\s+)?\d{{4}}\b', re.IGNORECASE)

# Policy numbers: P300XXXXXX format and common variations
_POLICY = re.compile(r'\bP\d{3}\d{6,}\b', re.IGNORECASE)

# Dollar amounts: $125.50 / $5,000 / 125 dollars
_DOLLAR = re.compile(r'\$[\d,]+(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s*dollars?\b', re.IGNORECASE)

# Credit / debit card numbers (13–19 digits, may have spaces/dashes)
_CARD = re.compile(r'\b(?:\d[\s\-]?){13,19}\b')

# Spoken-back PAN: "4, 1, 1, 1. 1, 1, 1, 1. ..."
_SPELLED_CARD = re.compile(r'(?:\d\s*[,.]?\s*){13,19}')

# Spoken-back CVV: "security code I heard is 5, 4, 3"
_SPELLED_CVV = re.compile(
    r'(?:security code(?: I heard is)?|CVV)[^\d]{0,24}(?:\d\s*[,.]?\s*){3,4}',
    re.IGNORECASE,
)

# Isolated 3-digit code (CVV spoken as "5 4 3" / "543")
_THREE_DIGIT = re.compile(r'^\s*\d(?:[\s,.\-]*\d){2}\s*\.?\s*$')

# SSN: XXX-XX-XXXX or 9 raw digits preceded by "social" keyword
_SSN = re.compile(r'\b\d{3}[\-\s]\d{2}[\-\s]\d{4}\b')

# Bank account / routing numbers (8–17 digits)
_BANK_ACCT = re.compile(
    r'(?:account|routing|aba)[\s#:]*\d{6,17}',
    re.IGNORECASE,
)

# ── Replacement labels ─────────────────────────────────────────────────────────

_REPLACEMENTS = [
    (_SSN,         "[SSN REDACTED]"),
    (_SPELLED_CVV, "[CVV REDACTED]"),
    (_SPELLED_CARD,"[CARD REDACTED]"),
    (_CARD,        "[CARD REDACTED]"),
    (_BANK_ACCT,   "[BANK ACCT REDACTED]"),
    (_PHONE,       "[PHONE REDACTED]"),
    (_DATE_VERBAL, "[DOB REDACTED]"),
    (_DATE_DAY_FIRST, "[DOB REDACTED]"),
    (_MONTH_YEAR,  "[DATE REDACTED]"),
    (_DATE_NUMERIC,"[DATE REDACTED]"),
    (_POLICY,      "[POLICY REDACTED]"),
    (_DOLLAR,      "[AMOUNT REDACTED]"),
]


def redact(text: str, keep_amounts: bool = False) -> str:
    """Return text with PII and payment info replaced by labeled placeholders.

    keep_amounts=True leaves dollar amounts intact — they identify no one and
    LLM prompts need them to answer questions like "can I borrow $5,000?".
    """
    if not text:
        return text
    for pattern, label in _REPLACEMENTS:
        if keep_amounts and pattern is _DOLLAR:
            continue
        text = pattern.sub(label, text)
    return text


def redact_for_llm(text: str) -> str:
    """Redact caller text before it is sent to an external LLM or embedding API."""
    return redact(text, keep_amounts=True)


def redact_messages(messages: list) -> list:
    """Copies of LangChain messages with string content redacted for external LLM calls."""
    out = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str) and hasattr(msg, "model_copy"):
            msg = msg.model_copy(update={"content": redact_for_llm(content)})
        out.append(msg)
    return out


def looks_like_isolated_cvv(text: str) -> bool:
    return bool(text and _THREE_DIGIT.match(text))


def redact_turn(role: str, text: str, node: str = "") -> str:
    """Redact PAN/CVV in both human and bot turns before persistence."""
    if not text:
        return text
    if node == "otp" and looks_like_isolated_cvv(text):
        return "[CVV REDACTED]"
    return redact(text)


# ── Known caller names (#67) ───────────────────────────────────────────────────
# Names can't be matched by pattern, so names seen in identity fields of the call
# state (party records, caller name, cardholder name) are remembered and masked
# wherever they appear later — in caller utterances and bot replies.

_NAME_KEYS = frozenset(k.lower() for k in (
    "FirstName", "LastName", "first_name", "last_name",
    "caller_name", "cardholder_name", "authenticated_name",
))
_NAME_STOPWORDS = frozenset((
    "the", "and", "its", "it's", "this", "that", "only", "card", "name", "same",
    "yes", "yeah", "not", "mister", "miss", "mrs", "sir", "madam", "corp", "inc", "llc",
))
_KNOWN_MAX = 500
_known_names: "OrderedDict[str, None]" = OrderedDict()
_known_re: "re.Pattern | None" = None


def remember_identity(state) -> None:
    """Record name tokens found under identity keys anywhere in state (dicts/lists)."""
    global _known_re
    added = False
    stack = [state]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in _NAME_KEYS and isinstance(v, str):
                    for tok in re.findall(r"[A-Za-z][A-Za-z'\-]+", v):
                        key = tok.lower()
                        if len(key) < 3 or key in _NAME_STOPWORDS:
                            continue
                        if key not in _known_names:
                            added = True
                        _known_names[key] = None
                        _known_names.move_to_end(key)
                elif isinstance(v, (dict, list, tuple)):
                    stack.append(v)
        elif isinstance(obj, (list, tuple)):
            stack.extend(obj)
    while len(_known_names) > _KNOWN_MAX:
        _known_names.popitem(last=False)
    if added:
        alts = sorted(_known_names, key=len, reverse=True)
        _known_re = re.compile(r"\b(?:" + "|".join(map(re.escape, alts)) + r")\b", re.IGNORECASE)


def redact_known_names(text: str) -> str:
    """Mask caller names previously seen via remember_identity()."""
    if not text or _known_re is None:
        return text
    return _known_re.sub("[NAME REDACTED]", text)


def redact_for_log(text: str) -> str:
    """Strictest redaction for application logs: patterns, known names, then any digit."""
    if not text:
        return text
    return re.sub(r"\d", "#", redact_known_names(redact(text)))
