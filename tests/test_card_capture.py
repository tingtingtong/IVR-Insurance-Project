"""
Standalone test: card number extraction from STT variations.

Run:  python tests/test_card_capture.py

Tests the extract_card_digits() function against every known STT output
pattern for credit card numbers (Twilio, Google STT, Whisper, etc.).
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.card_extractor import extract_card_digits

# ═══════════════════════════════════════════════════════════════════════════
# TEST CASES
# ═══════════════════════════════════════════════════════════════════════════

TESTS = [
    # ── Pure digit strings ──────────────────────────────────────────────
    ("1111222233334444", "1111222233334444", "pure 16 digits"),
    ("4532 1234 5678 9012", "4532123456789012", "spaced groups of 4"),
    ("4532  1234  5678  9012", "4532123456789012", "double-spaced groups"),

    # ── Comma-separated (STT punctuation) ───────────────────────────────
    ("1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4", "1111222233334444", "comma-separated singles"),
    ("1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4", "1111222233334444", "commas no space"),
    ("1111, 2222, 3333, 4444", "1111222233334444", "comma-separated groups"),

    # ── Dot-separated (STT punctuation) ─────────────────────────────────
    ("1111. 2222. 3333. 4444.", "1111222233334444", "dot-separated groups"),
    ("1111.2222.3333.4444", "1111222233334444", "dots no space"),
    ("1. 1. 1. 1. 2. 2. 2. 2. 3. 3. 3. 3. 4. 4. 4. 4.", "1111222233334444", "dot-separated singles"),

    # ── Mixed punctuation ───────────────────────────────────────────────
    ("1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3. 3. 3. 4, 4, 4, 4", "111122223333334444", "mixed comma/dot 18 digits (STT extra)"),
    ("1111 2222. 3333 4444.", "1111222233334444", "space+dot groups"),

    # ── Word numbers ────────────────────────────────────────────────────
    ("one one one one two two two two three three three three four four four four",
     "1111222233334444", "all word numbers"),
    ("four five three two one two three four five six seven eight nine zero one two",
     "4532123456789012", "word numbers as card"),

    # ── Homophones (STT mishearings) ────────────────────────────────────
    ("for five three to won to three for five six seven ate nine oh won to",
     "4532123456789012", "all homophones"),
    ("for five tree two one too three fore five six seven eight nine oh one two",
     "4532123456789012", "mixed homophones"),

    # ── Mixed words + digits ────────────────────────────────────────────
    ("1 1 1 1 two two two two 3 3 3 3 four four four four",
     "1111222233334444", "mixed digits and words"),
    ("4532 one two three four 5678 nine zero one two",
     "4532123456789012", "alternating digits and words"),

    # ── Repeat prefixes ─────────────────────────────────────────────────
    ("double one double one double two double two double three double three double four double four",
     "1111222233334444", "double prefixes"),
    ("triple one one triple two two triple three three triple four four",
     "1111222233334444", "triple + single"),

    # ── Twilio-style spaced single digits ───────────────────────────────
    ("4 5 3 2 1 2 3 4 5 6 7 8 9 0 1 2", "4532123456789012", "spaced single digits"),

    # ── With noise words ────────────────────────────────────────────────
    ("my card number is 4532 1234 5678 9012", "4532123456789012", "with noise prefix"),
    ("it's 1111 2222 3333 4444 thanks", "1111222233334444", "with noise suffix"),
    ("the number is 4 5 3 2 1 2 3 4 5 6 7 8 9 0 1 2 please", "4532123456789012", "noise + single digits"),

    # ── Edge: extra digits (17+) -- extract all, let validator handle ────
    ("1 1 1 1 1 2 2 2 2 3 3 3 3 4 4 4 4", "11111222233334444", "17 digits (1 extra one)"),
    ("4 5 3 2 1 2 3 4 5 6 7 8 9 0 1 2 3", "45321234567890123", "17 digits (trailing 3)"),

    # ── Edge: fewer digits (15) -- extract all, let validator handle ─────
    ("1 1 1 1 2 2 2 2 3 3 3 4 4 4 4", "111122223334444", "15 digits (missing one 3)"),

    # ── Edge: empty / garbage ───────────────────────────────────────────
    ("", "", "empty string"),
    ("I don't know my card number", "", "no digits at all"),
    ("um, let me think", "", "filler words only"),
]


def run_tests():
    passed = 0
    failed = 0
    print(f"\n{'='*70}")
    print(f" Card Number Extraction Tests")
    print(f"{'='*70}\n")

    for utterance, expected, label in TESTS:
        result = extract_card_digits(utterance)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        if not ok:
            print(f"  [{status}] {label}")
            print(f"         Input:    \"{utterance}\"")
            print(f"         Expected: \"{expected}\"")
            print(f"         Got:      \"{result}\"")
            print()
        else:
            print(f"  [{status}] {label}")

    print(f"\n{'='*70}")
    print(f" Results: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'='*70}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
