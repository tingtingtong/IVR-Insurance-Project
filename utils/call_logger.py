"""
Structured per-call event logger.
Writes to structlog AND stores events in conversation_store for dashboard display.
"""
import time
import structlog
from typing import Any

log = structlog.get_logger()


def log_event(call_sid: str, event_type: str, **data: Any) -> None:
    """Emit a structured log line and store the event in conversation_store."""
    from services.conversation_store import add_call_event
    ts = time.time()
    log.info(event_type, call_sid=call_sid, **data)
    add_call_event(call_sid, event_type, {"ts": ts, **data})
