"""#79: TwiML carries the per-turn speechTimeout/hints."""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_voice as tv
from config import settings
from utils.gather_profiles import profile_for


def _attr(twiml: str, name: str) -> str:
    return re.search(rf'{name}="([^"]*)"', twiml).group(1)


class GatherTwimlTests(unittest.TestCase):
    def test_no_profile_keeps_previous_behaviour(self):
        xml = tv._gather_response("Hello")
        self.assertEqual(_attr(xml, "speechTimeout"), "3")
        self.assertIn("policy", _attr(xml, "hints"))
        self.assertNotIn("speechModel", xml)

    def test_short_turn_uses_short_timeout(self):
        xml = tv._gather_response("Anything else I can help with?",
                                  profile=profile_for({}, "Anything else I can help with?"))
        self.assertEqual(_attr(xml, "speechTimeout"), str(settings.gather_speech_timeout_confirm))
        self.assertLess(int(_attr(xml, "speechTimeout")), 3)
        self.assertIn("january", _attr(xml, "hints"))

    def test_dob_confirmation_allows_spoken_correction(self):
        xml = tv._gather_response("I heard June 15 1965. Is that correct?",
                                  profile=profile_for({"auth_step": "confirming_dob"}))
        self.assertEqual(_attr(xml, "speechTimeout"), "3")
        self.assertIn("june", _attr(xml, "hints"))

    def test_collecting_turn_keeps_three_seconds(self):
        xml = tv._gather_response("What is your date of birth?",
                                  profile=profile_for({"auth_step": "collecting_dob"}))
        self.assertEqual(_attr(xml, "speechTimeout"), "3")
        self.assertIn("january", _attr(xml, "hints"))

    def test_dtmf_gather_unchanged(self):
        self.assertEqual(_attr(tv._gather_dtmf_or_speech("Enter your card number"), "speechTimeout"), "3")


if __name__ == "__main__":
    unittest.main()
