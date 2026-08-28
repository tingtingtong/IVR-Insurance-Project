"""
MLflow call tracker for CNO IVR.

Called once per call from conversation_store.end_call().
Each phone call becomes one MLflow Run under the CNO_IVR experiment.

Tracking URI: file:./mlruns  (local, no external DB required)
UI:           mlflow ui --port 5000 --backend-store-uri ./mlruns
"""
import json
import os
import tempfile
import pathlib
import structlog

log = structlog.get_logger()

_EXPERIMENT = "CNO_IVR"
_DB_PATH = str(pathlib.Path(__file__).parent.parent / "mlflow.db")

# Lazy init — only import mlflow when first call ends (avoids slowing startup)
_mlflow = None


def _get_mlflow():
    global _mlflow
    if _mlflow is None:
        import mlflow as _m
        _m.set_tracking_uri(f"sqlite:///{_DB_PATH}")
        try:
            _m.set_experiment(_EXPERIMENT)
        except Exception:
            pass
        _mlflow = _m
    return _mlflow


def log_call(call: dict) -> None:
    """
    Log a completed call record as a single MLflow Run.
    Silently skips if mlflow is unavailable or the call has no data.
    """
    if not call:
        return
    call_sid = call.get("call_sid", "")
    if not call_sid:
        return

    try:
        mlflow = _get_mlflow()
        with mlflow.start_run(run_name=call_sid[:20]):
            _log_params(mlflow, call)
            _log_metrics(mlflow, call)
            _log_tags(mlflow, call)
            _log_artifacts(mlflow, call)
        log.info("mlflow_call_logged", call_sid=call_sid)
    except Exception as e:
        # Never let MLflow errors affect the call flow
        log.warning("mlflow_log_failed", call_sid=call_sid, error=str(e))


# ── Parameters ────────────────────────────────────────────────────────────────

def _log_params(mlflow, call: dict) -> None:
    model_info = call.get("model_info", {})
    params = {
        "llm_model":         model_info.get("llm_model", ""),
        "stt_engine":        model_info.get("stt_engine", ""),
        "stt_model":         model_info.get("stt_model", ""),
        "stt_detail":        model_info.get("stt_detail", ""),
        "channel":           call.get("channel", "webhook"),
        "auth_mode":         model_info.get("auth_mode", ""),
    }
    try:
        from config import settings as _s
        params["max_auth_attempts"] = str(_s.max_auth_attempts)
        params["groq_model"]        = _s.groq_model
    except Exception:
        pass
    mlflow.log_params({k: v for k, v in params.items() if v})


# ── Metrics ───────────────────────────────────────────────────────────────────

def _log_metrics(mlflow, call: dict) -> None:
    metrics = {}

    # Duration
    dur = call.get("duration_seconds")
    if dur is not None:
        metrics["duration_seconds"] = float(dur)

    # Turn counts
    turns = call.get("turns", [])
    metrics["turn_count"]     = float(len(turns))
    bot_turns   = [t for t in turns if t.get("role") == "bot"]
    human_turns = [t for t in turns if t.get("role") == "human"]
    metrics["bot_turns"]   = float(len(bot_turns))
    metrics["human_turns"] = float(len(human_turns))

    if bot_turns:
        total_chars = sum(len(t.get("text", "")) for t in bot_turns)
        metrics["avg_tts_chars"] = float(total_chars / len(bot_turns))

    # Auth
    metrics["auth_attempts"] = float(call.get("auth_attempts", 0))
    metrics["authenticated"]  = 1.0 if call.get("authenticated") else 0.0

    # Intent outcomes
    intent_history = call.get("intent_history", [])
    metrics["unique_intents"] = float(len(set(intent_history)))
    metrics["escalated"]      = 1.0 if "escalate" in intent_history else 0.0
    metrics["goodbye"]        = 1.0 if "goodbye"  in intent_history else 0.0

    # Node-level latencies from events
    for event in call.get("events", []):
        if event.get("event_type") == "node_exit":
            node    = event.get("node", "")
            latency = event.get("latency_ms")
            if node and latency is not None:
                key = f"latency_{node}_ms"
                # Keep the longest (most representative) latency per node
                if key not in metrics or latency > metrics[key]:
                    metrics[key] = float(latency)

    # Pipeline latency aggregates (from Layer 2 instrumentation)
    _pipeline_events = ("stt_complete", "graph_complete", "tts_first_byte", "tts_complete", "turn_complete")
    events = call.get("events", [])
    for evt_type in _pipeline_events:
        matching = [e for e in events if e.get("event_type") == evt_type]
        if matching:
            vals = [e.get("latency_ms") or e.get("round_trip_ms") for e in matching]
            vals = [v for v in vals if v is not None]
            if vals:
                metrics[f"avg_{evt_type}_ms"] = float(sum(vals) / len(vals))
                sorted_vals = sorted(vals)
                metrics[f"p95_{evt_type}_ms"] = float(sorted_vals[min(int(len(sorted_vals) * 0.95), len(sorted_vals) - 1)])

    mlflow.log_metrics(metrics)


# ── Tags ──────────────────────────────────────────────────────────────────────

def _log_tags(mlflow, call: dict) -> None:
    intent_history = call.get("intent_history", [])

    tags = {
        "call_sid":      call.get("call_sid", ""),
        "from_number":   call.get("from_number", ""),
        "status":        call.get("status", ""),
        "channel":       call.get("channel", "webhook"),
        "caller_persona": call.get("caller_persona", ""),
        "caller_name":   call.get("caller_name", ""),
        "auth_result":   _auth_result(call),
        "final_intent":  call.get("current_intent", ""),
        "intent_path":   " → ".join(intent_history),
        "current_node":  call.get("current_node", ""),
    }
    try:
        from config import settings as _s
        tags["environment"] = _s.environment
    except Exception:
        pass

    mlflow.set_tags({k: str(v) for k, v in tags.items() if v})


def _auth_result(call: dict) -> str:
    if call.get("authenticated"):
        return "complete"
    step = call.get("auth_step", "")
    if step == "failed":
        return "failed"
    if step:
        return "incomplete"
    return ""


# ── Artifacts ─────────────────────────────────────────────────────────────────

def _log_artifacts(mlflow, call: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # transcript.txt
        transcript_path = os.path.join(tmp, "transcript.txt")
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(_build_transcript(call))
        mlflow.log_artifact(transcript_path)

        # events.json
        events_path = os.path.join(tmp, "events.json")
        with open(events_path, "w", encoding="utf-8") as f:
            json.dump(call.get("events", []), f, indent=2, default=str)
        mlflow.log_artifact(events_path)


def _build_transcript(call: dict) -> str:
    lines = [
        f"Call SID  : {call.get('call_sid', '')}",
        f"From      : {call.get('from_number', '')}",
        f"Channel   : {call.get('channel', '')}",
        f"Started   : {call.get('started_at', '')}",
        f"Ended     : {call.get('ended_at', '')}",
        f"Duration  : {call.get('duration_seconds', '—')}s",
        f"Persona   : {call.get('caller_persona', '—')}",
        f"Auth      : {_auth_result(call)}",
        f"Intents   : {' → '.join(call.get('intent_history', []))}",
        "",
        "─" * 60,
        "",
    ]
    for turn in call.get("turns", []):
        role = turn.get("role", "").upper().ljust(5)
        ts   = turn.get("ts", "")
        node = f" [{turn['node']}]" if turn.get("node") else ""
        text = turn.get("text", "")
        lines.append(f"[{ts}] {role}{node}")
        lines.append(f"  {text}")
        lines.append("")
    return "\n".join(lines)
