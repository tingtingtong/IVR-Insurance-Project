"""#69: dashboard recordings are served through the app with Twilio credentials."""
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webhooks.dashboard as dash
from webhooks.security import require_dashboard_auth

REC_SID = "RE73ccca7bd636167706369d8c41d70b91"


def _client():
    app = FastAPI()
    app.include_router(dash.router)
    app.dependency_overrides[require_dashboard_auth] = lambda: None
    return TestClient(app)


def _twilio_response(status=200, content=b"\xff\xf3mp3-bytes"):
    resp = MagicMock(status_code=status, content=content)
    http = MagicMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.get = AsyncMock(return_value=resp)
    return http


class DashboardRecordingTests(unittest.TestCase):
    def test_streams_mp3_with_server_credentials(self):
        http = _twilio_response()
        with patch.object(dash, "get_call", return_value={"recording_sid": REC_SID}), \
             patch.object(dash.httpx, "AsyncClient", return_value=http):
            r = _client().get("/dashboard/calls/CA_TEST/recording")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "audio/mpeg")
        self.assertEqual(r.content, b"\xff\xf3mp3-bytes")
        url = http.get.call_args.args[0]
        self.assertTrue(url.startswith("https://api.twilio.com/2010-04-01/Accounts/"))
        self.assertTrue(url.endswith(f"/Recordings/{REC_SID}.mp3"))
        self.assertEqual(http.get.call_args.kwargs["auth"],
                         (dash.settings.twilio_account_sid, dash.settings.twilio_auth_token))

    def test_404_without_recording(self):
        with patch.object(dash, "get_call", return_value={}):
            self.assertEqual(_client().get("/dashboard/calls/CA_NONE/recording").status_code, 404)

    def test_rejects_malformed_recording_sid(self):
        http = _twilio_response()
        with patch.object(dash, "get_call", return_value={"recording_sid": "../../Calls/x"}), \
             patch.object(dash.httpx, "AsyncClient", return_value=http):
            self.assertEqual(_client().get("/dashboard/calls/CA_BAD/recording").status_code, 404)
        http.get.assert_not_called()

    def test_twilio_error_is_502(self):
        with patch.object(dash, "get_call", return_value={"recording_sid": REC_SID}), \
             patch.object(dash.httpx, "AsyncClient", return_value=_twilio_response(status=404)):
            self.assertEqual(_client().get("/dashboard/calls/CA_TEST/recording").status_code, 502)

    def test_dashboard_links_to_app_endpoint_not_twilio(self):
        src = (ROOT / "webhooks" / "dashboard.py").read_text(encoding="utf-8")
        self.assertNotIn("href=\"'+call.recording_url+'\"", src)
        self.assertIn("'/recording'", src)

    def test_endpoint_requires_dashboard_auth_when_password_set(self):
        import webhooks.security as security
        app = FastAPI()
        app.include_router(dash.router)
        with patch.object(security.settings, "dashboard_password", "secret"):
            r = TestClient(app).get("/dashboard/calls/CA_TEST/recording")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
