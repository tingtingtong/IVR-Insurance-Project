"""
Auto-sync Twilio webhooks on app startup.

Detects the public base URL and updates:
  - TwiML App voice URL  → {base}/webhook/voice   (browser softphone)
  - Phone number voice URL → {base}/twilio/voice   (inbound PSTN calls)

URL resolution order:
  1. TWILIO_BASE_URL env var (explicit — use for AWS ALB/CloudFront)
  2. ngrok tunnel auto-detection (local dev)
  3. Skip sync if neither is available
"""
import structlog
import httpx

log = structlog.get_logger()


def _detect_ngrok_url() -> str | None:
    """Check local ngrok API for an active HTTPS tunnel."""
    try:
        resp = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        tunnels = resp.json().get("tunnels", [])
        for t in tunnels:
            if t.get("public_url", "").startswith("https://"):
                return t["public_url"].rstrip("/")
    except Exception:
        pass
    return None


def resolve_base_url(settings) -> str | None:
    """Return the public base URL: explicit setting > ngrok > None."""
    explicit = (settings.twilio_base_url or "").strip().rstrip("/")
    if explicit:
        return explicit

    ngrok = _detect_ngrok_url()
    if ngrok:
        log.info("twilio_webhook_sync_ngrok_detected", url=ngrok)
        return ngrok

    return None


def sync_webhooks(settings) -> None:
    """Update Twilio TwiML app and phone number webhooks to match the current base URL."""
    if not settings.sync_twilio_webhooks:
        log.info("twilio_webhook_sync_disabled")
        return

    base_url = resolve_base_url(settings)
    if not base_url:
        log.warning("twilio_webhook_sync_skipped", reason="no base URL (set TWILIO_BASE_URL or start ngrok)")
        return

    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    if not sid or not token:
        log.warning("twilio_webhook_sync_skipped", reason="missing TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN")
        return

    try:
        from twilio.rest import Client
        client = Client(sid, token)
    except Exception as e:
        log.warning("twilio_webhook_sync_failed", error=str(e))
        return

    voice_url = f"{base_url}/webhook/voice"
    phone_url = f"{base_url}/twilio/voice"
    changed = []

    # ── TwiML App (browser softphone) ────────────────────────────────────────
    twiml_sid = settings.twilio_twiml_app_sid
    if twiml_sid:
        try:
            app = client.applications(twiml_sid).fetch()
            if app.voice_url != voice_url:
                client.applications(twiml_sid).update(
                    voice_url=voice_url,
                    voice_method="POST",
                )
                changed.append(f"TwiML app → {voice_url}")
            else:
                log.info("twilio_webhook_twiml_already_correct", url=voice_url)
        except Exception as e:
            log.warning("twilio_webhook_twiml_update_failed", error=str(e))

    # ── Phone number (inbound PSTN) ──────────────────────────────────────────
    phone = settings.twilio_phone_number
    if phone:
        try:
            numbers = client.incoming_phone_numbers.list(phone_number=phone)
            for n in numbers:
                if n.voice_url != phone_url:
                    n.update(voice_url=phone_url, voice_method="POST")
                    changed.append(f"Phone {phone} → {phone_url}")
                else:
                    log.info("twilio_webhook_phone_already_correct", phone=phone, url=phone_url)
        except Exception as e:
            log.warning("twilio_webhook_phone_update_failed", error=str(e))

    if changed:
        log.info("twilio_webhooks_synced", base_url=base_url, updates=changed)
    else:
        log.info("twilio_webhooks_up_to_date", base_url=base_url)
