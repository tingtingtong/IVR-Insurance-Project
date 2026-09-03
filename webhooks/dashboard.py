"""
CNO IVR Dashboard — served at /dashboard
Tabs: WebChat | Calls | Softphone | Codebase | Graph | Logs | Config
"""
import secrets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from services.conversation_store import get_calls, get_call
from webhooks.security import require_dashboard_auth

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_auth)],
)


@router.get("/calls")
async def api_calls():
    return JSONResponse({"calls": get_calls()})


@router.get("/calls/{call_sid}")
async def api_call_detail(call_sid: str):
    return JSONResponse(get_call(call_sid))


@router.get("/calls/{call_sid}/events")
async def get_call_events(call_sid: str):
    from services.conversation_store import get_call
    call = get_call(call_sid)
    return JSONResponse({"call_sid": call_sid, "events": call.get("events", [])})


@router.post("/calls/cleanup")
async def cleanup_stale_calls():
    """Mark all 'active' calls as 'ended' — they're stale if Twilio has no active calls."""
    from services.conversation_store import get_calls, end_call
    calls = get_calls()
    cleaned = 0
    for c in calls:
        if c.get("status") == "active":
            end_call(c["call_sid"])
            cleaned += 1
    return JSONResponse({"cleaned": cleaned})


@router.get("/analytics")
async def api_analytics():
    """Aggregate analytics across all calls — auto-detect issues."""
    from services.conversation_store import get_calls
    calls = get_calls()
    total = len(calls)
    active = sum(1 for c in calls if c.get("status") == "active")
    ended = total - active

    # Issue detection
    issues = []
    auth_failures = 0
    escalations = 0
    stt_low_confidence = 0
    avg_turns = 0
    intent_counts: dict[str, int] = {}
    node_counts: dict[str, int] = {}
    auth_step_failures: dict[str, int] = {}
    total_graph_latency = 0
    graph_latency_count = 0

    for c in calls:
        events = c.get("events", [])
        turns = c.get("turns", [])
        avg_turns += len(turns)
        call_sid = c.get("call_sid", "")

        # Track intents
        for h in c.get("intent_history", []):
            intent_counts[h] = intent_counts.get(h, 0) + 1

        for ev in events:
            et = ev.get("event_type", "")

            # Auth failures
            if et == "auth_failed":
                auth_failures += 1
                step = ev.get("auth_step", "unknown")
                auth_step_failures[step] = auth_step_failures.get(step, 0) + 1
                issues.append({"call_sid": call_sid, "type": "auth_failure",
                    "detail": f"Auth failed at step: {step}, attempts: {ev.get('attempts', '?')}"})

            # Escalations
            if et == "node_enter" and ev.get("node") == "escalation":
                escalations += 1

            # STT low confidence
            if et == "stt_result":
                conf = ev.get("confidence", 1.0)
                if isinstance(conf, (int, float)) and conf < 0.7:
                    stt_low_confidence += 1
                    issues.append({"call_sid": call_sid, "type": "stt_low_confidence",
                        "detail": f"STT confidence {conf:.2f}: '{ev.get('transcript', '')[:50]}'"})

            # Graph latency spikes
            if et == "graph_result":
                lat = ev.get("graph_latency_ms", 0)
                if lat:
                    total_graph_latency += lat
                    graph_latency_count += 1
                if lat > 5000:
                    issues.append({"call_sid": call_sid, "type": "slow_graph",
                        "detail": f"Graph took {lat}ms (node={ev.get('node', '')})"})

            # API errors
            if et == "api_call" and not ev.get("success"):
                issues.append({"call_sid": call_sid, "type": "api_error",
                    "detail": f"API {ev.get('api', '?')} failed in {ev.get('node', '?')}: {ev.get('error', '')[:50]}"})

            # DOB mismatch
            if et == "auth_detail" and ev.get("action") == "dob_mismatch":
                issues.append({"call_sid": call_sid, "type": "dob_mismatch",
                    "detail": f"DOB mismatch attempt {ev.get('attempt', '?')}"})

            # Track nodes
            if et == "node_enter":
                n = ev.get("node", "")
                if n:
                    node_counts[n] = node_counts.get(n, 0) + 1

    avg_latency = int(total_graph_latency / graph_latency_count) if graph_latency_count else 0
    avg_turns_per_call = round(avg_turns / total, 1) if total else 0

    return JSONResponse({
        "summary": {
            "total_calls": total,
            "active_calls": active,
            "ended_calls": ended,
            "auth_failures": auth_failures,
            "escalations": escalations,
            "stt_low_confidence": stt_low_confidence,
            "avg_graph_latency_ms": avg_latency,
            "avg_turns_per_call": avg_turns_per_call,
        },
        "intent_distribution": intent_counts,
        "node_usage": node_counts,
        "auth_step_failures": auth_step_failures,
        "issues": issues[-50:],  # last 50 issues
    })


@router.get("/config")
async def api_config():
    from config import settings
    from services.realtime_auth import REALTIME_URL, REALTIME_VOICE

    def mask(v: str) -> str:
        v = str(v)
        if len(v) <= 8:
            return "****"
        return v[:4] + "****" + v[-4:]

    realtime_model = REALTIME_URL.split("model=")[-1] if "model=" in REALTIME_URL else "unknown"

    return JSONResponse({
        "llm": {
            "GROQ_MODEL":            settings.groq_model,
            "ROUTER_MODEL":          settings.router_model,
            "GROQ_API_KEY":          mask(settings.groq_api_key),
            "OPENAI_EMBEDDING_MODEL": settings.openai_embedding_model,
        },
        "realtime": {
            "AUTH_MODE":      settings.auth_mode,
            "OPENAI_API_KEY": mask(settings.openai_api_key),
            "REALTIME_MODEL": realtime_model,
            "REALTIME_VOICE": REALTIME_VOICE,
            "REALTIME_URL":   REALTIME_URL,
        },
        "stt": {
            "DEEPGRAM_API_KEY":           mask(settings.deepgram_api_key),
            "DEEPGRAM_MODEL":             settings.deepgram_model,
            "DEEPGRAM_LANGUAGE":          settings.deepgram_language,
            "DEEPGRAM_ENCODING":          settings.deepgram_encoding,
            "DEEPGRAM_SAMPLE_RATE":       str(settings.deepgram_sample_rate),
            "DEEPGRAM_CHANNELS":          str(settings.deepgram_channels),
            "DEEPGRAM_ENDPOINTING":       str(settings.deepgram_endpointing),
            "DEEPGRAM_UTTERANCE_END_MS":  str(settings.deepgram_utterance_end_ms),
            "DEEPGRAM_SMART_FORMAT":      str(settings.deepgram_smart_format),
            "DEEPGRAM_INTERIM_RESULTS":   str(settings.deepgram_interim_results),
            "DEEPGRAM_PUNCTUATE":         str(settings.deepgram_punctuate),
            "DEEPGRAM_NO_DELAY":          str(settings.deepgram_no_delay),
        },
        "tts": {
            "OPENAI_TTS_MODEL":                      settings.openai_tts_model,
            "OPENAI_TTS_VOICE":                      settings.openai_tts_voice,
            "OPENAI_TTS_FORMAT":                     "PCM 24kHz → mulaw 8kHz (hardcoded)",
            "WEBHOOK_TTS_VOICE":                     "Polly.Joanna",
            "ELEVENLABS_API_KEY":                    mask(settings.elevenlabs_api_key),
            "ELEVENLABS_VOICE_ID":                   settings.elevenlabs_voice_id,
            "ELEVENLABS_MODEL":                      settings.elevenlabs_model,
            "ELEVENLABS_STABILITY":                  str(settings.elevenlabs_stability),
            "ELEVENLABS_SIMILARITY_BOOST":           str(settings.elevenlabs_similarity_boost),
            "ELEVENLABS_OPTIMIZE_STREAMING_LATENCY": str(settings.elevenlabs_optimize_streaming_latency),
        },
        "twilio": {
            "TWILIO_PHONE_NUMBER":        settings.twilio_phone_number,
            "TWILIO_AGENT_PHONE_NUMBER":  settings.twilio_agent_phone_number,
            "TWILIO_TWIML_APP_SID":       settings.twilio_twiml_app_sid,
            "TWILIO_ACCOUNT_SID":         mask(settings.twilio_account_sid),
            "TWILIO_AUTH_TOKEN":          mask(settings.twilio_auth_token),
            "TWILIO_API_KEY":             mask(settings.twilio_api_key),
            "TWILIO_API_SECRET":          mask(settings.twilio_api_secret),
        },
        "backend": {
            "CNO_API_BASE_URL": settings.cno_api_base_url,
            "CNO_API_KEY":      mask(settings.cno_api_key) if settings.cno_api_key else "(not set)",
            "CNO_JWT_SECRET":   mask(settings.cno_jwt_secret) if settings.cno_jwt_secret else "(not set)",
        },
        "feature_flags": {
            "ENABLE_RAG":               str(settings.enable_rag),
            "FAQ_FALLBACK_TO_ESCALATE": str(settings.faq_fallback_to_escalate),
            "MAX_AUTH_ATTEMPTS":        str(settings.max_auth_attempts),
        },
        "security": {
            "DASHBOARD_USERNAME":          settings.dashboard_username,
            "DASHBOARD_PASSWORD":          "****" if settings.dashboard_password else "(not set)",
            "VALIDATE_TWILIO_SIGNATURE":   str(settings.validate_twilio_signature),
            "TWILIO_BASE_URL":             settings.twilio_base_url or "(not set)",
            "WS_AUTH_TOKEN":               mask(settings.ws_auth_token) if settings.ws_auth_token else "(not set)",
            "ALLOWED_ORIGINS":             settings.allowed_origins,
        },
        "infra": {
            "REDIS_URL":    settings.redis_url,
            "DATABASE_URL": settings.database_url.replace(settings.database_url.split("@")[-1], "***") if "@" in settings.database_url else settings.database_url,
            "ENVIRONMENT":  settings.environment,
            "LOG_LEVEL":    settings.log_level,
            "APP_HOST":     settings.app_host,
            "APP_PORT":     str(settings.app_port),
        },
        # These keys are computed/hardcoded — not writable via the dashboard
        "_readonly": ["REALTIME_MODEL", "REALTIME_URL", "OPENAI_TTS_FORMAT", "WEBHOOK_TTS_VOICE"],
    })


_ENV_PATH = __file__  # resolved below
import pathlib as _pl
_ENV_FILE = _pl.Path(__file__).parent.parent / ".env"

def _update_env_file(key: str, value: str) -> None:
    """Write or update KEY=VALUE in the .env file."""
    lines = _ENV_FILE.read_text(encoding="utf-8").splitlines() if _ENV_FILE.exists() else []
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


from fastapi import Body

@router.post("/config")
async def api_config_update(payload: dict = Body(...)):
    """Update a single .env key. Requires password."""
    password = payload.get("password", "")
    key      = payload.get("key", "").strip().upper()
    value    = payload.get("value", "")

    from config import settings as _settings
    cfg_pw = _settings.dashboard_password
    if not cfg_pw or not secrets.compare_digest(password.encode("utf-8"), cfg_pw.encode("utf-8")):
        return JSONResponse({"ok": False, "error": "Invalid password"}, status_code=403)

    readonly = {"REALTIME_MODEL", "REALTIME_URL", "OPENAI_TTS_FORMAT", "WEBHOOK_TTS_VOICE"}
    if key in readonly:
        return JSONResponse({"ok": False, "error": f"{key} is read-only (hardcoded in source)"}, status_code=400)

    if not key:
        return JSONResponse({"ok": False, "error": "key is required"}, status_code=400)

    _update_env_file(key, value)
    return JSONResponse({"ok": True, "restart_required": True,
                         "message": f"{key} updated. Restart the server for changes to take effect."})


@router.get("/call/{call_sid}", response_class=HTMLResponse)
async def call_detail_page(call_sid: str):
    """Standalone shareable page for a single call — can be sent to others."""
    call = get_call(call_sid)
    if not call:
        return HTMLResponse("<h2>Call not found</h2>", status_code=404)

    turns_html = ""
    for t in call.get("turns", []):
        role_label = "YOU" if t["role"] == "human" else "BOT"
        role_cls   = t["role"]
        ts         = t["ts"][11:19]
        intent_badge = f'<span style="font-size:10px;background:rgba(56,189,248,.15);color:#38bdf8;padding:1px 6px;border-radius:3px;margin-left:6px">{t.get("intent","")}</span>' if t.get("intent") else ""
        node_badge   = f'<span style="font-size:10px;background:rgba(34,197,94,.1);color:#22c55e;padding:1px 6px;border-radius:3px;margin-left:4px">{t.get("node","")}</span>' if t.get("node") else ""
        turns_html += f"""
        <div style="display:flex;gap:10px;margin-bottom:12px">
          <div style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;flex-shrink:0;min-width:38px;text-align:center;margin-top:2px;{'background:rgba(59,130,246,.2);color:#3b82f6' if role_cls=='human' else 'background:rgba(167,139,250,.2);color:#a78bfa'}">{role_label}</div>
          <div style="flex:1">
            <div style="color:#e2e8f0;font-size:13px;line-height:1.5">{t["text"]}</div>
            <div style="font-size:11px;color:#475569;margin-top:3px">{ts}{intent_badge}{node_badge}</div>
          </div>
        </div>"""

    events_html = ""
    for ev in call.get("events", []):
        import datetime as _dt
        ts_ev = _dt.datetime.fromtimestamp(ev.get("ts", 0)).strftime("%H:%M:%S") if ev.get("ts") else ""
        extras = " ".join(f"{k}={str(v)[:30]}" for k, v in ev.items() if k not in ("ts", "ts_str", "event_type"))
        events_html += f'<div style="font-size:11px;font-family:monospace;padding:2px 0;border-bottom:1px solid #1e293b"><span style="color:#475569;display:inline-block;width:80px">{ts_ev}</span><span style="color:#38bdf8;display:inline-block;width:160px">{ev.get("event_type","")}</span><span style="color:#94a3b8">{extras}</span></div>'

    pii = call.get("pii_captured", {})
    status_color = "#22c55e" if call.get("status") == "active" else "#64748b"
    status_label = "&#9679; Live" if call.get("status") == "active" else "Ended"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Call {call_sid[:16]} — insuranceCompany IVR</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;max-width:900px;margin:0 auto}}
h1{{font-size:18px;font-weight:700;margin-bottom:4px}}
.sub{{font-size:12px;color:#64748b;margin-bottom:20px}}
.card{{background:#1e293b;border-radius:10px;padding:16px 20px;margin-bottom:16px}}
.card h3{{font-size:11px;font-weight:700;color:#38bdf8;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}}
.meta-row{{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}}
.meta-row:last-child{{border-bottom:none}}
.lbl{{color:#64748b}} .val{{font-family:monospace;color:#e2e8f0}}
.copy-all{{float:right;background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);color:#60a5fa;border-radius:5px;padding:4px 12px;font-size:11px;cursor:pointer}}
.copy-all:hover{{background:rgba(59,130,246,.3)}}
</style>
</head>
<body>
<h1>Call Transcript</h1>
<p class="sub">
  <span style="font-family:monospace;font-size:11px;color:#94a3b8">{call_sid}</span>
  &nbsp;&middot;&nbsp;
  <span style="color:{status_color}">{status_label}</span>
  &nbsp;&middot;&nbsp; Started {call.get("started_at","")[:19]}
  &nbsp;&middot;&nbsp; From {call.get("from_number","").replace("client:","").replace("+1","")}
</p>

<div class="card">
  <h3>Auth &amp; PII <button class="copy-all" onclick="cpySection('meta-sec')">&#x2398; Copy</button></h3>
  <div id="meta-sec">
  <div class="meta-row"><span class="lbl">Authenticated</span><span class="val">{"Yes" if call.get("authenticated") else "No"}</span></div>
  <div class="meta-row"><span class="lbl">Auth Step</span><span class="val">{call.get("auth_step","&mdash;")}</span></div>
  <div class="meta-row"><span class="lbl">Caller Name</span><span class="val">{call.get("caller_name","&mdash;")}</span></div>
  <div class="meta-row"><span class="lbl">Persona</span><span class="val">{call.get("caller_persona","&mdash;")}</span></div>
  <div class="meta-row"><span class="lbl">Phone</span><span class="val">{pii.get("phoneNumber",{{}}).get("raw","&mdash;")}</span></div>
  <div class="meta-row"><span class="lbl">Policy</span><span class="val">{pii.get("policyNumber",{{}}).get("raw","&mdash;")}</span></div>
  <div class="meta-row"><span class="lbl">DOB</span><span class="val">{pii.get("dateOfBirth",{{}}).get("raw","&mdash;")}</span></div>
  </div>
</div>

<div class="card">
  <h3>Transcript <button class="copy-all" onclick="cpySection('turns-sec')">&#x2398; Copy</button></h3>
  <div id="turns-sec">{turns_html or '<div style="color:#475569;padding:10px 0">No turns recorded.</div>'}</div>
</div>

<div class="card">
  <h3>Event Timeline <button class="copy-all" onclick="cpySection('ev-sec')">&#x2398; Copy</button></h3>
  <div id="ev-sec">{events_html or '<div style="color:#475569;font-size:12px;padding:8px 0">No events recorded.</div>'}</div>
</div>

<script>
function cpySection(id) {{
  const el = document.getElementById(id);
  const btn = event.target;
  navigator.clipboard.writeText(el.innerText).then(() => {{
    const orig = btn.textContent; btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 1500);
  }});
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("", response_class=HTMLResponse)
async def dashboard():
    return _HTML


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IVR Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;flex-direction:column}
/* topbar */
.topbar{background:#1e293b;border-bottom:1px solid #334155;padding:0 20px;display:flex;align-items:center;gap:12px;height:50px;flex-shrink:0}
.topbar h1{font-size:15px;font-weight:700;color:#e2e8f0}
.topbar .live{font-size:10px;padding:2px 8px;border-radius:99px;background:#22c55e;color:#000;font-weight:700}
.topbar a{font-size:12px;color:#64748b;text-decoration:none;padding:4px 10px;border-radius:5px;border:1px solid #334155;margin-left:auto}
.topbar a:hover{color:#e2e8f0;border-color:#3b82f6}
/* tabs */
.tabbar{background:#1e293b;border-bottom:1px solid #334155;display:flex;padding:0 20px;flex-shrink:0}
.tab{padding:10px 18px;font-size:13px;font-weight:500;cursor:pointer;color:#64748b;border-bottom:2px solid transparent;user-select:none}
.tab:hover{color:#e2e8f0}
.tab.on{color:#3b82f6;border-bottom-color:#3b82f6}
/* panes */
.body{flex:1;overflow:hidden;position:relative}
.pane{display:none;position:absolute;inset:0;overflow:hidden}
.pane.on{display:flex}

/* ── CHAT ── */
.chat-wrap{display:flex;width:100%;height:100%}
.chat-side{width:220px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column;flex-shrink:0}
.chat-side-title{padding:12px 14px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between}
.btn-new{background:#3b82f6;color:#fff;border:none;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer}
.sess-list{flex:1;overflow-y:auto}
.sess-item{padding:10px 14px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.05);font-size:12px}
.sess-item:hover{background:rgba(255,255,255,.04)}
.sess-item.on{background:rgba(59,130,246,.12);border-left:2px solid #3b82f6}
.sess-item .sid{font-size:10px;color:#64748b;font-family:monospace}
.sess-item .prev{color:#e2e8f0;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.chat-hdr{padding:10px 16px;border-bottom:1px solid #334155;font-size:12px;color:#64748b;flex-shrink:0}
.msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.msg{display:flex;gap:8px;max-width:85%}
.msg.u{align-self:flex-end;flex-direction:row-reverse}
.msg.b{align-self:flex-start}
.bubble{padding:9px 13px;border-radius:14px;font-size:13px;line-height:1.5}
.u .bubble{background:#3b82f6;color:#fff;border-bottom-right-radius:3px}
.b .bubble{background:#1e293b;border:1px solid #334155;border-bottom-left-radius:3px}
.av{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0;align-self:flex-end}
.b .av{background:#7c3aed;color:#fff}
.u .av{background:#3b82f6;color:#fff}
.tag{display:inline-block;font-size:9px;padding:1px 5px;border-radius:99px;background:rgba(56,189,248,.15);color:#38bdf8;margin-left:4px}
.mm{font-size:10px;color:#64748b;margin-top:3px}
.typing{display:flex;gap:4px;align-items:center;padding:10px 13px}
.typing span{width:5px;height:5px;background:#64748b;border-radius:50%;animation:blink 1.2s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
.inp-area{padding:12px 16px;border-top:1px solid #334155;display:flex;gap:8px;flex-shrink:0}
.inp{flex:1;background:#1e293b;border:1px solid #334155;border-radius:8px;padding:9px 12px;color:#e2e8f0;font-size:13px;outline:none;resize:none;font-family:inherit}
.inp:focus{border-color:#3b82f6}
.btn-send{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:0 16px;font-size:13px;font-weight:600;cursor:pointer}
.btn-send:disabled{opacity:.4;cursor:not-allowed}

/* ── CALLS ── */
.calls-wrap{display:flex;width:100%;height:100%}
.calls-list{width:280px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column;flex-shrink:0}
.panel-hdr{padding:12px 14px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between}
.refresh{background:none;border:1px solid #334155;color:#64748b;padding:2px 7px;border-radius:4px;cursor:pointer;font-size:11px}
.calls-scroll{flex:1;overflow-y:auto}
.call-item{padding:12px 14px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,.05)}
.call-item:hover{background:rgba(255,255,255,.04)}
.call-item.on{background:rgba(59,130,246,.1);border-left:2px solid #3b82f6}
.call-item .csid{font-size:10px;font-family:monospace;color:#64748b}
.call-item .cfrom{font-size:13px;font-weight:500;margin-top:3px}
.call-item .cmeta{font-size:11px;color:#64748b;margin-top:3px;display:flex;gap:6px;align-items:center}
.dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.dot.active{background:#22c55e}
.dot.ended{background:#64748b}
.call-detail{flex:1;display:flex;flex-direction:column;overflow:hidden}
.call-detail-hdr{padding:14px 18px;border-bottom:1px solid #334155;font-size:13px;font-weight:600;flex-shrink:0}
.call-detail-scroll{flex:1;overflow-y:auto;display:flex;flex-direction:column}
/* ── Call state panel ── */
#call-state-panel{flex-shrink:0;padding:12px 18px;border-bottom:1px solid #334155;display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:12px}
.csp-card{background:#1e293b;border-radius:8px;padding:10px 12px}
.csp-card h4{font-size:11px;font-weight:700;color:#38bdf8;margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em}
.csp-row{display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.csp-row:last-child{border-bottom:none}
.csp-label{color:#64748b;font-size:11px}
.csp-val{font-family:monospace;font-size:11px;color:#e2e8f0;text-align:right;word-break:break-all;max-width:160px}
.csp-val.ok{color:#22c55e;font-weight:700}
.csp-val.warn{color:#fb923c;font-weight:700}
.csp-val.na{color:#475569;font-style:italic}
.intent-badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:10px;margin:2px 2px 2px 0;background:rgba(56,189,248,.15);color:#38bdf8}
.intent-badge.active{background:rgba(34,197,94,.2);color:#22c55e;font-weight:700}
.turns{flex:1;min-height:200px;padding:16px;display:flex;flex-direction:column;gap:10px}
.turn{display:flex;gap:10px}
.role{font-size:10px;font-weight:700;padding:2px 7px;border-radius:3px;flex-shrink:0;min-width:38px;text-align:center;margin-top:2px}
.turn.human .role{background:rgba(59,130,246,.2);color:#3b82f6}
.turn.bot .role{background:rgba(167,139,250,.2);color:#a78bfa}
.turn-body{flex:1}
.turn-text{font-size:13px;line-height:1.5}
.turn-meta{font-size:10px;color:#64748b;margin-top:3px;display:flex;gap:6px}
.tk{font-size:10px;padding:1px 5px;border-radius:99px}
.tk.intent{background:rgba(56,189,248,.15);color:#38bdf8}
.tk.node{background:rgba(34,197,94,.15);color:#22c55e}
.empty-msg{flex:1;display:flex;align-items:center;justify-content:center;color:#64748b;font-size:13px}

/* ── EVENT TIMELINE ── */
#event-timeline{padding:10px 18px 6px;border-bottom:1px solid #334155;flex-shrink:0}
#event-timeline h5{font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
#ev-rows{max-height:180px;overflow-y:auto}
.ev-row{display:flex;gap:8px;font-size:11px;font-family:monospace;padding:2px 0;border-bottom:1px solid rgba(255,255,255,.03);line-height:1.5}
.ev-ts{color:#475569;flex-shrink:0;width:90px}
.ev-type{font-weight:700;flex-shrink:0;width:130px;overflow:hidden;text-overflow:ellipsis}
.ev-data{color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev-call_start,.ev-call_end{color:#22c55e}
.ev-auth_step_change,.ev-auth_complete,.ev-auth_failed,.ev-node_enter{color:#38bdf8}
.ev-error{color:#ef4444}
.ev-rag_retrieved,.ev-llm_response{color:#a78bfa}
.ev-node_exit{color:#64748b}
.ev-intent_detected{color:#f59e0b}
.ev-tts_sent{color:#0ea5e9}
.copy-btn{position:absolute;top:8px;right:10px;background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3);color:#60a5fa;border-radius:5px;padding:3px 9px;font-size:10px;cursor:pointer;font-family:monospace;transition:background .15s}
.copy-btn:hover{background:rgba(59,130,246,.3)}
.copy-ok{background:rgba(34,197,94,.15)!important;border-color:rgba(34,197,94,.3)!important;color:#4ade80!important}
.section-with-copy{position:relative}

/* ── CODEBASE ── */
.code-wrap{display:flex;width:100%;height:100%}
.code-nav{width:200px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column;flex-shrink:0;overflow-y:auto}
.code-nav-title{padding:12px 14px;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #334155}
.code-nav-item{padding:10px 14px;cursor:pointer;font-size:12px;color:#64748b;border-bottom:1px solid rgba(255,255,255,.04);display:flex;align-items:center;gap:8px}
.code-nav-item:hover{color:#e2e8f0;background:rgba(255,255,255,.03)}
.code-nav-item.on{color:#3b82f6;background:rgba(59,130,246,.08);border-left:2px solid #3b82f6}
.code-nav-item .n{font-size:10px;background:#334155;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.code-nav-item.on .n{background:#3b82f6;color:#fff}
.code-body{flex:1;overflow-y:auto;padding:28px 36px}
.code-section{display:none}
.code-section.on{display:block}
.code-section h2{font-size:18px;font-weight:700;margin-bottom:6px}
.code-section .sub{font-size:12px;color:#64748b;margin-bottom:24px}
.block{margin-bottom:28px}
.block h3{font-size:13px;font-weight:600;color:#38bdf8;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #334155}
.block p,.block li{font-size:13px;line-height:1.7;color:#cbd5e1}
.block ul{padding-left:18px;margin-top:4px}
.chip{display:inline-block;font-size:11px;font-family:monospace;background:rgba(59,130,246,.1);color:#38bdf8;padding:1px 6px;border-radius:4px;border:1px solid rgba(59,130,246,.2)}
.box{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:12px 14px;margin:10px 0;font-size:12px;font-family:monospace;color:#94a3b8;line-height:1.6;white-space:pre;overflow-x:auto}
.flow{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:14px;font-family:monospace;font-size:12px;color:#94a3b8;white-space:pre;overflow-x:auto;line-height:1.7;margin:10px 0}
.tbl{width:100%;border-collapse:collapse;font-size:12px;margin:10px 0}
.tbl th{text-align:left;padding:7px 10px;background:#334155;color:#64748b;font-size:11px;text-transform:uppercase}
.tbl td{padding:7px 10px;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:top}
.tbl td:first-child{font-family:monospace;color:#38bdf8;width:160px}
.kw{color:#a78bfa}.fn{color:#38bdf8}.str{color:#22c55e}.cmt{color:#64748b;font-style:italic}

/* ── GRAPH ── */
.graph-wrap{padding:28px 36px;overflow-y:auto;width:100%;display:flex;gap:36px;flex-wrap:wrap}
.graph-wrap h2{font-size:18px;font-weight:700;margin-bottom:20px;width:100%}
.gdiag{font-family:monospace;font-size:12px;color:#94a3b8;line-height:1.9;white-space:pre;background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px 24px;flex:1;min-width:460px}
.state-ref{flex:1;min-width:300px}
.state-ref h3{font-size:13px;font-weight:600;color:#38bdf8;margin-bottom:10px}
.sg{margin-bottom:18px}
.sg h4{font-size:10px;font-weight:700;text-transform:uppercase;color:#64748b;letter-spacing:.06em;margin-bottom:6px;padding-bottom:3px;border-bottom:1px solid #334155}
.sr{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:12px}
.sk{font-family:monospace;color:#f59e0b;width:150px;flex-shrink:0}
.st{color:#a78bfa;font-family:monospace;font-size:11px;width:70px;flex-shrink:0}
.sd{color:#94a3b8;line-height:1.4}

/* ── CONFIG ── */
.cfg-wrap{width:100%;height:100%;overflow-y:auto;padding:28px 36px}
.cfg-wrap h2{font-size:18px;font-weight:700;margin-bottom:24px}
.cfg-section{margin-bottom:32px}
.cfg-section h3{font-size:13px;font-weight:600;color:#38bdf8;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #334155}
.cfg-row{display:flex;align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.cfg-key{font-family:monospace;font-size:12px;color:#f59e0b;width:230px;flex-shrink:0;padding-top:2px}
.cfg-val{font-family:monospace;font-size:12px;color:#e2e8f0;flex:1;word-break:break-all}
.cfg-val.active{color:#22c55e;font-weight:600}
.cfg-val.warn{color:#fb923c}
.cfg-info{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:#334155;color:#64748b;font-size:10px;font-weight:700;cursor:help;flex-shrink:0;margin-top:2px}
.cfg-info:hover .cfg-tip{display:block}
.cfg-tip{display:none;position:absolute;bottom:calc(100% + 8px);left:50%;transform:translateX(-50%);background:#1e293b;border:1px solid #475569;border-radius:8px;padding:10px 13px;font-size:12px;color:#e2e8f0;min-width:240px;max-width:320px;z-index:200;white-space:normal;line-height:1.6;box-shadow:0 8px 24px rgba(0,0,0,.5)}
.cfg-tip .tip-best{color:#22c55e;margin-top:6px;font-size:11px}
.cfg-tip::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);border:6px solid transparent;border-top-color:#475569}
.cfg-edit-btn{background:none;border:none;cursor:pointer;font-size:13px;opacity:.5;padding:0 2px;flex-shrink:0;line-height:1;transition:opacity .15s}.cfg-edit-btn:hover{opacity:1}
.cfg-ro{font-size:10px;color:#475569;font-style:italic;flex-shrink:0;padding-top:3px}

/* ── LOGS ── */
.logs-wrap{display:flex;flex-direction:column;width:100%;height:100%}
.logs-toolbar{background:#1e293b;padding:8px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #334155;flex-shrink:0}
.logs-toolbar h3{font-size:13px;font-weight:600;flex:1}
.ldot{width:8px;height:8px;border-radius:50%;background:#22c55e;animation:blink 2s infinite}
.ldot.err{background:#ef4444;animation:none}
.lcnt{font-size:11px;color:#64748b}
.lbtn{background:#334155;color:#e2e8f0;border:none;border-radius:4px;padding:3px 9px;font-size:11px;cursor:pointer}
.lbtn:hover{background:#475569}
.lbtn.on{background:#3b82f6}
#logbox{flex:1;overflow-y:auto;padding:10px 14px;font-family:monospace;font-size:12px;line-height:1.7}
.ll{display:block;white-space:pre-wrap;word-break:break-all}
.ll.sp{color:#38bdf8;font-weight:bold}
.ll.ca{color:#a78bfa}
.ll.au{color:#fb923c}
.ll.gr{color:#34d399}
.ll.er{color:#ef4444}
.ll.wa{color:#f59e0b}
.ll.ht{color:#475569}
.ll.ok{color:#22c55e}
.ll.in{color:#94a3b8}
</style>
</head>
<body>

<div class="topbar">
  <h1>IVR Dashboard</h1>
  <span class="live">LIVE</span>
  <a href="/client" target="_blank">Softphone</a>
</div>

<div class="tabbar">
  <div class="tab on" data-pane="chat">WebChat</div>
  <div class="tab" data-pane="calls">Calls <span id="call-cnt" style="font-size:10px;background:#3b82f6;color:#fff;padding:1px 6px;border-radius:99px;margin-left:4px">0</span></div>
  <div class="tab" data-pane="phone">📞 Softphone</div>
  <div class="tab" data-pane="code">Codebase</div>
  <div class="tab" data-pane="graph">Graph</div>
  <div class="tab" data-pane="logs">Live Logs</div>
  <div class="tab" data-pane="analytics">Analytics</div>
  <div class="tab" data-pane="cfg">⚙️ Config</div>
</div>

<div class="body">

<!-- CHAT -->
<div class="pane on" id="pane-chat">
  <div class="chat-wrap">
    <div class="chat-side">
      <div class="chat-side-title">Sessions <button class="btn-new" onclick="newChat()">+ New</button></div>
      <div class="sess-list" id="sess-list"></div>
    </div>
    <div class="chat-main">
      <div class="chat-hdr">Session: <span id="chat-sid" style="font-family:monospace;font-size:11px;color:#38bdf8">—</span></div>
      <div class="msgs" id="msgs"></div>
      <div class="inp-area">
        <textarea class="inp" id="inp" placeholder="Type a message and press Enter..." rows="1"></textarea>
        <button class="btn-send" id="btn-send" onclick="send()" disabled>Send</button>
      </div>
    </div>
  </div>
</div>

<!-- CALLS -->
<div class="pane" id="pane-calls">
  <div class="calls-wrap">
    <div class="calls-list">
      <div class="panel-hdr">Phone Calls <button class="refresh" onclick="loadCalls()">Refresh</button></div>
      <div class="calls-scroll" id="call-list"><div class="empty-msg">No calls yet</div></div>
    </div>
    <div class="call-detail">
      <div class="call-detail-hdr" id="call-hdr">Select a call to view transcript</div>
      <div class="call-detail-scroll">
        <div id="call-state-panel"></div>
        <div id="event-timeline" style="display:none"><h5>Event Timeline</h5><div id="ev-rows"></div></div>
        <div class="turns" id="call-turns"><div class="empty-msg">Select a call from the list</div></div>
      </div>
    </div>
  </div>
</div>

<!-- SOFTPHONE -->
<div class="pane" id="pane-phone">
  <div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;background:#0f172a">
    <div style="background:#1e293b;border-radius:16px;padding:36px;width:340px;box-shadow:0 20px 60px rgba(0,0,0,.5)">
      <h2 style="font-size:17px;font-weight:600;margin-bottom:3px;color:#f1f5f9">IVR Softphone</h2>
      <p style="font-size:12px;color:#64748b;margin-bottom:20px">Browser call — no physical phone needed</p>
      <div id="ph-ngrok-warn" style="display:none;background:rgba(251,146,60,.1);border:1px solid rgba(251,146,60,.3);border-radius:8px;padding:10px 12px;font-size:12px;color:#fb923c;margin-bottom:14px">
        ⚠️ <strong>ngrok required for voice calls.</strong><br>
        Run: <code style="background:#0f172a;padding:2px 6px;border-radius:4px">ngrok http 8888</code><br>
        Then set the Twilio TwiML App Voice URL to your ngrok URL + <code style="background:#0f172a;padding:2px 6px;border-radius:4px">/webhook/voice</code>
      </div>
      <div style="background:#0f172a;border-radius:8px;padding:10px 14px;font-size:13px;color:#94a3b8;margin-bottom:20px;display:flex;align-items:center;gap:8px">
        <div id="ph-dot" style="width:8px;height:8px;border-radius:50%;background:#475569;flex-shrink:0;transition:background .3s"></div>
        <span id="ph-status">Initialising...</span>
      </div>
      <div id="ph-timer" style="text-align:center;font-size:28px;font-weight:300;color:#3b82f6;margin:12px 0;letter-spacing:2px;display:none">0:00</div>
      <button id="ph-btn-call" onclick="phCall()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:8px;background:#22c55e;color:#fff;opacity:.35">Call IVR</button>
      <button id="ph-btn-mute" onclick="phMute()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:8px;background:#334155;color:#e2e8f0;opacity:.35">Mute</button>
      <button id="ph-btn-hang" onclick="phHang()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;background:#ef4444;color:#fff;opacity:.35">Hang Up</button>
      <div id="ph-log" style="background:#0f172a;border-radius:8px;padding:10px;font-size:11px;color:#475569;height:100px;overflow-y:auto;font-family:monospace;margin-top:10px"></div>
    </div>
  </div>
</div>

<!-- CODEBASE -->
<div class="pane" id="pane-code">
  <div class="code-wrap">
    <div class="code-nav">
      <div class="code-nav-title">Sessions</div>
      <div class="code-nav-item on" data-s="s1"><span class="n">1</span>Entry Point</div>
      <div class="code-nav-item" data-s="s2"><span class="n">2</span>State &amp; Graph</div>
      <div class="code-nav-item" data-s="s3"><span class="n">3</span>Router Node</div>
      <div class="code-nav-item" data-s="s4"><span class="n">4</span>Auth Node</div>
      <div class="code-nav-item" data-s="s5"><span class="n">5</span>Service Nodes</div>
      <div class="code-nav-item" data-s="s6"><span class="n">6</span>Infrastructure</div>
    </div>
    <div class="code-body">

      <div class="code-section on" id="s1">
        <h2>Session 1 — Entry Point &amp; Request Lifecycle</h2>
        <p class="sub">How a phone call becomes an LLM response: <span class="chip">run.py</span> → <span class="chip">main.py</span> → <span class="chip">webhooks/twilio_voice.py</span></p>
        <div class="block">
          <h3>run.py — three jobs before server starts</h3>
          <ul>
            <li><strong>Tee stdout/stderr</strong> — wraps sys.stdout with _Tee so every print/log line goes to terminal + ivr.log + in-memory log_bus (powers SSE live viewer)</li>
            <li><strong>Windows event loop policy</strong> — must be set before uvicorn creates the loop</li>
            <li><strong>uvicorn.run("main:app", port=8888)</strong> — starts the FastAPI server</li>
          </ul>
          <div class="box"><span class="kw">class</span> <span class="fn">_Tee</span>:
    <span class="kw">def</span> <span class="fn">write</span>(self, msg):
        self._s.write(msg)           <span class="cmt"># → terminal</span>
        self._f.write(msg)           <span class="cmt"># → ivr.log file</span>
        <span class="kw">from</span> log_bus <span class="kw">import</span> push
        push(line)                   <span class="cmt"># → SSE /client/logs/stream</span></div>
        </div>
        <div class="block">
          <h3>main.py — FastAPI app wiring</h3>
          <ul>
            <li>Routers: voice_router (/webhook/*), browser_router (/client/*), chat_router (/chat/*), dashboard_router (/dashboard/*)</li>
            <li>structlog writes to sys.stdout → captured by _Tee above</li>
            <li>CORS middleware: allow_origins=["*"] for dev</li>
          </ul>
        </div>
        <div class="block">
          <h3>webhooks/twilio_voice.py — the IVR loop</h3>
          <div class="flow">POST /webhook/voice  →  greet caller, start &lt;Gather&gt;

POST /webhook/gather (each caller utterance):
  1. SpeechResult="" ?  → re-prompt "I didn't catch that"
  2. cno_graph.ainvoke({messages:[HumanMessage(transcript)]}, thread_id=call_sid)
  3. result.transfer_to set?  → &lt;Say&gt; + &lt;Dial&gt;
  4. current_node=="goodbye"? → &lt;Say&gt; + &lt;Hangup/&gt;
  5. else                     → &lt;Say tts_text&gt; + &lt;Gather&gt; (loop)</div>
          <p><strong>Key:</strong> thread_id = call_sid — each call has its own MemorySaver checkpoint. Two concurrent calls never share state.</p>
        </div>
      </div>

      <div class="code-section" id="s2">
        <h2>Session 2 — State &amp; Graph Architecture</h2>
        <p class="sub"><span class="chip">core/graph/state.py</span> → <span class="chip">core/graph/graph.py</span></p>
        <div class="block">
          <h3>CNOState — all persisted fields</h3>
          <table class="tbl">
            <tr><th>Field</th><th>Type</th><th>Purpose</th></tr>
            <tr><td>messages</td><td>list</td><td>Turn history. add_messages reducer = append-only. Pass only new messages each turn.</td></tr>
            <tr><td>call_sid</td><td>str</td><td>= MemorySaver thread_id. Isolates each call.</td></tr>
            <tr><td>authenticated</td><td>bool</td><td>True after auth_step="complete"</td></tr>
            <tr><td>auth_step</td><td>str</td><td>State machine: collecting_phone → collecting_dob → confirming_dob → complete|failed</td></tr>
            <tr><td>auth_attempts</td><td>int</td><td>Failed attempts counter. Max 3 → escalate</td></tr>
            <tr><td>pii_collected</td><td>dict</td><td>{phoneNumber, policyNumber, dateOfBirth}</td></tr>
            <tr><td>customer</td><td>dict</td><td>Post-auth: firstName, lastName, policyNumber, partyKey</td></tr>
            <tr><td>access_token</td><td>str</td><td>Bearer token for all downstream API calls</td></tr>
            <tr><td>current_intent</td><td>str</td><td>Last intent classified by router</td></tr>
            <tr><td>active_flow</td><td>str</td><td>"" = open routing. "contact" etc = locked to that node.</td></tr>
            <tr><td>tts_text</td><td>str</td><td>Text to speak to caller this turn</td></tr>
            <tr><td>transfer_to</td><td>str</td><td>Phone number for &lt;Dial&gt; on escalation</td></tr>
            <tr><td>otp_data</td><td>dict</td><td>Multi-step flow state: contact_step, new_address, payment_type, etc.</td></tr>
          </table>
        </div>
        <div class="block">
          <h3>graph.py — routing logic</h3>
          <div class="box"><span class="kw">def</span> <span class="fn">_route_after_auth</span>(state):
    <span class="kw">if</span> auth_step == <span class="str">"complete"</span>:
        intent = state.get(<span class="str">"current_intent"</span>)
        <span class="kw">if</span> intent <span class="kw">in</span> (<span class="str">"auth"</span>, <span class="str">""</span>): <span class="kw">return</span> END      <span class="cmt"># no pre-auth intent</span>
        <span class="kw">return</span> <span class="str">"router"</span>                              <span class="cmt"># re-route immediately</span>
    <span class="kw">if</span> auth_step == <span class="str">"failed"</span>: <span class="kw">return</span> <span class="str">"escalation"</span>
    <span class="kw">return</span> END                                     <span class="cmt"># mid-auth, wait for next turn</span></div>
          <p>If caller says "check policy status" before auth, that intent is captured. Auth runs. On completion, graph immediately routes to policy — no extra round-trip.</p>
        </div>
      </div>

      <div class="code-section" id="s3">
        <h2>Session 3 — Router Node</h2>
        <p class="sub"><span class="chip">core/graph/nodes/router.py</span></p>
        <div class="block">
          <h3>Three-phase classification</h3>
          <div class="flow">1. ESCALATION CHECK (keyword scan — no LLM cost)
   "agent", "representative", "human", "transfer" → intent = "escalate"

2. FLOW LOCK CHECK
   active_flow="auth"|"otp" → mode="locked"  → force back to active flow
   active_flow="contact" etc → mode="escalate_only" → LLM but can only escalate
   active_flow=""            → mode="open"   → full free routing

3. LLM CLASSIFICATION (Groq llama-3.3-70b, temp=0)
   → one word from VALID_INTENTS
   → unknown → "faq"</div>
        </div>
        <div class="block">
          <h3>Context-switch modes</h3>
          <table class="tbl">
            <tr><th>Mode</th><th>When</th><th>Behaviour</th></tr>
            <tr><td>locked</td><td>auth, otp</td><td>No LLM. Forces back to active flow. Only escalation exits.</td></tr>
            <tr><td>escalate_only</td><td>contact, document, etc.</td><td>LLM runs. Non-escalation intent overridden back to active flow.</td></tr>
            <tr><td>open</td><td>active_flow=""</td><td>Full free routing. Caller can pivot to any intent.</td></tr>
          </table>
        </div>
      </div>

      <div class="code-section" id="s4">
        <h2>Session 4 — Auth Node</h2>
        <p class="sub"><span class="chip">core/graph/nodes/auth.py</span> → <span class="chip">core/tools/party_search.py</span></p>
        <div class="block">
          <h3>Auth state machine</h3>
          <div class="flow">collecting_phone → normalize_phone() → party_search(phone)
  found     → collecting_dob
  not found → confirming_phone → yes → collecting_policy
                                 no  → collecting_phone

collecting_dob → normalize_dob() → confirming_dob
  yes → check_auth_success(phone + DOB)
        MATCH   → complete → acquire_access_token()
        NOMATCH → collecting_name
  no  → collecting_dob

collecting_name → LLM extract_name() → check_auth_success(phone + name)
  MATCH   → complete
  NOMATCH → failed (attempts &gt;= 3 → escalate)</div>
        </div>
        <div class="block">
          <h3>Test callers</h3>
          <!-- ISSUE-2-003 fix: corrected Policy column from PKY (party-key format) to
               real P300-format policy numbers that the mock API / party_search.py accepts.
               Also corrected DOBs to match mock_cno_api.py PARTIES data. -->
          <table class="tbl">
            <tr><th>Name</th><th>Phone</th><th>DOB</th><th>Policy</th></tr>
            <tr><td>John Smith</td><td>5551234567</td><td>Jul 15 1965</td><td>P300123456</td></tr>
            <tr><td>Mary Johnson</td><td>5559876543</td><td>Mar 22 1950</td><td>P300654321</td></tr>
            <tr><td>Robert Williams</td><td>5553334444</td><td>Nov 8 1945</td><td>P300111222, P300333444</td></tr>
            <tr><td>Test User</td><td>5550000000</td><td>Jan 1 2000</td><td>P300000001</td></tr>
          </table>
        </div>
      </div>

      <div class="code-section" id="s5">
        <h2>Session 5 — Service Nodes</h2>
        <p class="sub"><span class="chip">core/graph/nodes/</span> — policy, payment, loan, beneficiary, contact, document, privacy, faq, escalation, goodbye</p>
        <div class="block">
          <h3>One-shot nodes (policy, payment, loan, beneficiary)</h3>
          <ul>
            <li>Called once, fetch data from CNO API, use LLM to format natural response</li>
            <li>Return <code>active_flow=""</code> so router is free next turn</li>
            <li>payment.py appends mandatory disclosure: "Please allow 24-48 hours for payment to post..."</li>
          </ul>
        </div>
        <div class="block">
          <h3>Multi-step flows (contact, document)</h3>
          <ul>
            <li>Use <code>otp_data</code> dict to track sub-steps: contact_step, new_address, new_phone etc.</li>
            <li>Return <code>active_flow="contact"</code> to lock router until complete or cancelled</li>
            <li>Cancel detection at every step: "no", "cancel", "never mind", "don't want" → _exit_response()</li>
            <li>contact.py uses word-boundary regex <code>r'\b(address|street|zip)\b'</code> so "I don't want to update the contact address" doesn't trigger collecting_address</li>
          </ul>
        </div>
        <div class="block">
          <h3>Termination nodes</h3>
          <ul>
            <li><strong>escalation</strong> — sets transfer_to=agent_phone. twilio_voice.py sees transfer_to → &lt;Dial&gt;</li>
            <li><strong>goodbye</strong> — twilio_voice.py sees current_node=="goodbye" → &lt;Say&gt; + &lt;Hangup/&gt;</li>
          </ul>
        </div>
      </div>

      <div class="code-section" id="s6">
        <h2>Session 6 — Infrastructure</h2>
        <p class="sub"><span class="chip">log_bus.py</span> · <span class="chip">services/session.py</span> · <span class="chip">services/conversation_store.py</span></p>
        <div class="block">
          <h3>Live log pipeline</h3>
          <div class="flow">structlog.info() → sys.stdout → _Tee.write()
  ├── sys.__stdout__  (terminal)
  ├── ivr.log file    (disk)
  └── log_bus.push()
       ├── _buffer deque(maxlen=500)  (history for new SSE clients)
       └── _queues list               (asyncio.Queue per active SSE connection)
            └── GET /client/logs/stream → EventSource → Live Logs tab</div>
        </div>
        <div class="block">
          <h3>conversation_store.py</h3>
          <ul>
            <li>In-memory dict for call history (_calls) and webchat sessions (_chats)</li>
            <li>start_call(sid, from) / add_call_turn(sid, role, text, intent, node) / end_call(sid)</li>
            <li>ensure_chat(sid) / add_chat_turn(sid, role, text) / get_chats() / get_calls()</li>
            <li>Resets on server restart (no persistence — use for monitoring only)</li>
          </ul>
        </div>
        <div class="block">
          <h3>WebChat API (webhooks/chat.py)</h3>
          <ul>
            <li>POST /chat/message — same LangGraph, thread_id="chat_{session_id}"</li>
            <li>GET /chat/history/{sid} — returns full turn list</li>
            <li>GET /chat/sessions — list all active sessions</li>
            <li>POST /chat/reset/{sid} — clear session + LangGraph checkpoint</li>
          </ul>
        </div>
      </div>

    </div><!-- end code-body -->
  </div><!-- end code-wrap -->
</div>

<!-- GRAPH -->
<div class="pane" id="pane-graph">
  <div class="graph-wrap">
    <h2>LangGraph — IVR Flow</h2>
    <div class="gdiag">[START]
   |
   v
+--------+
| router | &lt;------------------------------------------+
+---+----+                                            |
    |                                                 |
    +-- "auth"       --&gt; +------+                     |
    |                    | auth |                     |
    |                    +--+---+                     |
    |          complete+intent  ----------------------+ (immediate re-route)
    |          complete only    --&gt; [END]
    |          failed           --&gt; [escalation]
    |          mid-flow         --&gt; [END]  (wait next turn)
    |
    +-- "policy"      --&gt; [policy]      --&gt; [END]
    +-- "payment"     --&gt; [payment]     --&gt; [END]
    +-- "loan"        --&gt; [loan]        --&gt; [END]
    +-- "beneficiary" --&gt; [beneficiary] --&gt; [END]
    +-- "contact"     --&gt; [contact]     --&gt; [END] (active_flow=contact until done)
    +-- "document"    --&gt; [document]    --&gt; [END]
    +-- "privacy"     --&gt; [privacy]     --&gt; [END]
    +-- "faq"         --&gt; [faq]         --&gt; [END]
    +-- "otp"         --&gt; [otp]         --&gt; [END]
    +-- "goodbye"     --&gt; [goodbye]     --&gt; [END] --&gt; &lt;Hangup/&gt;
    +-- "escalate"    --&gt; [escalation]  --&gt; [END] --&gt; &lt;Dial agent&gt;</div>
    <div class="state-ref">
      <h3>CNOState Fields</h3>
      <div class="sg">
        <h4>Conversation</h4>
        <div class="sr"><span class="sk">messages</span><span class="st">list</span><span class="sd">Turn history. add_messages reducer (append-only)</span></div>
        <div class="sr"><span class="sk">call_sid</span><span class="st">str</span><span class="sd">= MemorySaver thread_id</span></div>
        <div class="sr"><span class="sk">tts_text</span><span class="st">str</span><span class="sd">Text spoken to caller via &lt;Say&gt;</span></div>
        <div class="sr"><span class="sk">transfer_to</span><span class="st">str</span><span class="sd">Phone for &lt;Dial&gt; on escalation</span></div>
      </div>
      <div class="sg">
        <h4>Authentication</h4>
        <div class="sr"><span class="sk">authenticated</span><span class="st">bool</span><span class="sd">True after auth complete</span></div>
        <div class="sr"><span class="sk">auth_step</span><span class="st">str</span><span class="sd">collecting_phone → dob → complete|failed</span></div>
        <div class="sr"><span class="sk">auth_attempts</span><span class="st">int</span><span class="sd">Max 3 → escalate</span></div>
        <div class="sr"><span class="sk">pii_collected</span><span class="st">dict</span><span class="sd">{phoneNumber, policyNumber, dateOfBirth}</span></div>
      </div>
      <div class="sg">
        <h4>Customer (post-auth)</h4>
        <div class="sr"><span class="sk">customer</span><span class="st">dict</span><span class="sd">firstName, lastName, policyNumber, partyKey</span></div>
        <div class="sr"><span class="sk">access_token</span><span class="st">str</span><span class="sd">Bearer token for all API calls</span></div>
      </div>
      <div class="sg">
        <h4>Flow Control</h4>
        <div class="sr"><span class="sk">current_intent</span><span class="st">str</span><span class="sd">Last classified intent</span></div>
        <div class="sr"><span class="sk">active_flow</span><span class="st">str</span><span class="sd">"" = open routing; node name = locked</span></div>
        <div class="sr"><span class="sk">current_node</span><span class="st">str</span><span class="sd">Last node that ran</span></div>
        <div class="sr"><span class="sk">otp_data</span><span class="st">dict</span><span class="sd">contact_step, new_address, payment_type…</span></div>
      </div>
    </div>
  </div>
</div>

<!-- LOGS -->
<div class="pane" id="pane-logs">
  <div class="logs-wrap">
    <div class="logs-toolbar">
      <div class="ldot" id="ldot"></div>
      <h3>Live Logs</h3>
      <span class="lcnt" id="lcnt">0 lines</span>
      <button class="lbtn on" id="ascroll-btn" onclick="toggleScroll()">Auto-scroll ON</button>
      <button class="lbtn" onclick="document.getElementById('logbox').innerHTML='';lc=0;updateLc()">Clear</button>
    </div>
    <div id="logbox"></div>
  </div>
</div>

<!-- ANALYTICS -->
<div class="pane" id="pane-analytics">
  <div style="padding:24px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
      <h2 style="margin:0;color:#e2e8f0">Call Analytics & Issue Detection</h2>
      <button onclick="loadAnalytics()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:5px 14px;font-size:12px;cursor:pointer">Refresh</button>
    </div>

    <!-- Summary cards -->
    <div id="analytics-summary" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px"></div>

    <!-- Two-column layout for distributions -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">
      <div>
        <h3 style="color:#94a3b8;margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:1px">Intent Distribution</h3>
        <div id="analytics-intents" style="background:#1e293b;border-radius:8px;padding:16px"></div>
      </div>
      <div>
        <h3 style="color:#94a3b8;margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:1px">Node Usage</h3>
        <div id="analytics-nodes" style="background:#1e293b;border-radius:8px;padding:16px"></div>
      </div>
    </div>

    <!-- Issues table -->
    <h3 style="color:#94a3b8;margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:1px">Detected Issues</h3>
    <div id="analytics-issues" style="background:#1e293b;border-radius:8px;overflow:hidden">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#334155;color:#94a3b8;text-align:left">
            <th style="padding:8px 12px">Type</th>
            <th style="padding:8px 12px">Call SID</th>
            <th style="padding:8px 12px">Detail</th>
          </tr>
        </thead>
        <tbody id="analytics-issues-body"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- CONFIG -->
<div class="pane" id="pane-cfg">
  <div class="cfg-wrap">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
      <h2 style="margin:0">IVR Configuration</h2>
      <button onclick="loadConfig()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:5px 14px;font-size:12px;cursor:pointer">↻ Refresh</button>
    </div>
    <div id="cfg-body"><div class="empty-msg" style="padding:40px">Click the tab to load...</div></div>
  </div>
</div>

</div><!-- end body -->

<script>
// ── Tab switching ─────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    document.querySelectorAll('.pane').forEach(x => x.classList.remove('on'));
    t.classList.add('on');
    document.getElementById('pane-' + t.dataset.pane).classList.add('on');
    if (t.dataset.pane === 'calls') startCallsPoll(); else stopCallsPoll();
  });
});

// ── Code nav ──────────────────────────────────────────────────
document.querySelectorAll('.code-nav-item').forEach(n => {
  n.addEventListener('click', () => {
    document.querySelectorAll('.code-nav-item').forEach(x => x.classList.remove('on'));
    document.querySelectorAll('.code-section').forEach(x => x.classList.remove('on'));
    n.classList.add('on');
    document.getElementById(n.dataset.s).classList.add('on');
  });
});

// ── WebChat ───────────────────────────────────────────────────
let sid = null;
const sessions = {};

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>'); }

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('copy-ok');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copy-ok'); }, 1500);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('copy-ok');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copy-ok'); }, 1500);
  });
}

function newChat() {
  sid = 'chat_' + Date.now();
  sessions[sid] = [];
  document.getElementById('chat-sid').textContent = sid.slice(-10);
  document.getElementById('msgs').innerHTML = '';
  document.getElementById('btn-send').disabled = false;
  document.getElementById('inp').focus();
  updateSidebar();
  addMsg('b', "Hi! I'm your virtual insurance assistant. I can help with policy status, payments, beneficiaries, loans, and more. Please provide your 10-digit phone number to get started.", '', '');
}

function updateSidebar() {
  const el = document.getElementById('sess-list');
  const keys = Object.keys(sessions).reverse();
  if (!keys.length) { el.innerHTML = ''; return; }
  el.innerHTML = keys.map(k => {
    const turns = sessions[k];
    const last = turns.filter(t => t.r === 'u').pop();
    return '<div class="sess-item' + (k===sid?' on':'') + '" onclick="loadSess(\\''+k+'\\')"><div class="sid">'
      + k.slice(-10) + '</div><div class="prev">' + esc(last ? last.t.slice(0,40) : 'New chat') + '</div></div>';
  }).join('');
}

async function loadSess(id) {
  sid = id;
  document.getElementById('chat-sid').textContent = id.slice(-10);
  document.getElementById('btn-send').disabled = false;
  const box = document.getElementById('msgs');
  box.innerHTML = '';
  if (!sessions[id] || !sessions[id].length) {
    try {
      const res = await fetch('/chat/history/' + id);
      const data = await res.json();
      sessions[id] = (data.turns || []).map(t => ({r: t.role==='human'?'u':'b', t: t.text, intent: t.intent||'', node: t.node||''}));
    } catch(e) { sessions[id] = []; }
  }
  (sessions[id]||[]).forEach(m => addMsg(m.r, m.t, m.intent, m.node, true));
  box.scrollTop = box.scrollHeight;
  updateSidebar();
}

async function initChats() {
  try {
    const res = await fetch('/chat/sessions');
    const data = await res.json();
    const slist = data.sessions || [];
    if (!slist.length) { newChat(); return; }
    slist.forEach(s => { if (!sessions[s.session_id]) sessions[s.session_id] = []; });
    const last = slist[0].session_id;
    await loadSess(last);
    updateSidebar();
  } catch(e) { newChat(); }
}

function addMsg(role, text, intent, node, noScroll) {
  const box = document.getElementById('msgs');
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  const itag = intent && intent!='auth' ? '<span class="tag">'+esc(intent)+'</span>' : '';
  d.innerHTML = '<div class="av">'+(role==='b'?'B':'U')+'</div>'
    + '<div><div class="bubble">'+esc(text)+'</div>'
    + '<div class="mm">'+new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})+itag+'</div></div>';
  box.appendChild(d);
  if (!noScroll) box.scrollTop = box.scrollHeight;
}

function addTyping() {
  const box = document.getElementById('msgs');
  const d = document.createElement('div');
  d.className='msg b'; d.id='typing';
  d.innerHTML='<div class="av">B</div><div class="bubble typing"><span></span><span></span><span></span></div>';
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
}

async function send() {
  const inp = document.getElementById('inp');
  const text = inp.value.trim();
  if (!text || !sid) return;
  inp.value = ''; inp.style.height='auto';
  document.getElementById('btn-send').disabled = true;
  if (!sessions[sid]) sessions[sid] = [];
  sessions[sid].push({r:'u', t:text, intent:'', node:''});
  addMsg('u', text);
  addTyping();
  updateSidebar();
  try {
    const res = await fetch('/chat/message', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sid, text})
    });
    const data = await res.json();
    const el = document.getElementById('typing');
    if (el) el.remove();
    const reply = data.response || 'Sorry, something went wrong.';
    sessions[sid].push({r:'b', t:reply, intent:data.intent||'', node:data.node||''});
    addMsg('b', reply, data.intent, data.node);
    updateSidebar();
  } catch(e) {
    const el = document.getElementById('typing');
    if (el) el.remove();
    addMsg('b', 'Connection error: ' + e.message);
  }
  document.getElementById('btn-send').disabled = false;
  inp.focus();
}

document.getElementById('inp').addEventListener('keydown', e => {
  if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

// ── Calls ─────────────────────────────────────────────────────
let selCall = null;
let _callsPollTimer = null;

function startCallsPoll() {
  if (_callsPollTimer) return;
  loadCalls();
  _callsPollTimer = setInterval(loadCalls, 3000);
}
function stopCallsPoll() {
  if (_callsPollTimer) { clearInterval(_callsPollTimer); _callsPollTimer = null; }
}

async function loadCalls() {
  try {
    const res = await fetch('/dashboard/calls');
    const data = await res.json();
    const calls = data.calls || [];
    document.getElementById('call-cnt').textContent = calls.length;
    const el = document.getElementById('call-list');
    if (!calls.length) {
      el.innerHTML = '<div class="empty-msg">No calls recorded yet.<br>Make a call via the Softphone.</div>';
      return;
    }
    el.innerHTML = calls.map(c => {
      const from = (c.from_number||'').replace('client:','').replace('+1','');
      const active = c.status === 'active';
      const sel = c.call_sid === selCall ? ' on' : '';
      return '<div class="call-item'+sel+'" onclick="selCallFn(\\''+c.call_sid+'\\')"><div class="csid">'+c.call_sid.slice(0,24)+'</div>'
        + '<div class="cfrom">'+(from||'Softphone')+'</div>'
        + '<div class="cmeta"><span class="dot '+(active?'active':'ended')+'"></span>'+(active?'Active':'Ended')
        + ' &nbsp; '+c.turns.length+' turns &nbsp; '+c.started_at.slice(11,16)+'</div></div>';
    }).join('');
    // Auto-refresh selected call detail during polling
    if (selCall) refreshCallDetail(selCall);
  } catch(e) { console.error('loadCalls error', e); }
}

function renderStatePanel(call) {
  const na = v => v ? `<span class="csp-val">${esc(v)}</span>` : '<span class="csp-val na">—</span>';
  const bool = v => v ? '<span class="csp-val ok">YES</span>' : '<span class="csp-val warn">NO</span>';
  const row = (label, valHtml) => `<div class="csp-row"><span class="csp-label">${label}</span>${valHtml}</div>`;

  // ── Auth card ────────────────────────────────────────────────────────────
  const authCard = `<div class="csp-card"><h4>🔑 Authentication</h4>
    ${row('Authenticated',   bool(call.authenticated))}
    ${row('Auth Step',       na(call.auth_step))}
    ${row('Auth Attempts',   na(String(call.auth_attempts ?? '—')))}
    ${row('Caller Name',     na(call.caller_name))}
    ${row('Caller Persona',  call.caller_persona
        ? `<span class="csp-val ok">${esc(call.caller_persona)}</span>`
        : '<span class="csp-val na">—</span>')}
  </div>`;

  // ── PII card ─────────────────────────────────────────────────────────────
  const pii = call.pii_captured || {};
  const piiRows = Object.entries(pii).map(([field, info]) => {
    const val = info.raw && info.raw !== '—' ? info.raw : null;
    return `<div class="csp-row">
      <span class="csp-label" title="${esc(info.var||field)}">${esc(field)}</span>
      <span class="csp-val ${val?'ok':'na'}" style="font-size:11px">${val ? esc(val) : '—'}</span>
    </div>`;
  }).join('');
  const piiCard = `<div class="csp-card"><h4>🔒 PII Captured</h4>
    ${piiRows || '<div class="csp-row"><span class="csp-label na">None yet</span></div>'}
    <div style="margin-top:6px;font-size:10px;color:#475569">Hover field name for variable path</div>
  </div>`;

  // ── Flow card ────────────────────────────────────────────────────────────
  const flowCard = `<div class="csp-card"><h4>💬 Flow State</h4>
    ${row('Current Intent',  na(call.current_intent))}
    ${row('Active Flow',     na(call.active_flow) )}
    ${row('Current Node',    na(call.current_node))}
  </div>`;

  // ── Intents + Model card ─────────────────────────────────────────────────
  const history = call.intent_history || [];
  const curIntent = call.current_intent || '';
  const badges = history.length
    ? history.map(i => `<span class="intent-badge ${i===curIntent?'active':''}">${esc(i)}</span>`).join('')
    : '<span class="csp-val na">None yet</span>';
  const mi = call.model_info || {};
  const intentModelCard = `<div class="csp-card"><h4>📋 Intents + Model</h4>
    <div style="margin-bottom:8px">${badges}</div>
    ${row('LLM Model', na(mi.llm_model))}
    ${row('Auth Mode', na(mi.auth_mode))}
  </div>`;

  document.getElementById('call-state-panel').innerHTML = authCard + piiCard + flowCard + intentModelCard;
}

async function refreshCallDetail(csid) {
  try {
    const res = await fetch('/dashboard/calls/' + csid);
    const call = await res.json();
    if (!call.call_sid) return;
    const from = (call.from_number||'').replace('client:','');
    const status = call.status === 'active'
      ? '<span style="color:#22c55e;font-size:11px;margin-left:8px">● Live</span>'
      : '<span style="color:#64748b;font-size:11px;margin-left:8px">Ended ' + (call.ended_at||'').slice(11,19) + '</span>';
    const recBadge = call.recording_url
      ? ' <a href="'+call.recording_url+'" target="_blank" style="font-size:11px;color:#22c55e;margin-left:8px">▶ Recording</a>'
      : '';
    document.getElementById('call-hdr').innerHTML =
      esc(from || 'Softphone') + ' — ' + (call.started_at||'').slice(0,19) + status + recBadge;

    // Share button — opens standalone call URL
    const shareBtn = '<a href="/dashboard/call/'+csid+'" target="_blank" style="font-size:11px;color:#a78bfa;margin-left:10px;text-decoration:none;border:1px solid rgba(167,139,250,.3);padding:2px 8px;border-radius:4px">&#x2197; Share</a>';
    document.getElementById('call-hdr').innerHTML += shareBtn;

    renderStatePanel(call);
    renderEventTimeline(csid);

    const el = document.getElementById('call-turns');
    if (!call.turns || !call.turns.length) {
      el.innerHTML = '<div class="empty-msg">No turns recorded</div>'; return;
    }
    el.innerHTML = call.turns.map(t => {
      const redacted = t.role === 'human' && t.text.includes('REDACTED');
      const redBadge = redacted ? '<span class="tk" style="background:rgba(251,146,60,.15);color:#fb923c">PII redacted</span>' : '';
      return '<div class="turn '+t.role+'"><span class="role">'+(t.role==='human'?'YOU':'BOT')+'</span>'
        +'<div class="turn-body"><div class="turn-text">'+esc(t.text)+'</div>'
        +'<div class="turn-meta"><span>'+t.ts.slice(11,19)+'</span>'
        +(t.intent?'<span class="tk intent">'+esc(t.intent)+'</span>':'')
        +(t.node?'<span class="tk node">'+esc(t.node)+'</span>':'')
        +redBadge
        +'</div></div></div>';
    }).join('');
    el.scrollTop = el.scrollHeight;

    // ── Inject copy buttons ───────────────────────────────────────────────
    // Copy transcript
    const turnsEl = document.getElementById('call-turns');
    if (turnsEl) {
      turnsEl.style.position = 'relative';
      const oldBtn = turnsEl.querySelector('.copy-btn');
      if (oldBtn) oldBtn.remove();
      const tBtn = document.createElement('button');
      tBtn.className = 'copy-btn'; tBtn.textContent = '⎘ Copy Transcript';
      tBtn.onclick = () => {
        const lines = (call.turns || []).map(t => (t.role==='human'?'USER: ':'BOT:  ') + t.text).join('\\n');
        copyToClipboard(lines, tBtn);
      };
      turnsEl.insertBefore(tBtn, turnsEl.firstChild);
    }
    // Copy call-state-panel
    const stateEl = document.getElementById('call-state-panel');
    if (stateEl) {
      stateEl.style.position = 'relative';
      const oldBtn2 = stateEl.querySelector('.copy-btn');
      if (oldBtn2) oldBtn2.remove();
      const sBtn = document.createElement('button');
      sBtn.className = 'copy-btn'; sBtn.textContent = '⎘ Copy State';
      sBtn.onclick = () => copyToClipboard(stateEl.innerText, sBtn);
      stateEl.insertBefore(sBtn, stateEl.firstChild);
    }
    // Copy session ID in header
    const hdrEl = document.getElementById('call-hdr');
    if (hdrEl && !hdrEl.querySelector('.copy-btn')) {
      const idBtn = document.createElement('button');
      idBtn.className = 'copy-btn'; idBtn.textContent = '⎘ SID';
      idBtn.style.cssText = 'position:static;margin-left:10px;';
      idBtn.onclick = () => copyToClipboard(csid, idBtn);
      hdrEl.appendChild(idBtn);
    }
  } catch(e) { console.error('refreshCallDetail error', e); }
}

async function selCallFn(csid) {
  selCall = csid;
  // Re-render list to update selection highlight, then load detail
  const el = document.getElementById('call-list');
  el.querySelectorAll('.call-item').forEach(i => {
    i.classList.toggle('on', i.getAttribute('onclick').includes(csid));
  });
  await refreshCallDetail(csid);
}

async function renderEventTimeline(csid) {
  const wrap = document.getElementById('event-timeline');
  const box  = document.getElementById('ev-rows');
  if (!wrap || !box) return;
  wrap.style.display = 'block';
  box.innerHTML = '<div style="color:#475569;font-size:11px;font-family:monospace;padding:4px 0">Loading events…</div>';
  try {
    const res  = await fetch('/dashboard/calls/' + csid + '/events');
    if (!res.ok) { box.innerHTML = '<div style="color:#ef4444;font-size:11px">Events API error: ' + res.status + '</div>'; return; }
    const data = await res.json();
    const evs  = data.events || [];
    if (!evs.length) {
      box.innerHTML = '<div style="color:#475569;font-size:11px;font-family:monospace;padding:4px 0">No events recorded yet.</div>';
      return;
    }
    box.innerHTML = evs.map(ev => {
      const ts_raw = typeof ev.ts === 'number' ? ev.ts : parseFloat(ev.ts || 0);
      const d   = new Date(ts_raw * 1000);
      const hms = d.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
      const ms  = String(d.getMilliseconds()).padStart(3,'0');
      const ts  = hms + '.' + ms;
      const extra = Object.entries(ev)
        .filter(([k]) => !['ts','ts_str','event_type'].includes(k))
        .map(([k,v]) => { try { return k + '=' + String(v).slice(0,40); } catch(_){ return k + '=?'; } })
        .join('  ');
      const cls = 'ev-' + (ev.event_type || 'unknown').replace(/[^a-z0-9_]/gi,'_').toLowerCase();
      return '<div class="ev-row"><span class="ev-ts">'+ts+'</span>'
        + '<span class="ev-type '+cls+'">'+esc(ev.event_type||'')+'</span>'
        + '<span class="ev-data">'+esc(extra)+'</span></div>';
    }).join('');
    box.scrollTop = box.scrollHeight;

    // Copy events button
    const oldEvBtn = wrap.querySelector('.copy-btn');
    if (oldEvBtn) oldEvBtn.remove();
    const evBtn = document.createElement('button');
    evBtn.className = 'copy-btn'; evBtn.textContent = '⎘ Copy Events';
    evBtn.onclick = () => {
      const txt = evs.map(ev => {
        const d = new Date((typeof ev.ts==='number'?ev.ts:parseFloat(ev.ts||0))*1000);
        const ts = d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const extra = Object.entries(ev).filter(([k])=>!['ts','ts_str','event_type'].includes(k)).map(([k,v])=>k+'='+String(v).slice(0,40)).join(' ');
        return '['+ts+'] '+ev.event_type+' '+extra;
      }).join('\\n');
      copyToClipboard(txt, evBtn);
    };
    wrap.style.position = 'relative';
    wrap.insertBefore(evBtn, wrap.firstChild);
  } catch(e) {
    box.innerHTML = '<div style="color:#ef4444;font-size:11px;font-family:monospace">Event fetch error: ' + esc(String(e)) + '</div>';
    console.error('renderEventTimeline error:', e);
  }
}

setInterval(() => { if (selCall) selCallFn(selCall); }, 5000);

// ── Live Logs ─────────────────────────────────────────────────
let autoScroll = true, lc = 0, es = null;

function updateLc() { document.getElementById('lcnt').textContent = lc + ' lines'; }

function toggleScroll() {
  autoScroll = !autoScroll;
  const b = document.getElementById('ascroll-btn');
  b.textContent = 'Auto-scroll ' + (autoScroll ? 'ON' : 'OFF');
  b.className = 'lbtn' + (autoScroll ? ' on' : '');
}

function classify(line) {
  if (line.includes('speech_received'))                                return 'sp';
  if (line.includes('call_started')||line.includes('call_ended'))      return 'ca';
  if (line.includes('auth')||line.includes('access_token'))            return 'au';
  if (line.includes('graph_')||line.includes('node_'))                 return 'gr';
  if (line.includes('ERROR')||line.includes('[error')||line.includes('Traceback')) return 'er';
  if (line.includes('WARNING')||line.includes('[warning'))             return 'wa';
  if (line.includes('HTTP/1.1 2'))                                     return 'ht';
  if (line.includes('[info')||line.includes('cno_ivr'))                return 'ok';
  return 'in';
}

function connectLogs() {
  if (es) es.close();
  const dot = document.getElementById('ldot');
  es = new EventSource('/client/logs/stream');
  es.onopen = () => { dot.className = 'ldot'; };
  es.onmessage = e => {
    if (!e.data || !e.data.trim()) return;
    const box = document.getElementById('logbox');
    const span = document.createElement('span');
    span.className = 'll ' + classify(e.data);
    span.textContent = e.data;
    box.appendChild(span);
    lc++; updateLc();
    if (autoScroll) box.scrollTop = box.scrollHeight;
  };
  es.onerror = () => {
    dot.className = 'ldot err';
    setTimeout(connectLogs, 3000);
  };
}

// ── Softphone ─────────────────────────────────────────────────
let phDevice=null, phActiveCall=null, phTimer=null, phSec=0, phMuted=false, phCalling=false;

function phLog(msg, col) {
  const d=document.getElementById('ph-log');
  if(!d) return;
  const p=document.createElement('p');
  p.style.cssText='margin-bottom:3px;color:'+(col||'#94a3b8');
  const t=new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  p.textContent=t+'  '+msg;
  d.prepend(p);
}
function phSetStatus(txt, col) {
  const s=document.getElementById('ph-status'), dt=document.getElementById('ph-dot');
  if(s) s.textContent=txt;
  if(dt) dt.style.background=col;
}
function phSetBtn(id, enabled) {
  const b=document.getElementById(id);
  if(!b) return;
  b.disabled=!enabled;
  b.style.opacity=enabled?'1':'.35';
  b.style.cursor=enabled?'pointer':'not-allowed';
}
function phStartTimer() {
  phSec=0;
  const el=document.getElementById('ph-timer');
  if(el) el.style.display='block';
  phTimer=setInterval(()=>{ phSec++;const m=Math.floor(phSec/60),s=phSec%60;
    const el=document.getElementById('ph-timer'); if(el) el.textContent=m+':'+String(s).padStart(2,'0'); },1000);
}
function phStopTimer() {
  clearInterval(phTimer);
  const el=document.getElementById('ph-timer'); if(el) el.style.display='none';
}
async function phInit() {
  try {
    phLog('Fetching access token...');
    const res=await fetch('/client/token');
    if(!res.ok) { phLog('Token request failed ('+res.status+')', '#ef4444'); phSetStatus('Token error','#ef4444'); return; }
    const {token}=await res.json();
    phDevice=new Twilio.Device(token,{logLevel:1,codecPreferences:['opus','pcmu']});
    phDevice.on('registered', ()=>{ phSetStatus('Ready — click Call IVR','#22c55e'); phSetBtn('ph-btn-call',true); phLog('Device registered','#22c55e'); });
    phDevice.on('unregistered', ()=>{ phLog('Unregistered — reconnecting...','#f59e0b'); setTimeout(()=>phDevice.register(),2000); });
    phDevice.on('error', e=>{ phSetStatus('Error: '+e.message,'#ef4444'); phLog('Device error: '+e.message,'#ef4444');
      if(e.code===20101||e.code===31009||e.code===31005) { phLog('Attempting re-register...','#f59e0b'); setTimeout(()=>{ try{phDevice.register();}catch(_){} },3000); }
    });
    phDevice.on('tokenWillExpire', async()=>{ const r=await fetch('/client/token'); const{token:t}=await r.json(); phDevice.updateToken(t); phLog('Token refreshed','#22c55e'); });
    phDevice.register();
    phSetStatus('Registering...','#f59e0b');
  } catch(e) { phSetStatus('Init failed','#ef4444'); phLog('Init error: '+e.message,'#ef4444'); }
}
async function phCall() {
  if(phCalling||!phDevice) return;
  phCalling=true; phSetBtn('ph-btn-call',false);
  phSetStatus('Connecting...','#f59e0b'); phLog('Placing call to IVR...');
  try {
    const c=await phDevice.connect({params:{}});
    c.on('accept',()=>{ phSetStatus('Connected','#3b82f6'); phSetBtn('ph-btn-mute',true); phSetBtn('ph-btn-hang',true); phStartTimer(); phLog('Call connected','#22c55e'); });
    c.on('disconnect',()=>{ phCalling=false; phActiveCall=null; phSetStatus('Ready — click Call IVR','#22c55e');
      phSetBtn('ph-btn-call',true); phSetBtn('ph-btn-mute',false); phSetBtn('ph-btn-hang',false);
      phStopTimer(); phMuted=false;
      const mb=document.getElementById('ph-btn-mute'); if(mb){mb.textContent='Mute';mb.style.background='#334155';mb.style.color='#e2e8f0';}
      phLog('Call ended');
    });
    c.on('error',e=>{ phCalling=false; phLog('Call error: '+e.message,'#ef4444');
      if(e.message&&(e.message.includes('application error')||e.message.includes('31480'))) {
        document.getElementById('ph-ngrok-warn').style.display='block';
      }
    });
    phActiveCall=c;
  } catch(e) { phCalling=false; phSetStatus('Call failed','#ef4444'); phLog('Call failed: '+e.message,'#ef4444'); phSetBtn('ph-btn-call',true); }
}
function phMute() {
  if(!phActiveCall) return;
  phMuted=!phMuted; phActiveCall.mute(phMuted);
  const btn=document.getElementById('ph-btn-mute');
  if(btn){ btn.textContent=phMuted?'Unmute':'Mute'; btn.style.background=phMuted?'#f59e0b':'#334155'; btn.style.color=phMuted?'#000':'#e2e8f0'; }
  phLog(phMuted?'Muted':'Unmuted','#f59e0b');
}
function phHang() { if(phActiveCall) phActiveCall.disconnect(); }

// Load Twilio SDK and init softphone when Phone tab is first clicked
let phLoaded=false;
document.querySelectorAll('.tab').forEach(t=>{
  if(t.dataset.pane==='phone') t.addEventListener('click',()=>{
    if(phLoaded) return; phLoaded=true;
    if(window.Twilio&&window.Twilio.Device){ phInit(); return; }
    const s=document.createElement('script'); s.src='/client/twilio.min.js';
    s.onload=()=>phInit(); document.head.appendChild(s);
  });
});

// ── Config ────────────────────────────────────────────────────
const CFG_META = {
  // LLM
  GROQ_MODEL:              {desc:'LLM for all service nodes (policy, payment, loan, FAQ, beneficiary).',                       best:'llama-3.3-70b-versatile — fastest + most accurate on Groq for IVR tasks.'},
  ROUTER_MODEL:            {desc:'Fast LLM for single-word intent classification in the router node.',                         best:'llama-3.1-8b-instant — lightweight, sub-200ms classification.'},
  GROQ_API_KEY:            {desc:'Groq API key for LLM inference.',                                                           best:'Keep secret. Rotate every 90 days.'},
  OPENAI_EMBEDDING_MODEL:  {desc:'OpenAI embedding model for RAG vector search.',                                             best:'text-embedding-3-small — good accuracy/cost ratio for FAQ retrieval.'},
  // Realtime
  AUTH_MODE:               {desc:'"standard" uses Deepgram STT + LangGraph auth node. "realtime" uses OpenAI Realtime API.',  best:'"realtime" — lower latency, more natural auth conversation.'},
  OPENAI_API_KEY:          {desc:'OpenAI API key. Used for Realtime API auth and OpenAI TTS in the stream path.',             best:'Required for realtime mode and stream TTS. Keep secret.'},
  REALTIME_MODEL:          {desc:'OpenAI Realtime model (read-only — set in services/realtime_auth.py).',                    best:'gpt-realtime-1.5 — latest stable model.'},
  REALTIME_VOICE:          {desc:'Voice used by OpenAI Realtime during authentication (set in realtime_auth.py).',           best:'"alloy" — neutral US English. Options: alloy, ash, coral, echo, sage, shimmer, verse.'},
  REALTIME_URL:            {desc:'WebSocket URL for OpenAI Realtime API (read-only — set in realtime_auth.py).',             best:'wss://api.openai.com/v1/realtime?model=gpt-realtime-1.5'},
  // STT — Deepgram
  DEEPGRAM_API_KEY:        {desc:'Deepgram API key for STT. Used in both standard and realtime modes.',                      best:'Keep secret. Rotate every 90 days.'},
  DEEPGRAM_MODEL:          {desc:'Deepgram STT model. Determines transcription accuracy and latency.',                       best:'"nova-2" — best for US English IVR. "nova-3" available but more expensive.'},
  DEEPGRAM_LANGUAGE:       {desc:'BCP-47 language code for STT. Affects phoneme recognition.',                               best:'"en-US" for US English callers.'},
  DEEPGRAM_ENCODING:       {desc:'Audio encoding format. Must match what Twilio Media Streams sends.',                       best:'"mulaw" — Twilio native format. Do not change.'},
  DEEPGRAM_SAMPLE_RATE:    {desc:'Audio sample rate in Hz. Must match the Twilio stream output.',                            best:'8000 — standard telephony/PSTN rate. Do not change.'},
  DEEPGRAM_CHANNELS:       {desc:'Number of audio channels. Twilio sends mono.',                                             best:'1 — mono. Do not change.'},
  DEEPGRAM_ENDPOINTING:    {desc:'Milliseconds of silence before Deepgram finalizes an utterance.',                          best:'300ms — good IVR balance. Lower (200ms) = faster but may cut speech. Higher (500ms) = more patient.'},
  DEEPGRAM_UTTERANCE_END_MS:{desc:'Finalize utterance after N ms of no new words (safety net for endpointing).',            best:'1000ms — safety net so speech is always finalized.'},
  DEEPGRAM_SMART_FORMAT:   {desc:'Auto-formats dates, numbers, currencies in transcripts.',                                  best:'True — greatly improves policy number, DOB, and payment amount recognition.'},
  DEEPGRAM_INTERIM_RESULTS:{desc:'Stream partial transcripts before final. Enables barge-in detection.',                    best:'True — enables barge-in VAD to interrupt TTS playback.'},
  DEEPGRAM_PUNCTUATE:      {desc:'Add punctuation to transcripts.',                                                         best:'True — helps LLM understand sentence structure.'},
  DEEPGRAM_NO_DELAY:       {desc:'Disable Deepgram internal buffering for lowest latency.',                                  best:'True — critical for sub-second IVR response times.'},
  // TTS — OpenAI
  OPENAI_TTS_MODEL:        {desc:'OpenAI TTS model for the WebSocket stream path.',                                         best:'"tts-1" — lowest latency for real-time streaming. "tts-1-hd" = higher quality, ~2x latency.'},
  OPENAI_TTS_VOICE:        {desc:'OpenAI TTS voice for the stream path.',                                                   best:'"nova" — clear neutral female. Options: alloy, echo, fable, onyx, nova, shimmer.'},
  OPENAI_TTS_FORMAT:       {desc:'Audio pipeline (read-only). OpenAI outputs PCM 24kHz, resampled to mulaw 8kHz.',          best:'Hardcoded. Changing requires code edit in services/tts.py.'},
  WEBHOOK_TTS_VOICE:       {desc:'TwiML <Say> voice for the webhook (non-stream) path (read-only — set in twilio_voice.py).', best:'"Polly.Joanna" — Amazon Polly neural. Options: Polly.Matthew, Polly.Salli, Polly.Amy.'},
  // TTS — ElevenLabs
  ELEVENLABS_API_KEY:      {desc:'ElevenLabs API key for TTS synthesis.',                                                   best:'Keep secret. Rotate every 90 days.'},
  ELEVENLABS_VOICE_ID:     {desc:'ElevenLabs voice ID. Determines the caller-facing voice.',                                best:'21m00Tcm4TlvDq8ikWAM (Rachel). Clone a custom voice in ElevenLabs Voice Lab for branding.'},
  ELEVENLABS_MODEL:        {desc:'ElevenLabs TTS model. Affects quality and latency.',                                      best:'"eleven_turbo_v2" — lowest latency for IVR. "eleven_multilingual_v2" for multi-language.'},
  ELEVENLABS_STABILITY:    {desc:'Voice stability (0-1). Higher = more consistent, Lower = more expressive.',               best:'0.5 — balanced. Increase to 0.7+ for a professional IVR tone.'},
  ELEVENLABS_SIMILARITY_BOOST:{desc:'Voice similarity to reference (0-1). Helps cloned voices stay on-character.',         best:'0.75 — good for cloned voices. Set lower (0.5) for more natural variation.'},
  ELEVENLABS_OPTIMIZE_STREAMING_LATENCY:{desc:'Latency optimization level 0-4. Higher = lower latency, lower quality.',   best:'3 — best IVR latency with acceptable quality. Use 4 only if 3 still lags.'},
  // Twilio
  TWILIO_PHONE_NUMBER:     {desc:'The Twilio DID callers dial to reach the IVR.',                                           best:'Set in Twilio Console. Voice URL must point to /webhook/voice.'},
  TWILIO_AGENT_PHONE_NUMBER:{desc:'Phone number for live agent transfer during escalation.',                                best:'Your call centre queue or direct agent line.'},
  TWILIO_TWIML_APP_SID:   {desc:'TwiML App SID for browser softphone (SDK client calls).',                                 best:'Create in Twilio Console → Voice → TwiML Apps. Voice URL = ngrok_url/webhook/voice.'},
  TWILIO_ACCOUNT_SID:      {desc:'Twilio Account SID. Identifies your Twilio account.',                                    best:'Found in Twilio Console dashboard. Keep secret.'},
  TWILIO_AUTH_TOKEN:       {desc:'Twilio Auth Token for REST API authentication.',                                          best:'Keep secret. Rotate if compromised.'},
  TWILIO_API_KEY:          {desc:'Twilio API Sub-Key for generating browser client tokens.',                                best:'Create separate API Key in Console, not the main auth token.'},
  TWILIO_API_SECRET:       {desc:'Twilio API Secret paired with TWILIO_API_KEY for browser token generation.',             best:'Generated alongside the API Key in Twilio Console. Keep secret.'},
  // Backend
  CNO_API_BASE_URL:        {desc:'Base URL for the backend API (party search, holding, payments).',                         best:'http://localhost:8001 for dev mock. Use production URL in prod.'},
  CNO_API_KEY:             {desc:'API key for authenticating with the backend insurance API.',                              best:'Keep secret. Set in prod deployment.'},
  CNO_JWT_SECRET:          {desc:'JWT signing secret for backend API token verification.',                                  best:'Use a strong random string (32+ chars). Keep secret.'},
  // Feature Flags
  ENABLE_RAG:              {desc:'Enable pgvector RAG for FAQ answers. False = skip vector search, use canned fallback.',   best:'True in prod (requires pgvector). False for quick dev without Postgres.'},
  FAQ_FALLBACK_TO_ESCALATE:{desc:'When RAG finds no match: True = transfer to agent, False = canned "I can help with…".',  best:'False — avoids unnecessary agent transfers for edge-case questions.'},
  MAX_AUTH_ATTEMPTS:        {desc:'Max PII verification retries before escalating to a live agent.',                         best:'3 — gives callers enough tries without frustrating loops.'},
  // Security
  DASHBOARD_USERNAME:      {desc:'HTTP Basic username for dashboard and browser client access.',                            best:'"admin" for dev. Use a unique username in prod.'},
  DASHBOARD_PASSWORD:      {desc:'HTTP Basic password for dashboard access. Empty = no auth (dev only).',                   best:'Set a strong password in prod. Never leave empty in production.'},
  VALIDATE_TWILIO_SIGNATURE:{desc:'Validate X-Twilio-Signature on webhook requests to prevent spoofing.',                  best:'True in prod. Requires TWILIO_BASE_URL to be set.'},
  TWILIO_BASE_URL:         {desc:'Public base URL for Twilio signature validation (e.g. ngrok or prod domain).',            best:'https://your-domain.com — must match the URL Twilio uses to call your webhooks.'},
  WS_AUTH_TOKEN:           {desc:'Token for authenticating WebSocket /stream connections. Empty = skip (dev only).',         best:'Set a strong token in prod. Add as ?token= param in TwiML Media Stream URL.'},
  ALLOWED_ORIGINS:         {desc:'CORS allowed origins. Comma-separated list or "*" for all.',                              best:'"*" for dev. Restrict to your domain in prod.'},
  // Infra
  REDIS_URL:               {desc:'Redis URL for session state and LangGraph MemorySaver checkpointing.',                   best:'redis://localhost:6379/0 for dev. Redis Cloud or ElastiCache in prod.'},
  DATABASE_URL:            {desc:'PostgreSQL connection string for pgvector RAG knowledge base.',                           best:'Managed Postgres + pgvector in prod (Supabase, Neon, or RDS).'},
  ENVIRONMENT:             {desc:'Deployment environment tag. Controls logging and safety checks.',                         best:'"dev" locally. "prod" in production. Never run dev mode in prod.'},
  LOG_LEVEL:               {desc:'Logging verbosity.',                                                                      best:'"INFO" in dev and prod. "DEBUG" only for deep troubleshooting.'},
  APP_HOST:                {desc:'Network interface the server binds to.',                                                   best:'"0.0.0.0" to accept all interfaces. "127.0.0.1" for local-only.'},
  APP_PORT:                {desc:'HTTP port the FastAPI/uvicorn server listens on.',                                        best:'8888 for dev. Use 443 behind nginx in prod.'},
};

// Fields that cannot be edited (hardcoded in source)
const CFG_READONLY = new Set(['REALTIME_MODEL','REALTIME_URL','OPENAI_TTS_FORMAT','WEBHOOK_TTS_VOICE']);

function cfgTip(key) {
  const m = CFG_META[key] || {};
  return '<span class="cfg-info">?<span class="cfg-tip">'
    + esc(m.desc || key)
    + (m.best ? '<div class="tip-best">✓ Best: ' + esc(m.best) + '</div>' : '')
    + '</span></span>';
}

function cfgRow(key, val, readonly) {
  const isSecret = key.includes('KEY')||key.includes('TOKEN')||key.includes('SECRET');
  const cls = val==='realtime'||val==='prod'?'active': isSecret?'warn':'';
  const editBtn = readonly ? '<span class="cfg-ro">read-only</span>' :
    `<button class="cfg-edit-btn" onclick="cfgEditRow(this,'${esc(key)}')" title="Edit">✏️</button>`;
  return `<div class="cfg-row" id="cfgrow-${esc(key)}">` +
    `<span class="cfg-key">${esc(key)}</span>` +
    `<span class="cfg-val ${cls}" id="cfgval-${esc(key)}">${esc(val)}</span>` +
    cfgTip(key) + editBtn + '</div>';
}

function cfgSection(title, data, readonlySet) {
  return '<div class="cfg-section"><h3>'+title+'</h3>'
    + Object.entries(data).map(([k,v]) => cfgRow(k, String(v), readonlySet.has(k))).join('')
    + '</div>';
}

function cfgEditRow(btn, key) {
  const valSpan = document.getElementById('cfgval-' + key);
  const current = valSpan.textContent;
  const isSecret = key.includes('KEY')||key.includes('TOKEN')||key.includes('SECRET');
  if (valSpan.dataset.editing) return;
  valSpan.dataset.editing = '1';
  const orig = valSpan.innerHTML;
  valSpan.innerHTML = `<input id="cfg-inp-${key}" type="${isSecret?'password':'text'}" value="${esc(current)}"
    style="background:#1e293b;color:#e2e8f0;border:1px solid #4f8ef7;border-radius:4px;padding:3px 8px;font-size:12px;width:280px">
    <button onclick="cfgSaveRow('${key}')" style="margin-left:6px;background:#22c55e;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Save</button>
    <button onclick="cfgCancelRow('${key}','${esc(orig)}')" style="margin-left:4px;background:#475569;color:#e2e8f0;border:none;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:11px">✗</button>`;
  btn.style.display='none';
}

function cfgCancelRow(key, origHtml) {
  const valSpan = document.getElementById('cfgval-' + key);
  valSpan.innerHTML = decodeURIComponent(origHtml.replace(/&#(\\d+);/g,(_,n)=>String.fromCharCode(n)));
  valSpan.innerHTML = origHtml;
  delete valSpan.dataset.editing;
  const row = document.getElementById('cfgrow-' + key);
  if(row) { const b=row.querySelector('.cfg-edit-btn'); if(b) b.style.display=''; }
}

async function cfgSaveRow(key) {
  const inp = document.getElementById('cfg-inp-' + key);
  const value = inp ? inp.value.trim() : '';
  if (!value && !confirm('Save empty value for ' + key + '?')) return;

  let pwd = sessionStorage.getItem('cfgPwd');
  if (!pwd) {
    pwd = prompt('Enter dashboard password to update settings:');
    if (!pwd) return;
  }

  const res = await fetch('/dashboard/config', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({password: pwd, key, value}),
  });
  const d = await res.json();
  if (!d.ok) {
    if (res.status === 403) { sessionStorage.removeItem('cfgPwd'); alert('Wrong password.'); }
    else alert('Error: ' + (d.error || 'unknown'));
    return;
  }
  sessionStorage.setItem('cfgPwd', pwd);

  // Update displayed value
  const valSpan = document.getElementById('cfgval-' + key);
  const isSecret = key.includes('KEY')||key.includes('TOKEN')||key.includes('SECRET');
  valSpan.innerHTML = esc(isSecret ? value.slice(0,4)+'****'+value.slice(-4) : value);
  delete valSpan.dataset.editing;
  const row = document.getElementById('cfgrow-' + key);
  if(row) { const b=row.querySelector('.cfg-edit-btn'); if(b) b.style.display=''; }

  // Show restart notice
  let notice = document.getElementById('cfg-restart-notice');
  if (!notice) {
    notice = document.createElement('div');
    notice.id = 'cfg-restart-notice';
    notice.style.cssText='background:#7c3aed;color:#fff;border-radius:6px;padding:10px 16px;margin-bottom:16px;font-size:13px';
    notice.textContent = '⚠ Settings updated in .env. Restart the server for changes to take effect.';
    document.getElementById('cfg-body').prepend(notice);
  }
}

let _cfgReadonly = new Set();
async function loadConfig() {
  document.getElementById('cfg-body').innerHTML = '<div class="empty-msg" style="padding:40px">Loading...</div>';
  try {
    const res = await fetch('/dashboard/config');
    const d = await res.json();
    _cfgReadonly = new Set([...(d._readonly||[]), ...CFG_READONLY]);
    document.getElementById('cfg-body').innerHTML =
      cfgSection('LLM / Inference (Groq)', d.llm, _cfgReadonly)
      + cfgSection('Realtime Auth (OpenAI Realtime API)', d.realtime, _cfgReadonly)
      + cfgSection('Speech-to-Text — Deepgram STT', d.stt, _cfgReadonly)
      + cfgSection('Text-to-Speech — OpenAI TTS (stream path)', {...Object.fromEntries(Object.entries(d.tts).filter(([k])=>k.startsWith('OPENAI')||k.startsWith('WEBHOOK')||k.startsWith('OPENAI_TTS_FORMAT')))}, _cfgReadonly)
      + cfgSection('Text-to-Speech — ElevenLabs TTS', {...Object.fromEntries(Object.entries(d.tts).filter(([k])=>k.startsWith('ELEVENLABS')))}, _cfgReadonly)
      + cfgSection('Telephony (Twilio)', d.twilio, _cfgReadonly)
      + cfgSection('Backend API', d.backend, _cfgReadonly)
      + cfgSection('Feature Flags', d.feature_flags, _cfgReadonly)
      + cfgSection('Security', d.security, _cfgReadonly)
      + cfgSection('Infrastructure', d.infra, _cfgReadonly);
  } catch(e) {
    document.getElementById('cfg-body').innerHTML = '<div class="empty-msg" style="padding:40px">Failed to load config: '+esc(e.message)+'</div>';
  }
}

// Load config when tab clicked; allow refresh via button
document.querySelectorAll('.tab').forEach(t => {
  if (t.dataset.pane === 'cfg') t.addEventListener('click', loadConfig);
});

// ── Analytics ─────────────────────────────────────────────────
async function loadAnalytics() {
  try {
    const r = await fetch('/dashboard/analytics');
    const d = await r.json();

    // Summary cards
    const s = d.summary;
    const cards = [
      {label:'Total Calls', value:s.total_calls, color:'#3b82f6'},
      {label:'Active', value:s.active_calls, color:'#22c55e'},
      {label:'Auth Failures', value:s.auth_failures, color:s.auth_failures>0?'#ef4444':'#64748b'},
      {label:'Escalations', value:s.escalations, color:s.escalations>0?'#f59e0b':'#64748b'},
      {label:'Low STT Conf.', value:s.stt_low_confidence, color:s.stt_low_confidence>0?'#f97316':'#64748b'},
      {label:'Avg Latency', value:s.avg_graph_latency_ms+'ms', color:s.avg_graph_latency_ms>3000?'#ef4444':'#64748b'},
      {label:'Avg Turns/Call', value:s.avg_turns_per_call, color:'#64748b'},
    ];
    document.getElementById('analytics-summary').innerHTML = cards.map(c =>
      `<div style="background:#1e293b;border-radius:8px;padding:16px;border-left:3px solid ${c.color}">
        <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:1px">${c.label}</div>
        <div style="color:#e2e8f0;font-size:28px;font-weight:700;margin-top:4px">${c.value}</div>
      </div>`
    ).join('');

    // Intent distribution bars
    const maxI = Math.max(...Object.values(d.intent_distribution||{}), 1);
    document.getElementById('analytics-intents').innerHTML =
      Object.entries(d.intent_distribution||{}).sort((a,b)=>b[1]-a[1]).map(([k,v]) =>
        `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span style="width:100px;color:#94a3b8;font-size:11px;text-align:right">${esc(k)}</span>
          <div style="flex:1;background:#0f172a;border-radius:4px;height:18px;overflow:hidden">
            <div style="width:${(v/maxI*100).toFixed(1)}%;background:#3b82f6;height:100%;border-radius:4px;transition:width 0.3s"></div>
          </div>
          <span style="color:#e2e8f0;font-size:11px;width:30px">${v}</span>
        </div>`
      ).join('') || '<div style="color:#64748b;padding:12px">No data</div>';

    // Node usage bars
    const maxN = Math.max(...Object.values(d.node_usage||{}), 1);
    document.getElementById('analytics-nodes').innerHTML =
      Object.entries(d.node_usage||{}).sort((a,b)=>b[1]-a[1]).map(([k,v]) =>
        `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span style="width:100px;color:#94a3b8;font-size:11px;text-align:right">${esc(k)}</span>
          <div style="flex:1;background:#0f172a;border-radius:4px;height:18px;overflow:hidden">
            <div style="width:${(v/maxN*100).toFixed(1)}%;background:#22c55e;height:100%;border-radius:4px;transition:width 0.3s"></div>
          </div>
          <span style="color:#e2e8f0;font-size:11px;width:30px">${v}</span>
        </div>`
      ).join('') || '<div style="color:#64748b;padding:12px">No data</div>';

    // Issues table
    const typeColors = {
      auth_failure:'#ef4444', stt_low_confidence:'#f97316', slow_graph:'#f59e0b',
      api_error:'#ef4444', dob_mismatch:'#a855f7'
    };
    const issues = d.issues || [];
    document.getElementById('analytics-issues-body').innerHTML = issues.length ?
      issues.map(i => {
        const col = typeColors[i.type]||'#64748b';
        return `<tr style="border-bottom:1px solid #1e293b">
          <td style="padding:8px 12px"><span style="background:${col}22;color:${col};padding:2px 8px;border-radius:4px;font-size:11px">${esc(i.type)}</span></td>
          <td style="padding:8px 12px;font-family:monospace;font-size:11px;color:#38bdf8;cursor:pointer" onclick="document.querySelector('[data-pane=calls]').click();selCallFn('${esc(i.call_sid)}')">${esc((i.call_sid||'').slice(-8))}</td>
          <td style="padding:8px 12px;color:#94a3b8;font-size:11px">${esc(i.detail)}</td>
        </tr>`;
      }).join('') :
      '<tr><td colspan="3" style="padding:20px;text-align:center;color:#64748b">No issues detected</td></tr>';

  } catch(e) {
    document.getElementById('analytics-summary').innerHTML =
      '<div style="color:#ef4444;padding:20px">Failed to load analytics: '+esc(e.message)+'</div>';
  }
}

document.querySelectorAll('.tab').forEach(t => {
  if (t.dataset.pane === 'analytics') t.addEventListener('click', loadAnalytics);
});

// ── Init ──────────────────────────────────────────────────────
connectLogs();
loadCalls();
setInterval(loadCalls, 10000);
initChats();
</script>
</body>
</html>"""
