"""#75: log_event must redact caller text and names before logging or storing."""
import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from structlog.testing import capture_logs

import utils.pii_redactor as pr
from utils.call_logger import log_event
from utils.pii_redactor import remember_identity


def _emit(event_type, **data):
    with patch("services.conversation_store.add_call_event") as store, capture_logs() as logs:
        log_event("CA_TEST", event_type, **data)
    return logs[0], store.call_args.args[2]


class LogEventRedactionTests(unittest.TestCase):
    def setUp(self):
        pr._known_names.clear(); pr._known_re = None

    def tearDown(self):
        pr._known_names.clear(); pr._known_re = None

    def test_caller_name_is_replaced(self):
        logged, stored = _emit("auth_complete", caller_name="John Smith", persona="insured")
        for out in (logged, stored):
            self.assertEqual(out["caller_name"], "[NAME REDACTED]")
            self.assertEqual(out["persona"], "insured")

    def test_tts_preview_masks_known_names(self):
        remember_identity({"FirstName": "Jane", "LastName": "Smith"})
        logged, stored = _emit("graph_result", tts_preview="Your current beneficiaries are: Jane Smith, 100 percent")
        for out in (logged, stored):
            self.assertNotIn("Jane", out["tts_preview"])
            self.assertNotIn("100", out["tts_preview"])

    def test_raw_phone_and_dob_inputs(self):
        logged, _ = _emit("auth_detail", step="collecting_dob", input="July 15th 1965", parsed="1965-07-15")
        self.assertNotIn("1965", json.dumps(logged))
        logged, _ = _emit("auth_detail", step="collecting_phone", input="317 555 1234", digits="317***")
        self.assertNotIn("555", logged["input"])

    def test_node_enter_input_and_from_number(self):
        logged, _ = _emit("node_enter", node="contact", input="my new address is 742 Maple Ave")
        self.assertNotIn("742", logged["input"])
        logged, _ = _emit("call_start", from_number="+19087425347", channel="webhook")
        self.assertNotIn("9087425347", logged["from_number"])
        self.assertEqual(logged["channel"], "webhook")

    def test_non_text_fields_untouched(self):
        logged, stored = _emit("graph_result", node="policy", graph_latency_ms=293, intent="policy_info")
        self.assertEqual(stored["graph_latency_ms"], 293)
        self.assertEqual(logged["node"], "policy")


if __name__ == "__main__":
    unittest.main()
