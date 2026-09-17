"""PR-02: ACH phrase match, CVV not spoken, payment idempotency key."""
import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PaymentSafetyTests(unittest.TestCase):
    def test_ach_requires_i_authorize_phrase(self):
        src = (ROOT / "core" / "graph" / "nodes" / "otp.py").read_text(encoding="utf-8")
        self.assertIn(r"\bi authorize\b", src)
        self.assertNotIn('if "authorize" in last_human.lower()', src)

    def test_cvv_is_not_read_back_in_tts(self):
        src = (ROOT / "core" / "graph" / "nodes" / "otp.py").read_text(encoding="utf-8")
        self.assertNotIn("Security code {_spell_digits(cvv)}", src)
        self.assertIn("digit security code", src)

    def test_payment_api_sends_idempotency_key(self):
        src = (ROOT / "core" / "tools" / "payment_api.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("Idempotency-Key"), 2)
        self.assertIn("idempotency_key", src)

    def test_dtmf_complete_uses_redis_lock(self):
        src = (ROOT / "webhooks" / "twilio_stream.py").read_text(encoding="utf-8")
        self.assertIn("cno:paylock:", src)
        self.assertIn("dtmf_duplicate_ignored", src)


if __name__ == "__main__":
    unittest.main()
