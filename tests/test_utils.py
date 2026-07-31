"""
Unit tests for utility functions — no API keys needed.
Run: python tests/test_utils.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tts_normalizer import normalize_tts_text
from utils.pii_validator import normalize_phone, normalize_dob, normalize_policy_number
from utils.date_utils import format_date_natural, parse_spoken_date

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
tests_run = 0
tests_failed = 0


def check(label: str, got, expected):
    global tests_run, tests_failed
    tests_run += 1
    if got == expected:
        print(f"  {PASS}  {label}")
    else:
        tests_failed += 1
        print(f"  {FAIL}  {label}")
        print(f"         expected: {repr(expected)}")
        print(f"         got:      {repr(got)}")


def test_tts_normalizer():
    print("\n--- TTS Normalizer ---")
    check("currency $1,234.56",
          normalize_tts_text("Your premium is $1,234.56."),
          "Your premium is one thousand two hundred thirty-four dollars and fifty-six cents.")

    check("currency space '$ 50'",
          normalize_tts_text("Amount: $ 50"),
          "Amount: fifty dollars")

    check("phone NXX-NXX-XXXX",
          normalize_tts_text("Call 555-867-5309"),
          "Call five five five, eight six seven, five three zero nine")

    check("date MM/DD/YYYY",
          normalize_tts_text("Born 01/22/1978"),
          "Born January 22nd, 1978")

    check("policy number",
          normalize_tts_text("Policy P300123456"),
          "Policy P 3 0 0 1 2 3 4 5 6")

    check("double period fix",
          normalize_tts_text("response.. Just a moment"),
          "response. Just a moment")

    check("trailing space before ?",
          normalize_tts_text("Is that correct ?"),
          "Is that correct?")

    check("odd hyphenation",
          normalize_tts_text("both- affiliated"),
          "both, affiliated")

    check("${variable} protected",
          normalize_tts_text("Your balance is ${amount} dollars"),
          "Your balance is ${amount} dollars")

    check("percentage",
          normalize_tts_text("Rate is 15%"),
          "Rate is fifteen percent")


def test_pii_validator():
    print("\n--- PII Validator ---")
    check("phone spoken digits",
          normalize_phone("five five five one two three four five six seven"),
          "5551234567")

    check("phone with dashes",
          normalize_phone("555-123-4567"),
          "5551234567")

    check("phone 11-digit (country code)",
          normalize_phone("15551234567"),
          "5551234567")

    check("policy number",
          normalize_policy_number("P three zero zero one two three four five six"),
          "P300123456")

    check("policy already formatted",
          normalize_policy_number("P300123456"),
          "P300123456")

    check("dob numeric",
          normalize_dob("01/22/1978"),
          "1978-01-22")

    check("dob ISO",
          normalize_dob("1978-01-22"),
          "1978-01-22")


def test_date_utils():
    print("\n--- Date Utils ---")
    check("format ISO to natural",
          format_date_natural("2024-03-15"),
          "March 15th, 2024")

    check("format 1st ordinal",
          format_date_natural("2024-01-01"),
          "January 1st, 2024")

    check("format 2nd ordinal",
          format_date_natural("2024-02-02"),
          "February 2nd, 2024")

    check("format 11th (th not st)",
          format_date_natural("2024-01-11"),
          "January 11th, 2024")

    check("parse spoken date",
          parse_spoken_date("January twenty-second nineteen seventy-eight"),
          "1978-01-22")

    check("parse numeric date",
          parse_spoken_date("01/22/1978"),
          "1978-01-22")


if __name__ == "__main__":
    test_tts_normalizer()
    test_pii_validator()
    test_date_utils()

    print(f"\n{'='*40}")
    if tests_failed == 0:
        print(f"{PASS}  All {tests_run} tests passed.")
    else:
        print(f"{FAIL}  {tests_failed}/{tests_run} tests FAILED.")
    print("=" * 40)
    sys.exit(1 if tests_failed else 0)
