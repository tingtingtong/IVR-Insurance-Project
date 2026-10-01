"""
Structured per-call event logger.
Writes to structlog AND stores events in conversation_store for dashboard display.
"""
import time
import structlog
from typing import Any

log = structlog.get_logger()


# Free-text fields that can carry the caller's words, names, numbers or dates.
# Redacted here once so every call site (and any future one) is covered (#75).
_TEXT_FIELDS = frozenset((
    "input", "transcript", "tts_preview", "parsed", "query", "raw",
    "text", "utterance", "from_number",
))
_NAME_FIELDS = frozenset(("caller_name", "cardholder_name", "authenticated_name"))


def _redact_fields(data: dict) -> dict:
    from utils.pii_redactor import redact_for_log
    out = {}
    for k, v in data.items():
        if k in _NAME_FIELDS and isinstance(v, str) and v:
            out[k] = "[NAME REDACTED]"
        elif k in _TEXT_FIELDS and isinstance(v, str):
            out[k] = redact_for_log(v)
        else:
            out[k] = v
    return out


def log_event(call_sid: str, event_type: str, **data: Any) -> None:
    """Emit a structured log line and store the event in conversation_store.

    Free-text and name fields are redacted first — both the app log and the
    stored events (dashboard, MLflow events.json) only ever see the safe copy.
    """
    from services.conversation_store import add_call_event
    ts = time.time()
    data = _redact_fields(data)
    log.info(event_type, call_sid=call_sid, **data)
    add_call_event(call_sid, event_type, {"ts": ts, **data})
