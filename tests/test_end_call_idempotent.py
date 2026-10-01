"""#73: end_call must log a call to MLflow only once."""
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.conversation_store as cs


class EndCallIdempotentTests(unittest.TestCase):
    def setUp(self):
        self.sid = "CA_END_TEST"
        cs._calls[self.sid] = {"call_sid": self.sid, "status": "active", "ended_at": None,
                               "turns": [], "events": []}

    def tearDown(self):
        cs._calls.pop(self.sid, None)

    def test_second_end_call_is_a_no_op(self):
        with patch.object(cs._db, "upsert_call") as upsert, \
             patch("services.mlflow_tracker.log_call") as log_call:
            cs.end_call(self.sid)                      # goodbye node
            first_end = cs._calls[self.sid]["ended_at"]
            cs.end_call(self.sid)                      # status callback, seconds later
        self.assertEqual(log_call.call_count, 1)
        self.assertEqual(upsert.call_count, 1)
        self.assertEqual(cs._calls[self.sid]["ended_at"], first_end)
        self.assertEqual(cs._calls[self.sid]["status"], "ended")

    def test_hangup_only_still_ends_and_logs(self):
        with patch.object(cs._db, "upsert_call"), patch("services.mlflow_tracker.log_call") as log_call:
            cs.end_call(self.sid)                      # status callback is the only end
        self.assertEqual(log_call.call_count, 1)
        self.assertEqual(cs._calls[self.sid]["status"], "ended")


if __name__ == "__main__":
    unittest.main()
