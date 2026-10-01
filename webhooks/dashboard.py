"""
CNO IVR Dashboard — served at /dashboard
Tabs: WebChat | Calls | Softphone | Codebase | Graph | Logs | Analytics | Config
"""
import re
import secrets
import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from config import settings
from services.conversation_store import get_calls, get_call
from webhooks.security import require_dashboard_auth

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_auth)],
)


@router.get("/calls")
async def api_calls(limit: int = 50, offset: int = 0):
    from services import call_db as _db
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows, total = _db.list_calls(limit=limit, offset=offset)
    if not rows and offset == 0:
        rows = get_calls()
        total = len(rows)
    return JSONResponse({"calls": rows, "total": total, "limit": limit, "offset": offset})


@router.get("/calls/{call_sid}")
async def api_call_detail(call_sid: str):
    from services import call_db as _db
    call = get_call(call_sid)
    if not call.get("call_sid"):
        call = _db.load_call(call_sid)
    return JSONResponse(call)


@router.get("/calls/{call_sid}/events")
async def get_call_events(call_sid: str):
    from services.conversation_store import get_call
    call = get_call(call_sid)
    return JSONResponse({"call_sid": call_sid, "events": call.get("events", [])})


_RECORDING_SID = re.compile(r"^RE[0-9a-fA-F]{32}$")


@router.get("/calls/{call_sid}/recording")
async def call_recording(call_sid: str, download: bool = False):
    """Serve a call recording through the app (#69).

    Twilio media URLs need the account credentials, so a direct browser link
    returns 401. The URL is built from the stored recording SID, never taken
    from stored data, so this can only ever fetch this account's recordings.
    """
    call = get_call(call_sid)
    rec_sid = call.get("recording_sid") or ""
    if not _RECORDING_SID.match(rec_sid):
        return JSONResponse({"error": "no recording for this call"}, status_code=404)
    sid, token = settings.twilio_account_sid, settings.twilio_auth_token
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Recordings/{rec_sid}.mp3"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, auth=(sid, token))
    if resp.status_code != 200:
        return JSONResponse({"error": f"Twilio returned {resp.status_code}"}, status_code=502)
    # ?download=1 saves the file (#71); default streams inline for the player
    started = (call.get("started_at") or "")[:19].replace(":", "").replace("T", "_")
    filename = f"{started + '_' if started else ''}{call_sid}.mp3"
    disposition = "attachment" if download else "inline"
    return Response(
        content=resp.content,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"',
                 "Cache-Control": "private, max-age=3600"},
    )


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


@router.post("/calls/{call_sid}/end")
async def end_single_call(call_sid: str):
    """Mark a single call as ended."""
    from services.conversation_store import end_call, get_call
    call = get_call(call_sid)
    if not call.get("call_sid"):
        return JSONResponse({"error": "Call not found"}, status_code=404)
    end_call(call_sid)
    return JSONResponse({"call_sid": call_sid, "status": "ended"})


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
            "LLM_PROVIDER":          settings.llm_provider,
            "GROQ_MODEL":            settings.groq_model,
            "ROUTER_MODEL":          settings.router_model,
            "BEDROCK_MODEL":         settings.bedrock_model,
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
            "SYNC_TWILIO_WEBHOOKS":       str(settings.sync_twilio_webhooks),
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
    """Update a single .env key. Requires password. Disabled in prod (no writable .env on Fargate)."""
    from config import settings as _settings
    if _settings.is_prod:
        return JSONResponse(
            {"ok": False, "error": "Runtime .env updates are disabled in production"},
            status_code=403,
        )

    password = payload.get("password", "")
    key      = payload.get("key", "").strip().upper()
    value    = payload.get("value", "")

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
.rec-btn{display:inline-block;font-size:11px;line-height:1;color:#22c55e;background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);padding:2px 6px;border-radius:4px;cursor:pointer;margin-left:4px;text-decoration:none}
.rec-btn:hover{background:rgba(34,197,94,.2)}
.rec-bar{display:none;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid #334155;background:#0f172a}
.rec-bar audio{height:28px;flex:1;min-width:0}
.rec-bar .rec-label{font-size:10px;color:#94a3b8;font-family:monospace;white-space:nowrap}
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

/* ── Softphone DTMF keypad ── */
.dtmf-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0 6px}
.dtmf-btn{padding:12px 0;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:18px;font-weight:600;cursor:pointer;font-family:inherit}
.dtmf-btn:hover:not(:disabled){background:#334155}
.dtmf-btn:disabled{opacity:.35;cursor:not-allowed}
.dtmf-hint{font-size:11px;color:#64748b;text-align:center;margin-bottom:4px}
.now-banner{background:rgba(56,189,248,.08);border:1px solid rgba(56,189,248,.25);border-radius:8px;padding:10px 12px;font-size:12px;color:#7dd3fc;margin:0 0 16px;line-height:1.5}
</style>
</head>
<body>

<div class="topbar">
  <h1>IVR Dashboard</h1>
  <span class="live">LIVE</span>
  <span style="font-size:11px;color:#a78bfa;background:rgba(167,139,250,.12);padding:2px 8px;border-radius:4px;margin-left:6px;font-family:monospace">v2.1.0</span>
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
      <div class="chat-hdr">Same LangGraph as phone (thread_id = chat_&lt;session&gt;) · Session: <span id="chat-sid" style="font-family:monospace;font-size:11px;color:#38bdf8">—</span></div>
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
      <div class="panel-hdr">Phone Calls <span style="font-size:10px;color:#64748b;font-weight:400;margin-left:8px">Postgres-backed · PAN/CVV redacted</span> <button class="refresh" onclick="loadCalls()">Refresh</button></div>
      <!-- Persistent player: outside #call-list so 3s polling never interrupts playback (#71) -->
      <div class="rec-bar" id="rec-bar"><span class="rec-label" id="rec-label"></span><audio id="rec-player" controls preload="none"></audio></div>
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
  <div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;background:#0f172a;overflow:auto;padding:24px 0">
    <div style="background:#1e293b;border-radius:16px;padding:28px;width:360px;box-shadow:0 20px 60px rgba(0,0,0,.5)">
      <h2 style="font-size:17px;font-weight:600;margin-bottom:3px;color:#f1f5f9">IVR Softphone</h2>
      <p style="font-size:12px;color:#64748b;margin-bottom:16px">Twilio Voice JS SDK — calls <code style="color:#94a3b8">/webhook/voice</code>. Use the keypad to send DTMF during card/account entry.</p>
      <div id="ph-ngrok-warn" style="display:none;background:rgba(251,146,60,.1);border:1px solid rgba(251,146,60,.3);border-radius:8px;padding:10px 12px;font-size:12px;color:#fb923c;margin-bottom:14px">
        ⚠️ <strong>Public tunnel required for voice.</strong><br>
        Cloudflare: <code style="background:#0f172a;padding:2px 6px;border-radius:4px">cloudflared tunnel --url http://localhost:8888</code><br>
        or ngrok: <code style="background:#0f172a;padding:2px 6px;border-radius:4px">ngrok http 8888</code><br>
        Set <code style="background:#0f172a;padding:2px 6px;border-radius:4px">TWILIO_BASE_URL</code> + TwiML App Voice URL to that host + <code style="background:#0f172a;padding:2px 6px;border-radius:4px">/webhook/voice</code>
      </div>
      <div style="background:#0f172a;border-radius:8px;padding:10px 14px;font-size:13px;color:#94a3b8;margin-bottom:16px;display:flex;align-items:center;gap:8px">
        <div id="ph-dot" style="width:8px;height:8px;border-radius:50%;background:#475569;flex-shrink:0;transition:background .3s"></div>
        <span id="ph-status">Initialising...</span>
      </div>
      <div id="ph-timer" style="text-align:center;font-size:28px;font-weight:300;color:#3b82f6;margin:8px 0;letter-spacing:2px;display:none">0:00</div>
      <button id="ph-btn-call" onclick="phCall()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:8px;background:#22c55e;color:#fff;opacity:.35">Call IVR</button>
      <button id="ph-btn-mute" onclick="phMute()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;margin-bottom:8px;background:#334155;color:#e2e8f0;opacity:.35">Mute</button>
      <button id="ph-btn-hang" onclick="phHang()" disabled style="width:100%;padding:13px;border-radius:10px;border:none;font-size:15px;font-weight:600;cursor:pointer;background:#ef4444;color:#fff;opacity:.35">Hang Up</button>
      <p class="dtmf-hint">DTMF keypad — active during a connected call (or type 0–9 * #)</p>
      <div class="dtmf-pad" id="ph-dtmf">
        <button class="dtmf-btn" disabled onclick="phSendDtmf('1')">1</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('2')">2</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('3')">3</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('4')">4</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('5')">5</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('6')">6</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('7')">7</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('8')">8</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('9')">9</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('*')">*</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('0')">0</button>
        <button class="dtmf-btn" disabled onclick="phSendDtmf('#')">#</button>
      </div>
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
        <div class="now-banner"><strong>Current path (this machine):</strong> FastAPI on :8888 via <code>.venv\Scripts\python.exe run.py</code>. Twilio hits a Cloudflare quick tunnel → <code>POST /webhook/voice</code>. Live path is TwiML <code>&lt;Gather&gt;</code> (speech, plus DTMF on card/account). Media Streams (<code>/stream</code>) exist but are not the default webhook path.</div>
        <div class="block">
          <h3>run.py — three jobs before server starts</h3>
          <ul>
            <li><strong>Tee stdout/stderr</strong> — wraps sys.stdout with _Tee so every print/log line goes to terminal + ivr.log + in-memory log_bus (powers SSE live viewer)</li>
            <li><strong>Windows event loop policy</strong> — must be set before uvicorn creates the loop</li>
            <li><strong>uvicorn.run("main:app")</strong> — starts FastAPI on <code>APP_HOST</code>/<code>APP_PORT</code> (local default 8888)</li>
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
            <li>Startup: AsyncPostgresSaver graph compile (MemorySaver only if Postgres is down), call-history tables + RAG, Redis health, optional <code>SYNC_TWILIO_WEBHOOKS</code></li>
            <li>structlog writes to sys.stdout → captured by _Tee above</li>
          </ul>
        </div>
        <div class="block">
          <h3>webhooks/twilio_voice.py — the IVR loop (current)</h3>
          <div class="flow">POST /webhook/voice  →  greet caller, start speech &lt;Gather&gt;

POST /webhook/gather (each utterance):
  1. Empty SpeechResult → timeout prompt (yes/no if last bot turn was a confirm)
  2. cno_graph.ainvoke({messages:[HumanMessage(transcript)]}, thread_id=call_sid)
  3. is_terminal(result)?  → &lt;Say&gt; + optional &lt;Dial agent&gt; + &lt;Hangup/&gt;
       (goodbye, current_node=escalation, current_intent=escalate, or transfer_to set)
  4. otp_step in collecting_card_dtmf | collecting_bank_dtmf
       → &lt;Gather input="dtmf speech"&gt; action=/webhook/gather-payment  (# ends DTMF)
  5. else → &lt;Say tts_text&gt; + speech &lt;Gather&gt; (loop)

POST /webhook/gather-payment:
  Digits (preferred) or SpeechResult → graph, then hangup / DTMF gather / speech gather</div>
          <p><strong>Key:</strong> <code>thread_id = call_sid</code> — AsyncPostgresSaver isolates each call across restarts. Service nodes that need a live agent must call <code>transfer_now()</code> so Twilio never Gather after “let me transfer you”.</p>
        </div>
      </div>

      <div class="code-section" id="s2">
        <h2>Session 2 — State &amp; Graph Architecture</h2>
        <p class="sub"><span class="chip">core/graph/state.py</span> → <span class="chip">core/graph/graph.py</span> → <span class="chip">core/graph/auth_guard.py</span></p>
        <div class="now-banner"><strong>Auth is not a graph node.</strong> START → router → one service node → END. Each restricted node calls <code>ensure_authenticated()</code>, which runs <code>auth_node</code> in-process until PII + persona are done, then the same turn can continue into the service.</div>
        <div class="block">
          <h3>CNOState — persisted fields (current)</h3>
          <table class="tbl">
            <tr><th>Field</th><th>Type</th><th>Purpose</th></tr>
            <tr><td>messages</td><td>list</td><td>Turn history. add_messages reducer = append-only. Pass only new messages each turn.</td></tr>
            <tr><td>call_sid</td><td>str</td><td>= LangGraph thread_id (AsyncPostgresSaver). Isolates each call.</td></tr>
            <tr><td>authenticated</td><td>bool</td><td>True after PII match (before persona name is required)</td></tr>
            <tr><td>auth_step</td><td>str</td><td>collecting_phone → dob/name → collecting_caller_name → complete|failed</td></tr>
            <tr><td>caller_name</td><td>str</td><td>What the caller said after “May I ask your name please?”</td></tr>
            <tr><td>caller_persona</td><td>str</td><td>payor | insured | owner | other — required before service nodes proceed</td></tr>
            <tr><td>auth_attempts</td><td>int</td><td>Failed attempts. Max 3 → transfer_now()</td></tr>
            <tr><td>pii_collected</td><td>dict</td><td>{phoneNumber, policyNumber, dateOfBirth}</td></tr>
            <tr><td>customer</td><td>dict</td><td>Post-auth: firstName, lastName, policyNumber, partyKey</td></tr>
            <tr><td>access_token</td><td>str</td><td>Bearer token for CNO API calls</td></tr>
            <tr><td>current_intent</td><td>str</td><td>Last intent from router (policy_info, otp, escalate, goodbye, …)</td></tr>
            <tr><td>active_flow</td><td>str</td><td>"" = open routing. Node name while auth or a multi-step flow owns the call.</td></tr>
            <tr><td>tts_text</td><td>str</td><td>Text to speak this turn</td></tr>
            <tr><td>transfer_to</td><td>str</td><td>Agent DID. Non-empty ⇒ Twilio hangs up (is_terminal)</td></tr>
            <tr><td>otp_step / otp_data</td><td>str / dict</td><td>OTP state machine + payment fields. PAN/CVV wiped after API call; never stored in transcripts.</td></tr>
            <tr><td>beneficiary_step</td><td>str</td><td>list → action → collect → confirm → submit</td></tr>
          </table>
        </div>
        <div class="block">
          <h3>graph.py — routing (current: _route_after_router)</h3>
          <div class="box"><span class="kw">def</span> <span class="fn">_route_after_router</span>(state):
    intent = state.get(<span class="str">"current_intent"</span>, <span class="str">"faq"</span>)
    <span class="kw">if not</span> intent: <span class="kw">return</span> END   <span class="cmt"># empty STT — Twilio re-prompts</span>
    <span class="cmt"># policy_info→policy, contact_change→contact, owner_change→escalation, …</span>
    <span class="kw">return</span> route_map.get(intent, <span class="str">"faq"</span>)</div>
          <p>Checkpointer is <strong>AsyncPostgresSaver</strong> with a psycopg async pool (falls back to MemorySaver only if Postgres is unreachable). Postgres also stores call-history tables + pgvector RAG. Redis is health/session.</p>
        </div>
      </div>

      <div class="code-section" id="s3">
        <h2>Session 3 — Router Node</h2>
        <p class="sub"><span class="chip">core/graph/nodes/router.py</span> · model = <code>ROUTER_MODEL</code> (default <code>qwen/qwen3.8-27b</code>)</p>
        <div class="block">
          <h3>Classification order (current)</h3>
          <div class="flow">1. GOODBYE — "goodbye", "bye", bare "thank you"/"thanks" → intent=goodbye (hang up)
2. ESCALATION KEYWORDS — agent, representative, human, transfer, supervisor
   (skipped when the utterance is a confirmation "no" / "that is wrong")
3. PRIVACY PHRASES — "privacy policy", "opt out", "how do you use my…"
4. FLOW LOCK (CONTEXT_SWITCH_CONFIG)
   otp → locked (PCI). payment/policy/loan/beneficiary/contact/document/privacy → escalate_only.
   faq/escalation → open.
5. LLM CLASSIFICATION (Groq qwen/qwen3.8-27b, temp=0)
   → one word from VALID_INTENTS. unknown → faq. empty speech → empty intent → END.</div>
        </div>
        <div class="block">
          <h3>Context-switch modes</h3>
          <table class="tbl">
            <tr><th>Mode</th><th>When</th><th>Behaviour</th></tr>
            <tr><td>locked</td><td>otp (PCI DTMF)</td><td>No LLM. Stay in OTP. Escalation and bare thank-you/goodbye still exit.</td></tr>
            <tr><td>escalate_only</td><td>payment, policy, loan, beneficiary, contact, document, privacy</td><td>LLM runs. Non-escalation pivots are forced back to the active flow.</td></tr>
            <tr><td>open</td><td>active_flow="" or faq</td><td>Full free routing.</td></tr>
          </table>
        </div>
      </div>

      <div class="code-section" id="s4">
        <h2>Session 4 — Auth Guard + Persona Name</h2>
        <p class="sub"><span class="chip">core/graph/auth_guard.py</span> · <span class="chip">core/graph/nodes/auth.py</span> · <span class="chip">utils/name_extractor.py</span></p>
        <div class="block">
          <h3>Auth state machine (current)</h3>
          <div class="flow">collecting_phone → party_search(ANI or spoken phone)
  found     → collecting_dob
  not found → confirming_phone → yes → collecting_policy / no → collecting_phone

collecting_dob → confirming_dob ("I heard Jul 16, 1938. Is that correct?")
  yes + MATCH → authenticated (persona name only if not already collected)
  yes + NOMATCH → collecting_name (insured first/last)
  new date spoken → capture it, then match or re-confirm
  no → collecting_dob

collecting_caller_name → extract_name() (deterministic then LLM)
  fillers uh/um stripped so "Uh, my name is uh, John Smith." → John Smith
  labeled "my first name is John, last name is Smith"
  NATO / "J for Juliet" spelling
  → match payor | insured | owner | other
  other → transfer_now() (cannot access policy data)
  match → persona set; service node continues same turn

Max 3 auth failures → transfer_now() (Say + Dial + Hangup, never Gather)</div>
        </div>
        <div class="block">
          <h3>Test callers (mock API)</h3>
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
        <p class="sub"><span class="chip">core/graph/nodes/</span> — policy, payment, otp, loan, beneficiary, contact, document, privacy, faq, escalation, goodbye</p>
        <div class="block">
          <h3>One-shot nodes (policy, payment history, loan)</h3>
          <ul>
            <li>Guard with <code>ensure_authenticated</code>, fetch CNO API, LLM formats TTS</li>
            <li>API/missing-policy errors use <code>transfer_now()</code> so the call hangs up</li>
            <li>payment.py (history) appends: "Please allow 24-48 hours for payment to post…"</li>
          </ul>
        </div>
        <div class="block">
          <h3>OTP one-time payment (current PCI rules)</h3>
          <ul>
            <li>Card/account: DTMF or speech via <code>/webhook/gather-payment</code>. Prefer keypad digits.</li>
            <li>Full <strong>16-digit PAN</strong> is read back in 4-4-4-4 groups for confirmation (not last-4).</li>
            <li>CVV is hardcoded to <strong>exactly 3 digits</strong> and is also spoken back, then redacted.</li>
            <li>STT extra digits are trimmed silently (<code>try_trim_extra_digits</code>); callers are not blamed for STT.</li>
            <li>PAN/CVV live only in memory until the payment API call, then wiped. Transcripts/logs redact them.</li>
            <li>After success: spell confirmation IDs slowly; repeat on request; bare “thank you” → goodbye hangup.</li>
            <li>Card-capture max retries → <code>transfer_now()</code> (no leftover Gather).</li>
          </ul>
        </div>
        <div class="block">
          <h3>Multi-step (contact, document, privacy, beneficiary)</h3>
          <ul>
            <li>Lock <code>active_flow</code> until complete or cancelled. Failures call <code>transfer_now()</code>.</li>
            <li>Beneficiary: list → add/remove/update percentages with confirm.</li>
          </ul>
        </div>
        <div class="block">
          <h3>Termination</h3>
          <ul>
            <li><strong>transfer_now(tts)</strong> — sets current_node=escalation, intent=escalate, transfer_to=agent DID (may be empty). Twilio: Say + optional Dial + Hangup.</li>
            <li><strong>goodbye</strong> — current_node=goodbye → Say + Hangup (no Dial).</li>
            <li><code>is_terminal()</code> is true for those nodes, escalate intent, or any non-empty transfer_to.</li>
          </ul>
        </div>
      </div>

      <div class="code-section" id="s6">
        <h2>Session 6 — Infrastructure</h2>
        <p class="sub"><span class="chip">log_bus.py</span> · <span class="chip">services/conversation_store.py</span> · <span class="chip">services/call_db.py</span> · Docker Redis/Postgres</p>
        <div class="block">
          <h3>Live log pipeline</h3>
          <div class="flow">structlog.info() → sys.stdout → _Tee.write()
  ├── sys.__stdout__  (terminal)
  ├── ivr.log file    (disk)
  └── log_bus.push()
       ├── _buffer deque(maxlen=500)
       └── GET /client/logs/stream → EventSource → Live Logs tab</div>
        </div>
        <div class="block">
          <h3>Call history</h3>
          <ul>
            <li>In-memory cache + PostgreSQL write-through. Startup <code>init_from_db()</code> reloads last 100 calls so the Calls tab survives restart.</li>
            <li>Turns pass through <code>utils/pii_redactor.py</code> (bot + human PAN/CVV).</li>
            <li>WebChat sessions stay in-memory only.</li>
          </ul>
        </div>
        <div class="block">
          <h3>Local stack (this machine)</h3>
          <ul>
            <li>IVR <code>run.py</code> :8888 · mock CNO API :8001 · Redis :6379 · Postgres :5432 (compose)</li>
            <li>Graph checkpointer: AsyncPostgresSaver (async pool). Postgres also: call history + RAG. Redis: health/session.</li>
            <li>Twilio public URL: <code>TWILIO_BASE_URL</code> (Cloudflare quick tunnel or ngrok). <code>SYNC_TWILIO_WEBHOOKS=true</code> rewrites the TwiML App + DID Voice URL on startup.</li>
            <li>Browser phone: dashboard Softphone tab and <code>/client</code>. Mic needs localhost or HTTPS. DTMF via <code>call.sendDigits()</code>.</li>
          </ul>
        </div>
        <div class="block">
          <h3>WebChat API (webhooks/chat.py)</h3>
          <ul>
            <li>POST /chat/message — same LangGraph, thread_id="chat_{session_id}"</li>
            <li>GET /chat/history/{sid} · GET /chat/sessions · POST /chat/reset/{sid}</li>
          </ul>
        </div>
      </div>

    </div><!-- end code-body -->
  </div><!-- end code-wrap -->
</div>

<!-- GRAPH -->
<div class="pane" id="pane-graph">
  <div class="graph-wrap">
    <h2>LangGraph — IVR Flow (current)</h2>
    <p style="font-size:12px;color:#7dd3fc;margin:0 0 12px;max-width:720px">Auth is a guard inside each restricted node, not a graph vertex. Every service node ends at END; Twilio then Gather, DTMF-gather, or Hangup based on <code>is_terminal</code> / <code>otp_step</code>.</p>
    <div class="gdiag">[START]
   |
   v
+--------+
| router |   Groq qwen/qwen3.8-27b  (or ROUTER_MODEL)
+---+----+
    |
    |  empty intent (timeout) ----------&gt; [END]  Twilio re-prompts
    |
    +-- policy_info    --&gt; [policy]       --&gt; [END]   ensure_authenticated first
    +-- payment        --&gt; [payment]      --&gt; [END]
    +-- otp            --&gt; [otp]          --&gt; [END]   DTMF gather while collecting PAN/account
    +-- loan           --&gt; [loan]         --&gt; [END]
    +-- beneficiary    --&gt; [beneficiary]  --&gt; [END]
    +-- contact_change --&gt; [contact]      --&gt; [END]   active_flow locked until done
    +-- document       --&gt; [document]     --&gt; [END]
    +-- privacy        --&gt; [privacy]      --&gt; [END]
    +-- faq            --&gt; [faq]          --&gt; [END]
    +-- owner_change   --&gt; [escalation]   --&gt; [END] --&gt; transfer_now: Say + Dial + Hangup
    +-- escalate       --&gt; [escalation]   --&gt; [END] --&gt; same
    +-- goodbye        --&gt; [goodbye]      --&gt; [END] --&gt; Say + Hangup (no Dial)

Auth (inside the service node, same turn):
  collecting_phone → dob/name → collecting_caller_name → persona
  fail / persona=other / API error → transfer_now()</div>
    <div class="state-ref">
      <h3>CNOState Fields</h3>
      <div class="sg">
        <h4>Conversation</h4>
        <div class="sr"><span class="sk">messages</span><span class="st">list</span><span class="sd">Turn history. add_messages reducer (append-only)</span></div>
        <div class="sr"><span class="sk">call_sid</span><span class="st">str</span><span class="sd">= LangGraph thread_id (AsyncPostgresSaver)</span></div>
        <div class="sr"><span class="sk">tts_text</span><span class="st">str</span><span class="sd">Text spoken via TwiML &lt;Say Polly.Joanna&gt;</span></div>
        <div class="sr"><span class="sk">transfer_to</span><span class="st">str</span><span class="sd">Agent DID. Any value ⇒ is_terminal hangup</span></div>
      </div>
      <div class="sg">
        <h4>Authentication</h4>
        <div class="sr"><span class="sk">authenticated</span><span class="st">bool</span><span class="sd">True after PII match</span></div>
        <div class="sr"><span class="sk">auth_step</span><span class="st">str</span><span class="sd">phone → dob/name → collecting_caller_name → complete</span></div>
        <div class="sr"><span class="sk">caller_name / persona</span><span class="st">str</span><span class="sd">Spoken name → payor|insured|owner|other</span></div>
        <div class="sr"><span class="sk">auth_attempts</span><span class="st">int</span><span class="sd">Max 3 → transfer_now()</span></div>
        <div class="sr"><span class="sk">pii_collected</span><span class="st">dict</span><span class="sd">{phoneNumber, policyNumber, dateOfBirth}</span></div>
      </div>
      <div class="sg">
        <h4>Customer (post-auth)</h4>
        <div class="sr"><span class="sk">customer</span><span class="st">dict</span><span class="sd">firstName, lastName, policyNumber, partyKey</span></div>
        <div class="sr"><span class="sk">access_token</span><span class="st">str</span><span class="sd">Bearer token for CNO API</span></div>
      </div>
      <div class="sg">
        <h4>Flow Control</h4>
        <div class="sr"><span class="sk">current_intent</span><span class="st">str</span><span class="sd">Last classified intent</span></div>
        <div class="sr"><span class="sk">active_flow</span><span class="st">str</span><span class="sd">"" = open; node name = locked / auth-in-progress</span></div>
        <div class="sr"><span class="sk">current_node</span><span class="st">str</span><span class="sd">Last node that ran</span></div>
        <div class="sr"><span class="sk">otp_step / otp_data</span><span class="st">str / dict</span><span class="sd">OTP machine. PAN/CVV not persisted.</span></div>
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
      <span style="font-size:11px;color:#64748b">Live Redis/memory calls · escalations = node_enter escalation · STT conf &lt; 0.7 · graph &gt; 5s</span>
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
      <span style="font-size:11px;color:#64748b">Values from running process / .env. LLM default is Groq qwen/qwen3.8-27b. Restart required after Save.</span>
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

// ── Render only on change (#77) ──────────────────────────────────────────────
// Polling used to rebuild every section each time: flicker, lost scroll and
// selection. setHtml() skips the DOM write when the HTML is unchanged.
const _rendered = {};
let _detailSid = null;  // call currently shown in the detail pane
function setHtml(id, html) {
  if (_rendered[id] === html) return false;
  const el = document.getElementById(id);
  if (!el) return false;
  el.innerHTML = html;
  _rendered[id] = html;
  return true;
}
function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}
// No polling work while the tab is hidden; catch up as soon as it's visible
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadCalls(); });

function startCallsPoll() {
  if (_callsPollTimer) return;
  loadCalls();
  _callsPollTimer = setInterval(loadCalls, 3000);
}
function stopCallsPoll() {
  if (_callsPollTimer) { clearInterval(_callsPollTimer); _callsPollTimer = null; }
}

// ── Recording playback / download (#71) ─────────────────────────────────────
let playingSid = null;
function recUrl(sid, download) {
  return '/dashboard/calls/' + encodeURIComponent(sid) + '/recording' + (download ? '?download=1' : '');
}
function recButtons(c, labelled) {
  if (!c.recording_sid) return '';
  const player = document.getElementById('rec-player');
  const playing = playingSid === c.call_sid && player && !player.paused;
  return '<button class="rec-btn" title="'+(playing ? 'Pause' : 'Play recording')+'" '
    + 'onclick="toggleRec(\\''+c.call_sid+'\\');event.stopPropagation()">'
    + (playing ? '⏸' : '▶') + (labelled ? (playing ? ' Pause' : ' Play') : '') + '</button>'
    + '<a class="rec-btn" title="Download recording (.mp3)" href="'+recUrl(c.call_sid, true)+'" download '
    + 'onclick="event.stopPropagation()">⬇' + (labelled ? ' Download' : '') + '</a>';
}
function toggleRec(sid) {
  const player = document.getElementById('rec-player');
  if (playingSid === sid && !player.paused) { player.pause(); return; }
  if (playingSid !== sid) {
    playingSid = sid;
    player.src = recUrl(sid, false);
    document.getElementById('rec-label').textContent = '▶ ' + sid.slice(0, 14) + '…';
  }
  document.getElementById('rec-bar').style.display = 'flex';
  player.play().catch(e => console.error('recording playback failed', e));
}
// Keep ▶/⏸ icons in sync with the player without waiting for the next poll
['play', 'pause', 'ended'].forEach(ev => document.addEventListener('DOMContentLoaded', () => {
  const player = document.getElementById('rec-player');
  if (player) player.addEventListener(ev, () => { loadCalls(); });
}));

async function loadCalls() {
  if (document.hidden) return;
  try {
    const res = await fetch('/dashboard/calls');
    const data = await res.json();
    const calls = data.calls || [];
    document.getElementById('call-cnt').textContent = calls.length;
    if (!calls.length) {
      setHtml('call-list', '<div class="empty-msg">No calls recorded yet.<br>Make a call via the Softphone.</div>');
      return;
    }
    setHtml('call-list', calls.map(c => {
      const from = (c.from_number||'').replace('client:','').replace('+1','');
      const active = c.status === 'active';
      const sel = c.call_sid === selCall ? ' on' : '';
      const endBtn = active ? '<button onclick="endCallFn(\\''+c.call_sid+'\\');event.stopPropagation()" style="font-size:9px;color:#ef4444;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);padding:1px 6px;border-radius:3px;cursor:pointer;margin-left:6px">End</button>' : '';
      return '<div class="call-item'+sel+'" onclick="selCallFn(\\''+c.call_sid+'\\')"><div class="csid">'+c.call_sid.slice(0,24)+'</div>'
        + '<div class="cfrom">'+(from||'Softphone')+'</div>'
        + '<div class="cmeta"><span class="dot '+(active?'active':'ended')+'"></span>'+(active?'Active':'Ended')
        + endBtn
        + ' &nbsp; '+c.turns.length+' turns &nbsp; '+c.started_at.slice(11,16)
        + recButtons(c) + '</div></div>';
    }).join(''));
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

  return authCard + piiCard + flowCard + intentModelCard;
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
    // Served through the app (Twilio's URL needs account auth, #69); plays in the
    // persistent player so polling can't cut it off (#71)
    const recBadge = call.recording_sid ? ' <span style="margin-left:6px">' + recButtons(call, true) + '</span>' : '';

    // Opening a different call: forget cached sections so everything renders
    // and the transcript starts at the bottom (#77)
    const switched = csid !== _detailSid;
    if (switched) {
      _detailSid = csid;
      ['call-hdr', 'call-state-panel', 'call-turns', 'ev-rows'].forEach(id => delete _rendered[id]);
    }

    // Share button — opens standalone call URL
    const shareBtn = '<a href="/dashboard/call/'+csid+'" target="_blank" style="font-size:11px;color:#a78bfa;margin-left:10px;text-decoration:none;border:1px solid rgba(167,139,250,.3);padding:2px 8px;border-radius:4px">&#x2197; Share</a>';

    // End Call button — only shown for active calls
    const endBtn = call.status === 'active'
      ? '<button onclick="endCallFn(\\''+csid+'\\');event.stopPropagation()" style="font-size:11px;color:#ef4444;margin-left:10px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.4);padding:2px 10px;border-radius:4px;cursor:pointer">✕ End Call</button>'
      : '';

    if (setHtml('call-hdr', esc(from || 'Softphone') + ' — ' + (call.started_at||'').slice(0,19) + status + recBadge + shareBtn + endBtn)) {
      // Copy session ID in header (re-added only when the header was redrawn)
      const hdrEl = document.getElementById('call-hdr');
      const idBtn = document.createElement('button');
      idBtn.className = 'copy-btn'; idBtn.textContent = '⎘ SID';
      idBtn.style.cssText = 'position:static;margin-left:10px;';
      idBtn.onclick = () => copyToClipboard(csid, idBtn);
      hdrEl.appendChild(idBtn);
    }

    if (setHtml('call-state-panel', renderStatePanel(call))) {
      const stateEl = document.getElementById('call-state-panel');
      stateEl.style.position = 'relative';
      const sBtn = document.createElement('button');
      sBtn.className = 'copy-btn'; sBtn.textContent = '⎘ Copy State';
      sBtn.onclick = () => copyToClipboard(stateEl.innerText, sBtn);
      stateEl.insertBefore(sBtn, stateEl.firstChild);
    }
    renderEventTimeline(csid, switched);

    const el = document.getElementById('call-turns');
    if (!call.turns || !call.turns.length) {
      setHtml('call-turns', '<div class="empty-msg">No turns recorded</div>'); return;
    }
    // The detail pane scrolls, not the transcript box. Follow new turns only if
    // you were already at the bottom — never yank it while you're reading
    // further up; opening a call keeps the top (state cards) in view.
    const scroller = el.closest('.call-detail-scroll') || el;
    const stick = !switched && nearBottom(scroller);
    const turnsHtml = call.turns.map(t => {
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
    if (setHtml('call-turns', turnsHtml)) {
      if (stick) scroller.scrollTop = scroller.scrollHeight;
      // Copy transcript (re-added only when the transcript was redrawn)
      el.style.position = 'relative';
      const tBtn = document.createElement('button');
      tBtn.className = 'copy-btn'; tBtn.textContent = '⎘ Copy Transcript';
      tBtn.onclick = () => {
        const lines = (call.turns || []).map(t => (t.role==='human'?'USER: ':'BOT:  ') + t.text).join('\\n');
        copyToClipboard(lines, tBtn);
      };
      el.insertBefore(tBtn, el.firstChild);
    }
  } catch(e) { console.error('refreshCallDetail error', e); }
}

async function endCallFn(csid) {
  if (!confirm('End this call?')) return;
  try {
    await fetch('/dashboard/calls/' + csid + '/end', { method: 'POST' });
    await loadCalls();
    await refreshCallDetail(csid);
  } catch(e) { console.error('endCall error', e); }
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

async function renderEventTimeline(csid, switched) {
  const wrap = document.getElementById('event-timeline');
  const box  = document.getElementById('ev-rows');
  if (!wrap || !box) return;
  wrap.style.display = 'block';
  // "Loading…" only when opening a call — not on every poll (#77)
  if (switched) setHtml('ev-rows', '<div style="color:#475569;font-size:11px;font-family:monospace;padding:4px 0">Loading events…</div>');
  try {
    const res  = await fetch('/dashboard/calls/' + csid + '/events');
    if (!res.ok) { setHtml('ev-rows', '<div style="color:#ef4444;font-size:11px">Events API error: ' + res.status + '</div>'); return; }
    const data = await res.json();
    const evs  = data.events || [];
    if (!evs.length) {
      setHtml('ev-rows', '<div style="color:#475569;font-size:11px;font-family:monospace;padding:4px 0">No events recorded yet.</div>');
      return;
    }
    const stick = switched || nearBottom(box);
    const evHtml = evs.map(ev => {
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
    if (!setHtml('ev-rows', evHtml)) return;  // unchanged — keep scroll + button
    if (stick) box.scrollTop = box.scrollHeight;

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
    setHtml('ev-rows', '<div style="color:#ef4444;font-size:11px;font-family:monospace">Event fetch error: ' + esc(String(e)) + '</div>');
    console.error('renderEventTimeline error:', e);
  }
}

// (#77) No separate 5 s detail poll: loadCalls() already refreshes the open
// call, and redraws now happen only when something changed.

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
    c.on('accept',()=>{ phSetStatus('Connected — keypad sends DTMF','#3b82f6'); phSetBtn('ph-btn-mute',true); phSetBtn('ph-btn-hang',true); phSetDtmf(true); phStartTimer(); phLog('Call connected','#22c55e'); });
    c.on('disconnect',()=>{ phCalling=false; phActiveCall=null; phSetStatus('Ready — click Call IVR','#22c55e');
      phSetBtn('ph-btn-call',true); phSetBtn('ph-btn-mute',false); phSetBtn('ph-btn-hang',false); phSetDtmf(false);
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
function phSetDtmf(on) {
  document.querySelectorAll('#ph-dtmf .dtmf-btn').forEach(b => { b.disabled = !on; });
}
function phSendDtmf(digit) {
  if (!phActiveCall) { phLog('No active call — DTMF ignored', '#f59e0b'); return; }
  try {
    phActiveCall.sendDigits(String(digit));
    phLog('DTMF ' + digit, '#38bdf8');
  } catch (e) {
    phLog('DTMF failed: ' + e.message, '#ef4444');
  }
}
document.addEventListener('keydown', (e) => {
  if (!phActiveCall) return;
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA')) return;
  const pane = document.getElementById('pane-phone');
  if (!pane || !pane.classList.contains('on')) return;
  if (/^[0-9*#]$/.test(e.key)) { e.preventDefault(); phSendDtmf(e.key); }
});

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
  LLM_PROVIDER:            {desc:'Which backend get_llm() uses: "groq" (default) or "bedrock".',                              best:'"groq" for local/dev. Bedrock only if AWS credentials and BEDROCK_MODEL are set.'},
  GROQ_MODEL:              {desc:'LLM for service nodes (policy, payment, loan, FAQ, beneficiary, name extract).',            best:'qwen/qwen3.8-27b — current default in config/settings.py.'},
  ROUTER_MODEL:            {desc:'LLM for single-word intent classification in the router node.',                             best:'qwen/qwen3.8-27b — same default as GROQ_MODEL right now.'},
  BEDROCK_MODEL:           {desc:'Used only when LLM_PROVIDER=bedrock.',                                                      best:'amazon.nova-micro-v1:0 unless you have a dedicated Bedrock IVR model.'},
  GROQ_API_KEY:            {desc:'Groq API key for LLM inference.',                                                           best:'Keep secret. Rotate every 90 days.'},
  OPENAI_EMBEDDING_MODEL:  {desc:'OpenAI embedding model for RAG vector search.',                                             best:'text-embedding-3-small — good accuracy/cost ratio for FAQ retrieval.'},
  // Realtime
  AUTH_MODE:               {desc:'"standard" = Twilio <Gather> + LangGraph auth_guard (current live webhook path). "realtime" = OpenAI Realtime over Media Streams.', best:'"standard" for the Gather webhook path used by the browser phone and PSTN today.'},
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
  TWILIO_TWIML_APP_SID:   {desc:'TwiML App SID for browser softphone (SDK client calls).',                                 best:'Voice URL = {TWILIO_BASE_URL}/webhook/voice (Cloudflare tunnel or ngrok).'},
  SYNC_TWILIO_WEBHOOKS:    {desc:'On startup, rewrite TwiML App + inbound DID Voice URL to TWILIO_BASE_URL/webhook/voice.', best:'true for local tunnels so Twilio tracks the current Cloudflare/ngrok URL.'},
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
  REDIS_URL:               {desc:'Redis URL for health/session. Not the LangGraph store.',                                best:'redis://localhost:6379/0 for local compose. Required for /health in this stack.'},
  DATABASE_URL:            {desc:'PostgreSQL for AsyncPostgresSaver (call graph state), call-history tables, and pgvector RAG.', best:'postgresql://cno:cno_pass@localhost:5432/cno_ivr with compose pgvector/pg16.'},
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
