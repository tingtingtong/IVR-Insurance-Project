"""#79: Gather settings are chosen per turn from graph state."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import settings
from utils.gather_profiles import profile_for


class ProfileSelectionTests(unittest.TestCase):
    def test_default_when_no_state(self):
        p = profile_for({}, "What is your date of birth?")
        self.assertEqual(p.name, "default")
        self.assertEqual(p.speech_timeout, settings.gather_speech_timeout_default)

    def test_every_confirming_step_is_confirm(self):
        for key, step in [("auth_step", "confirming_dob"), ("auth_step", "confirming_name"),
                          ("otp_step", "confirming_card"), ("otp_step", "confirming_cvv"),
                          ("otp_step", "confirming_routing"), ("otp_step", "ach_auth_script")]:
            with self.subTest(step=step):
                p = profile_for({key: step})
                self.assertEqual(p.name, "confirm")
                self.assertEqual(p.speech_timeout, settings.gather_speech_timeout_confirm)

    def test_collecting_steps_keep_default_timeout(self):
        for key, step in [("auth_step", "collecting_dob"), ("auth_step", "collecting_phone"),
                          ("otp_step", "collecting_card_expiry"), ("otp_step", "collecting_card_cvv"),
                          ("otp_step", "collecting_custom_amount")]:
            with self.subTest(step=step):
                self.assertEqual(profile_for({key: step}).speech_timeout,
                                 settings.gather_speech_timeout_default)

    def test_choosing_method_is_choice(self):
        p = profile_for({"otp_step": "choosing_method"})
        self.assertEqual(p.name, "choice")
        self.assertIn("bank", p.hints)

    def test_yes_no_prompt_without_step_is_confirm(self):
        self.assertEqual(profile_for({}, "Is there anything else I can help you with?").name, "confirm")
        self.assertEqual(profile_for({}, "Please say yes or no.").name, "confirm")

    def test_completed_flow_steps_do_not_trigger_confirm(self):
        self.assertEqual(profile_for({"auth_step": "complete", "otp_step": "complete"}).name, "default")

    def test_speech_model_unset_by_default(self):
        for st in ({}, {"auth_step": "confirming_dob"}, {"otp_step": "choosing_method"}):
            self.assertIsNone(profile_for(st).speech_model)

    def test_timeouts_follow_settings(self):
        old = settings.gather_speech_timeout_confirm
        try:
            settings.gather_speech_timeout_confirm = 1
            self.assertEqual(profile_for({"auth_step": "confirming_dob"}).speech_timeout, 1)
        finally:
            settings.gather_speech_timeout_confirm = old


if __name__ == "__main__":
    unittest.main()
