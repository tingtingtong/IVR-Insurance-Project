"""#98: the Gather and stream paths must greet callers with the same text."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_voice as tv
from core.prompts.retry_prompts import PROMPTS


class GreetingParityTests(unittest.TestCase):
    def test_gather_greeting_is_the_shared_prompt(self):
        self.assertEqual(tv._GREETING, PROMPTS["greeting"]["welcome"])

    def test_greeting_introduces_the_assistant(self):
        self.assertIn("virtual assistant", PROMPTS["greeting"]["welcome"])
        self.assertTrue(PROMPTS["greeting"]["welcome"].endswith("How can I help you today?"))


if __name__ == "__main__":
    unittest.main()
