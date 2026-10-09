"""#100: a finished call must actually be logged to MLflow (the dependency must be installed)."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.mlflow_tracker as tracker


class MlflowTrackerTests(unittest.TestCase):
    def test_log_call_writes_a_run_to_a_fresh_sqlite_store(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # sqlite file may stay open on Windows
            db = os.path.join(tmp, "mlflow_test.db")
            call = {
                "call_sid": "CA_MLFLOW_TEST_000000001",
                "from_number": "x",
                "started_at": "2026-10-09T17:00:00",
                "ended_at": "2026-10-09T17:01:00",
                "status": "ended",
                "turns": [{"role": "bot", "text": "hi", "node": "greeting"}],
                "events": [],
            }
            with patch.object(tracker, "_DB_PATH", db), patch.object(tracker, "_mlflow", None), \
                 patch.object(tracker, "log") as log:
                tracker.log_call(call)
            warnings = [c for c in log.warning.call_args_list if c.args and c.args[0] == "mlflow_log_failed"]
            self.assertEqual(warnings, [], f"mlflow logging failed: {warnings}")
            self.assertTrue(os.path.exists(db))


if __name__ == "__main__":
    unittest.main()
