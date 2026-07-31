"""
PII and payment info redactor for call transcripts.
Applied to caller (human) turns before storing in conversation_store.
Bot turns are not redacted — they never contain raw PII.
"""
import re

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

# Policy numbers: P300XXXXXX format and common variations
_POLICY = re.compile(r'\bP\d{3}\d{6,}\b', re.IGNORECASE)

# Dollar amounts: $125.50 / $5,000 / 125 dollars
_DOLLAR = re.compile(r'\$[\d,]+(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s*dollars?\b', re.IGNORECASE)

# Credit / debit card numbers (13–19 digits, may have spaces/dashes)
_CARD = re.compile(r'\b(?:\d[\s\-]?){13,19}\b')

# SSN: XXX-XX-XXXX or 9 raw digits preceded by "social" keyword
_SSN = re.compile(r'\b\d{3}[\-\s]\d{2}[\-\s]\d{4}\b')

# Bank account / routing numbers (8–17 digits)
_BANK_ACCT = re.compile(
    r'(?:account|routing|aba)[\s#:]*\d{6,17}',
    re.IGNORECASE,
)

# ── Replacement labels ─────────────────────────────────────────────────────────

_REPLACEMENTS = [
    (_SSN,       "[SSN REDACTED]"),
    (_CARD,      "[CARD REDACTED]"),
    (_BANK_ACCT, "[BANK ACCT REDACTED]"),
    (_PHONE,     "[PHONE REDACTED]"),
    (_DATE_VERBAL, "[DOB REDACTED]"),
    (_DATE_NUMERIC,"[DATE REDACTED]"),
    (_POLICY,    "[POLICY REDACTED]"),
    (_DOLLAR,    "[AMOUNT REDACTED]"),
]


def redact(text: str) -> str:
    """Return text with PII and payment info replaced by labeled placeholders."""
    for pattern, label in _REPLACEMENTS:
        text = pattern.sub(label, text)
    return text


def redact_turn(role: str, text: str) -> str:
    """Redact human turns; pass bot turns through unchanged."""
    if role == "human":
        return redact(text)
    return text
