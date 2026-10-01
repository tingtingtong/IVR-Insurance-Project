"""#63: call recording must start after the call is answered, with an absolute callback URL."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webhooks.twilio_voice as tv

NOT_ELIGIBLE = Exception("HTTP 400 error: Unable to create record: Requested resource is not eligible for recording")


def _twilio_with(create_side_effect):
    create = MagicMock(side_effect=create_side_effect)
    twilio = MagicMock()
    twilio.calls.return_value.recordings.create = create
    return twilio, create


class RecordingStartTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(tv, "_public_base_url", "https://example.ngrok-free.dev"),
            patch.object(tv, "_RECORDING_RETRY_S", 0),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_retries_until_call_is_answered(self):
        twilio, create = _twilio_with([NOT_ELIGIBLE, NOT_ELIGIBLE, None])
        with patch.object(tv, "_twilio", twilio):
            asyncio.run(tv._start_recording("CA_TEST"))
        self.assertEqual(create.call_count, 3)

    def test_callback_url_is_absolute(self):
        twilio, create = _twilio_with([None])
        with patch.object(tv, "_twilio", twilio):
            asyncio.run(tv._start_recording("CA_TEST"))
        url = create.call_args.kwargs["recording_status_callback"]
        self.assertEqual(url, "https://example.ngrok-free.dev/webhook/recording-status")

    def test_other_errors_are_not_retried(self):
        twilio, create = _twilio_with([Exception("HTTP 401 error: Authenticate")])
        with patch.object(tv, "_twilio", twilio):
            asyncio.run(tv._start_recording("CA_TEST"))
        self.assertEqual(create.call_count, 1)

    def test_gives_up_after_max_attempts(self):
        twilio, create = _twilio_with([NOT_ELIGIBLE] * 10)
        with patch.object(tv, "_twilio", twilio):
            asyncio.run(tv._start_recording("CA_TEST"))
        self.assertEqual(create.call_count, tv._RECORDING_ATTEMPTS)

    def test_no_callback_when_public_url_unknown(self):
        twilio, create = _twilio_with([None])
        with patch.object(tv, "_twilio", twilio), patch.object(tv, "_public_base_url", ""):
            asyncio.run(tv._start_recording("CA_TEST"))
        self.assertNotIn("recording_status_callback", create.call_args.kwargs)

    def test_incoming_call_does_not_record_inline(self):
        src = (ROOT / "webhooks" / "twilio_voice.py").read_text(encoding="utf-8")
        handler = src[src.index("async def incoming_call"):src.index("@router.post", src.index("async def incoming_call"))]
        self.assertNotIn("recordings.create", handler)
        self.assertIn("background_tasks.add_task(_start_recording, call_sid)", handler)


if __name__ == "__main__":
    unittest.main()
