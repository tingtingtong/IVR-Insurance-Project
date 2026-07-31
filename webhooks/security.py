"""
Shared security dependencies for FastAPI routes.

  require_dashboard_auth — HTTP Basic Auth for /dashboard/* and /client/*
  validate_twilio_webhook — HMAC-SHA1 signature check for Twilio POST webhooks
"""
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from twilio.request_validator import RequestValidator

from config import settings

_security = HTTPBasic(auto_error=False)

# Cached at startup — both are immutable after settings load.
_twilio_validator = RequestValidator(settings.twilio_auth_token)
_twilio_base_url = settings.twilio_base_url.rstrip("/")


def require_dashboard_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(_security),
):
    """HTTP Basic Auth guard.  Auth is bypassed when DASHBOARD_PASSWORD is not set (dev)."""
    if not settings.dashboard_password:
        return  # dev mode — no password configured, allow all

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    correct_user = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.dashboard_username.encode("utf-8"),
    )
    correct_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.dashboard_password.encode("utf-8"),
    )
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


async def validate_twilio_webhook(request: Request):
    """Validate X-Twilio-Signature on inbound Twilio webhooks.

    Skipped when VALIDATE_TWILIO_SIGNATURE=false (dev default) or TWILIO_BASE_URL
    is not set.  Set both in production.
    """
    if not settings.validate_twilio_signature or not settings.twilio_base_url:
        return  # validation disabled — dev / staging without fixed URL

    signature = request.headers.get("X-Twilio-Signature", "")
    url = _twilio_base_url + str(request.url.path)

    # Starlette caches form data after the first parse — safe to call twice.
    form = await request.form()
    if not _twilio_validator.validate(url, dict(form), signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio webhook signature")
