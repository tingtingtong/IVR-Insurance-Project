"""PR-01: ENVIRONMENT=prod must refuse to boot with open security controls."""
import os
import unittest
from unittest.mock import patch

# Dummy keys so Settings can be constructed in tests without a live .env
_BASE = {
    "openai_api_key": "sk-test",
    "deepgram_api_key": "dg-test",
    "elevenlabs_api_key": "el-test",
    "twilio_account_sid": "ACtest",
    "twilio_auth_token": "token",
}


def _settings(**overrides):
    from config.settings import Settings
    kwargs = {**_BASE, **overrides}
    return Settings(_env_file=None, **kwargs)


class ProdSecurityTests(unittest.TestCase):
    def test_dev_allows_open_defaults(self):
        s = _settings(environment="dev")
        self.assertFalse(s.is_prod)
        self.assertEqual(s.dashboard_password, "")
        self.assertFalse(s.validate_twilio_signature)

    def test_prod_rejects_empty_password_and_open_cors(self):
        with self.assertRaises(ValueError) as ctx:
            _settings(environment="prod")
        msg = str(ctx.exception)
        self.assertIn("DASHBOARD_PASSWORD", msg)
        self.assertIn("VALIDATE_TWILIO_SIGNATURE", msg)
        self.assertIn("WS_AUTH_TOKEN", msg)
        self.assertIn("ALLOWED_ORIGINS", msg)

    def test_prod_accepts_locked_down_config(self):
        s = _settings(
            environment="prod",
            dashboard_password="s3cret",
            validate_twilio_signature=True,
            twilio_base_url="https://ivr.example.com",
            ws_auth_token="stream-secret",
            allowed_origins="https://ivr.example.com",
            cno_jwt_secret="jwt-secret",
        )
        self.assertTrue(s.is_prod)


if __name__ == "__main__":
    unittest.main()
