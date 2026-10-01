"""#65: webhook sync must point Twilio status callbacks at the current deployment."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.twilio_webhook_sync as sync

BASE = "https://example.ngrok-free.dev"
STALE_ALB = "http://dev-ivr-alb-93307700.us-east-1.elb.amazonaws.com/webhook/status"


def _settings():
    return SimpleNamespace(
        sync_twilio_webhooks=True, twilio_base_url=BASE,
        twilio_account_sid="AC_TEST", twilio_auth_token="tok",
        twilio_twiml_app_sid="AP_TEST", twilio_phone_number="+15550001111",
    )


def _client(app_voice, app_status, num_voice, num_status):
    client = MagicMock()
    client.applications.return_value.fetch.return_value = SimpleNamespace(
        voice_url=app_voice, status_callback=app_status)
    number = MagicMock(voice_url=num_voice, status_callback=num_status)
    client.incoming_phone_numbers.list.return_value = [number]
    return client, number


def _run(client):
    with patch("twilio.rest.Client", return_value=client):
        sync.sync_webhooks(_settings())


class StatusCallbackSyncTests(unittest.TestCase):
    def test_stale_status_callbacks_are_updated(self):
        client, number = _client(f"{BASE}/webhook/voice", STALE_ALB,
                                 f"{BASE}/webhook/voice", "https://old.run.app/twilio/status")
        _run(client)
        app_kwargs = client.applications.return_value.update.call_args.kwargs
        self.assertEqual(app_kwargs["status_callback"], f"{BASE}/webhook/status")
        self.assertEqual(app_kwargs["status_callback_method"], "POST")
        self.assertEqual(number.update.call_args.kwargs["status_callback"], f"{BASE}/webhook/status")

    def test_voice_url_change_also_sets_status_callback(self):
        client, number = _client("https://old/webhook/voice", None, "https://old/webhook/voice", None)
        _run(client)
        self.assertEqual(client.applications.return_value.update.call_args.kwargs["voice_url"], f"{BASE}/webhook/voice")
        self.assertEqual(number.update.call_args.kwargs["status_callback"], f"{BASE}/webhook/status")

    def test_no_update_when_everything_matches(self):
        client, number = _client(f"{BASE}/webhook/voice", f"{BASE}/webhook/status",
                                 f"{BASE}/webhook/voice", f"{BASE}/webhook/status")
        _run(client)
        client.applications.return_value.update.assert_not_called()
        number.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
