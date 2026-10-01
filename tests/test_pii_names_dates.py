"""#67: names, spoken dates and raw voice-webhook logs must not leak PII."""
import asyncio
import json
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage
from structlog.testing import capture_logs

import utils.pii_redactor as pr
from utils.pii_redactor import redact, redact_for_log, redact_known_names, remember_identity
from utils.trace_masking import mask_pii


def _reset_names():
    pr._known_names.clear()
    pr._known_re = None


class DatePatternTests(unittest.TestCase):
    def test_day_first_dob(self):
        for text in ("15. July 1965.", "15 July 1965", "the 15th of July, 1965"):
            self.assertNotIn("1965", redact(text), text)

    def test_month_year_expiry(self):
        self.assertEqual(redact("Expiry date July 2029. Is that correct?"),
                         "Expiry date [DATE REDACTED]. Is that correct?")

    def test_month_words_without_year_survive(self):
        for text in ("I may want to pay", "in March I paid", "June is fine"):
            self.assertEqual(redact(text), text)


class KnownNameTests(unittest.TestCase):
    def setUp(self):
        _reset_names()

    def tearDown(self):
        _reset_names()

    def test_names_from_identity_fields_are_masked(self):
        remember_identity({"finalized_party": {"FirstName": "John", "LastName": "Smith"}})
        out = redact_known_names("Uh, it's John Smith only on the card.")
        self.assertNotIn("John", out)
        self.assertNotIn("Smith", out)

    def test_whole_words_only(self):
        remember_identity({"FirstName": "John"})
        self.assertEqual(redact_known_names("Johnson and Johnny"), "Johnson and Johnny")

    def test_non_name_fields_and_stopwords_are_ignored(self):
        remember_identity({"Personas": [{"name": "Smith Corp"}], "caller_name": "it's only the card"})
        self.assertEqual(redact_known_names("only the card, Corp"), "only the card, Corp")

    def test_registry_is_bounded(self):
        for i in range(pr._KNOWN_MAX + 50):
            remember_identity({"FirstName": f"Name{chr(97 + i % 26)}{i}"})
        self.assertLessEqual(len(pr._known_names), pr._KNOWN_MAX)

    def test_redact_for_log_masks_every_digit(self):
        remember_identity({"FirstName": "John"})
        self.assertEqual(redact_for_log("code 5 4 3, I'm John"), "code # # #, I'm [NAME REDACTED]")


class TraceMaskingTests(unittest.TestCase):
    """Shape of the traces flagged 17/17 on the 2026-10-01 live call."""

    def setUp(self):
        _reset_names()

    def tearDown(self):
        _reset_names()

    def test_flagged_trace_shape_is_clean(self):
        state = {
            "finalized_party": {"FirstName": "John", "LastName": "Smith", "DOB": "1965-07-15"},
            "messages": [
                HumanMessage(content="John Smith."),
                HumanMessage(content="15. July 1965."),
                HumanMessage(content="Uh, it's John Smith only on the card."),
                AIMessage(content="Thank you, John Smith. Your policy is active."),
            ],
            "tts_text": "Expiry date July 2029. Is that correct?",
        }
        out = json.dumps(mask_pii(state), default=lambda o: o.model_dump())
        for leaked in ("John", "Smith", "1965", "July 2029"):
            self.assertNotIn(leaked, out)

    def test_names_learned_in_one_trace_are_masked_in_later_ones(self):
        mask_pii({"customer": {"firstName": "Mary", "lastName": "Johnson"}})
        later = mask_pii({"messages": [HumanMessage(content="This is Mary Johnson")]})
        self.assertNotIn("Mary", later["messages"][0].content)


class VoiceWebhookLogTests(unittest.TestCase):
    def setUp(self):
        _reset_names()

    def tearDown(self):
        _reset_names()

    def _post_gather(self, form: dict):
        import webhooks.twilio_voice as tv

        request = MagicMock()
        request.form = AsyncMock(return_value=form)
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"tts_text": "Is that correct?", "current_node": "otp",
                                                "customer": {"firstName": "John", "lastName": "Smith"}})
        session = MagicMock()
        session.get_state = AsyncMock(return_value={})
        with patch.object(tv._graph_module, "cno_graph", graph), \
             patch.object(tv, "SessionService", return_value=session), \
             patch.object(tv, "add_call_turn"), patch.object(tv, "update_call_metadata"), \
             patch.object(tv, "log_event") as log_event, \
             capture_logs() as logs:
            asyncio.run(tv.gather_speech(request))
        return logs, log_event

    def test_cvv_and_phone_never_logged(self):
        form = {"CallSid": "CA_TEST", "SpeechResult": "5 4 3", "Confidence": "0.9",
                "From": "+19087425347", "Caller": "+19087425347", "Digits": "", "CallStatus": "in-progress"}
        logs, log_event = self._post_gather(form)
        dumped = json.dumps(logs, default=str) + json.dumps([c.kwargs for c in log_event.call_args_list], default=str)
        self.assertNotIn("5 4 3", dumped)
        self.assertNotIn("9087425347", dumped)
        raw = next(l for l in logs if l["event"] == "gather_raw")["form_params"]
        self.assertNotIn("SpeechResult", raw)
        self.assertEqual(raw["speech_len"], 5)

    def test_names_from_graph_result_masked_on_next_turn(self):
        self._post_gather({"CallSid": "CA_TEST", "SpeechResult": "hello", "Confidence": "0.9"})
        logs, _ = self._post_gather({"CallSid": "CA_TEST", "SpeechResult": "it's John Smith", "Confidence": "0.9"})
        speech = next(l for l in logs if l["event"] == "speech_received")["transcript"]
        self.assertNotIn("John", speech)


if __name__ == "__main__":
    unittest.main()
