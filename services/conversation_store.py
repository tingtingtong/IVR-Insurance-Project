"""
Call conversation store — in-memory cache with PostgreSQL write-through.

On startup call init_from_db() to pre-populate from the last 100 records so
the dashboard shows history after a server restart.  All writes go to both
the in-memory dict (for fast reads) and PostgreSQL (for persistence).
Webchat sessions remain in-memory only (ephemeral by design).
"""
from collections import deque
from datetime import datetime
from typing import Literal
from utils.pii_redactor import redact_turn
import services.call_db as _db

# ── Call (phone) conversations ────────────────────────────────────────────────
_calls: dict[str, dict] = {}
_call_order: deque[str] = deque(maxlen=100)


def init_from_db() -> None:
    """Load recent calls from PostgreSQL into the in-memory cache.
    Called once at server startup.  Safe to call even if DB is unavailable.
    """
    records = _db.load_recent_calls(limit=100)
    for record in reversed(records):  # oldest-first so deque order is newest-first
        sid = record.get("call_sid", "")
        if sid and sid not in _calls:
            _calls[sid] = record
            _call_order.appendleft(sid)

# ── Webchat sessions ──────────────────────────────────────────────────────────
_chats: dict[str, dict] = {}
_chat_order: deque[str] = deque(maxlen=100)


def start_call(call_sid: str, from_number: str) -> None:
    _calls[call_sid] = {
        "call_sid":      call_sid,
        "from_number":   from_number,
        "started_at":    datetime.now().isoformat(timespec="seconds"),
        "ended_at":      None,
        "status":        "active",
        "recording_url": None,
        "recording_sid": None,
        "turns":         [],
        "events":        [],
    }
    if call_sid not in _call_order:
        _call_order.appendleft(call_sid)
    _db.upsert_call(call_sid, _calls[call_sid])


def add_call_turn(
    call_sid: str,
    role: Literal["human", "bot"],
    text: str,
    intent: str = "",
    node: str = "",
) -> None:
    if call_sid not in _calls:
        return
    _calls[call_sid]["turns"].append({
        "ts":     datetime.now().isoformat(timespec="seconds"),
        "role":   role,
        "text":   redact_turn(role, text),
        "intent": intent,
        "node":   node,
    })
    _db.upsert_call(call_sid, _calls[call_sid])


def end_call(call_sid: str) -> None:
    if call_sid in _calls:
        _calls[call_sid]["ended_at"] = datetime.now().isoformat(timespec="seconds")
        _calls[call_sid]["status"]   = "ended"
        _db.upsert_call(call_sid, _calls[call_sid])


def set_recording(call_sid: str, recording_sid: str, recording_url: str) -> None:
    if call_sid in _calls:
        _calls[call_sid]["recording_sid"] = recording_sid
        _calls[call_sid]["recording_url"] = recording_url
        _db.upsert_call(call_sid, _calls[call_sid])


def _mask_phone(v: str) -> str:
    d = "".join(c for c in v if c.isdigit())
    return f"***-***-{d[-4:]}" if len(d) >= 4 else (v or "—")


def _mask_policy(v: str) -> str:
    if not v:
        return "—"
    if len(v) <= 5:
        return v[:2] + "****"
    return v[:3] + "****" + v[-3:]


def update_call_metadata(call_sid: str, state: dict) -> None:
    """Snapshot LangGraph state into the call record after each graph invocation."""
    if call_sid not in _calls:
        return
    c = _calls[call_sid]

    # ── PII captured during auth ──────────────────────────────────────────────
    pii = state.get("pii_collected") or {}
    c["pii_captured"] = {
        "phoneNumber":  {"raw": _mask_phone(pii.get("phoneNumber", "")),   "var": "pii_collected.phoneNumber"},
        "policyNumber": {"raw": _mask_policy(pii.get("policyNumber", "")), "var": "pii_collected.policyNumber"},
        "dateOfBirth":  {"raw": pii.get("dateOfBirth", "") or "—",         "var": "pii_collected.dateOfBirth"},
        "insuredName":  {"raw": pii.get("insuredName", "") or "—",         "var": "pii_collected.insuredName"},
    }

    # ── Auth state ────────────────────────────────────────────────────────────
    c["authenticated"]  = state.get("authenticated", False)
    c["auth_step"]      = state.get("auth_step", "")
    c["auth_attempts"]  = state.get("auth_attempts", 0)
    c["caller_name"]    = state.get("caller_name", "")
    c["caller_persona"] = state.get("caller_persona", "")

    # ── Flow state ────────────────────────────────────────────────────────────
    c["current_intent"] = state.get("current_intent", "")
    c["active_flow"]    = state.get("active_flow", "")
    c["current_node"]   = state.get("current_node", "")

    # ── Intent history (ordered, deduplicated on consecutive repeats) ─────────
    intent = state.get("current_intent", "")
    if intent and intent not in ("", "auth"):
        history: list = c.get("intent_history", [])
        if not history or history[-1] != intent:
            history = history + [intent]
        c["intent_history"] = history
    elif "intent_history" not in c:
        c["intent_history"] = []

    # ── Model info ────────────────────────────────────────────────────────────
    try:
        from config import settings as _s
        c["model_info"] = {
            "llm_model": _s.groq_model,
            "auth_mode": _s.auth_mode,
        }
    except Exception:
        pass

    _db.upsert_call(call_sid, c)


def get_calls() -> list[dict]:
    return [_calls[sid] for sid in _call_order if sid in _calls]


def add_call_event(call_sid: str, event_type: str, data: dict) -> None:
    """Append a structured event to the call's event list."""
    if call_sid not in _calls:
        return
    from datetime import timezone
    ts = data.get("ts", 0)
    ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(tz=timezone.utc).isoformat()
    event = {"ts": ts, "ts_str": ts_str, "event_type": event_type, **{k: v for k, v in data.items() if k != "ts"}}
    _calls[call_sid]["events"].append(event)
    _db.upsert_call(call_sid, _calls[call_sid])


def get_call(call_sid: str) -> dict:
    return _calls.get(call_sid, {})


# ── Webchat helpers ───────────────────────────────────────────────────────────

def ensure_chat(session_id: str) -> None:
    if session_id not in _chats:
        _chats[session_id] = {
            "session_id": session_id,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "turns":      [],
        }
        if session_id not in _chat_order:
            _chat_order.appendleft(session_id)


def add_chat_turn(
    session_id: str,
    role: Literal["human", "bot"],
    text: str,
    intent: str = "",
    node: str = "",
) -> None:
    ensure_chat(session_id)
    _chats[session_id]["turns"].append({
        "ts":     datetime.now().isoformat(timespec="seconds"),
        "role":   role,
        "text":   text,
        "intent": intent,
        "node":   node,
    })


def get_chat(session_id: str) -> dict:
    return _chats.get(session_id, {})


def get_chats() -> list[dict]:
    return [_chats[sid] for sid in _chat_order if sid in _chats]
