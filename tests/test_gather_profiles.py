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

    def test_pure_yes_no_steps_are_short(self):
        p = profile_for({"otp_step": "ach_auth_script"})
        self.assertEqual(p.name, "confirm")
        self.assertEqual(p.speech_timeout, settings.gather_speech_timeout_confirm)

    def test_correctable_confirmations_keep_default_timeout_and_full_hints(self):
        """'no, my DOB is 15 June 1965' must not be cut off or lose month/number hints."""
        for key, step in [("auth_step", "confirming_dob"), ("auth_step", "confirming_name"),
                          ("auth_step", "confirming_phone"), ("otp_step", "confirming_card"),
                          ("otp_step", "confirming_expiry"), ("otp_step", "confirming_cvv"),
                          ("otp_step", "confirming_routing"), ("otp_step", "confirming_amount"),
                          ("otp_step", "confirming_due_amount")]:
            with self.subTest(step=step):
                p = profile_for({key: step}, "I heard June 15 1965. Is that correct?")
                self.assertEqual(p.speech_timeout, settings.gather_speech_timeout_default)
                self.assertIn("june", p.hints)

    def test_confirm_hints_are_superset_of_default(self):
        from utils.gather_profiles import confirm_profile, default_profile
        for word in default_profile().hints.split(", "):
            self.assertIn(word, confirm_profile().hints)

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

    def test_anything_else_prompt_is_short_but_is_that_correct_is_not(self):
        self.assertEqual(profile_for({}, "Is there anything else I can help you with?").name, "confirm")
        self.assertEqual(profile_for({}, "I heard John Smith. Is that correct?").name, "default")

    def test_completed_flow_steps_do_not_trigger_confirm(self):
        self.assertEqual(profile_for({"auth_step": "complete", "otp_step": "complete"}).name, "default")

    def test_speech_model_unset_by_default(self):
        for st in ({}, {"otp_step": "ach_auth_script"}, {"otp_step": "choosing_method"}):
            self.assertIsNone(profile_for(st).speech_model)

    def test_timeouts_follow_settings(self):
        old = settings.gather_speech_timeout_confirm
        try:
            settings.gather_speech_timeout_confirm = 1
            self.assertEqual(profile_for({"otp_step": "ach_auth_script"}).speech_timeout, 1)
        finally:
            settings.gather_speech_timeout_confirm = old


if __name__ == "__main__":
    unittest.main()
