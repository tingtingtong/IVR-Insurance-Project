"""
Browser softphone — Twilio Voice JS SDK client for testing without a physical phone.

Endpoints:
  GET /client        — serves the softphone HTML page
  GET /client/token  — returns a short-lived Twilio Access Token (VoiceGrant)

Setup (one-time in Twilio Console):
  1. Account → API Keys & Tokens → Create API Key (Standard)
     → save Key SID as TWILIO_API_KEY, Secret as TWILIO_API_SECRET
  2. Voice → TwiML Apps → Create → set Voice URL to https://<your-ngrok>/webhook/voice
     → save the TwiML App SID as TWILIO_TWIML_APP_SID
"""
import os
import asyncio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from config import settings
import log_bus
from webhooks.security import require_dashboard_auth

_SDK_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "twilio.min.js")

router = APIRouter(
    prefix="/client",
    tags=["browser-client"],
    dependencies=[Depends(require_dashboard_auth)],
)


@router.get("/twilio.min.js")
async def twilio_sdk():
    """Serve the locally bundled Twilio Voice JS SDK — avoids external CDN blocks."""
    return FileResponse(_SDK_PATH, media_type="application/javascript")


@router.get("/token")
async def get_token():
    """Generate a Twilio Access Token for the browser Voice SDK."""
    import structlog, traceback
    log = structlog.get_logger()
    try:
        token = AccessToken(
            settings.twilio_account_sid,
            settings.twilio_api_key,
            settings.twilio_api_secret,
            identity="browser_tester",
            ttl=3600,
        )
        token.add_grant(VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=True,
        ))
        jwt = token.to_jwt()
        return JSONResponse({"token": jwt if isinstance(jwt, str) else jwt.decode()})
    except Exception as e:
        log.error("token_error", error=str(e), trace=traceback.format_exc())
        raise


@router.get("", response_class=HTMLResponse)
async def softphone():
    """Serve the browser softphone UI."""
    return _HTML


# ── Softphone HTML ────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>insuranceCompany IVR — Browser Test Client</title>
<script src="/client/twilio.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f172a; color: #e2e8f0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }
  .card {
    background: #1e293b; border-radius: 16px; padding: 40px;
    width: 340px; box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  h1 { font-size: 18px; font-weight: 600; margin-bottom: 4px; color: #f1f5f9; }
  .subtitle { font-size: 12px; color: #64748b; margin-bottom: 28px; }
  .status-bar {
    background: #0f172a; border-radius: 8px; padding: 10px 14px;
    font-size: 13px; color: #94a3b8; margin-bottom: 24px;
    display: flex; align-items: center; gap: 8px;
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%; background: #475569;
    flex-shrink: 0; transition: background 0.3s;
  }
  .dot.ready    { background: #22c55e; }
  .dot.calling  { background: #f59e0b; animation: pulse 1s infinite; }
  .dot.active   { background: #3b82f6; }
  .dot.error    { background: #ef4444; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  .btn {
    width: 100%; padding: 14px; border-radius: 10px; border: none;
    font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.2s;
    margin-bottom: 10px;
  }
  .btn:disabled { opacity: 0.35; cursor: not-allowed; }
  .btn-call  { background: #22c55e; color: #fff; }
  .btn-call:hover:not(:disabled)  { background: #16a34a; }
  .btn-hang  { background: #ef4444; color: #fff; }
  .btn-hang:hover:not(:disabled)  { background: #dc2626; }
  .btn-mute  { background: #334155; color: #e2e8f0; }
  .btn-mute:hover:not(:disabled)  { background: #475569; }
  .btn-mute.muted { background: #f59e0b; color: #000; }
  .log {
    background: #0f172a; border-radius: 8px; padding: 12px;
    font-size: 11px; color: #475569; height: 110px; overflow-y: auto;
    font-family: monospace; margin-top: 10px;
  }
  .log p { margin-bottom: 3px; }
  .log p.info  { color: #94a3b8; }
  .log p.ok    { color: #22c55e; }
  .log p.warn  { color: #f59e0b; }
  .log p.err   { color: #ef4444; }
  .timer { text-align: center; font-size: 28px; font-weight: 300;
           color: #3b82f6; margin: 16px 0; letter-spacing: 2px; display: none; }
</style>
</head>
<body>
<div class="card">
  <h1>insuranceCompany IVR Test Client</h1>
  <p class="subtitle">Browser softphone — no phone required</p>

  <div class="status-bar">
    <div class="dot" id="dot"></div>
    <span id="status-text">Initialising...</span>
  </div>

  <div class="timer" id="timer">0:00</div>

  <button class="btn btn-call" id="btn-call" disabled onclick="handleCallClick()">Call IVR</button>
  <button class="btn btn-mute" id="btn-mute" disabled onclick="toggleMute()">Mute</button>
  <button class="btn btn-hang" id="btn-hang" disabled onclick="hangUp()">Hang Up</button>

  <div class="log" id="log"></div>
</div>

<script>
let device, call, timerInterval, seconds = 0, muted = false, calling = false;

function log(msg, cls = "info") {
  const d = document.getElementById("log");
  const p = document.createElement("p");
  p.className = cls;
  const t = new Date().toLocaleTimeString("en-GB", {hour:"2-digit",minute:"2-digit",second:"2-digit"});
  p.textContent = t + "  " + msg;
  d.prepend(p);
}

function setStatus(text, dotClass) {
  document.getElementById("status-text").textContent = text;
  const dot = document.getElementById("dot");
  dot.className = "dot " + dotClass;
}

function setButtons(calling) {
  document.getElementById("btn-call").disabled =  calling;
  document.getElementById("btn-mute").disabled = !calling;
  document.getElementById("btn-hang").disabled = !calling;
}

function startTimer() {
  seconds = 0;
  const el = document.getElementById("timer");
  el.style.display = "block";
  timerInterval = setInterval(() => {
    seconds++;
    const m = Math.floor(seconds/60), s = seconds%60;
    el.textContent = m + ":" + String(s).padStart(2,"0");
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
  document.getElementById("timer").style.display = "none";
}

async function init() {
  try {
    log("Fetching access token...");
    const res = await fetch("/client/token");
    const { token } = await res.json();
    device = new Twilio.Device(token, { logLevel: 1, codecPreferences: ["opus","pcmu"] });

    device.on("registered",   () => { setStatus("Ready — click Call IVR", "ready");
                                       if (!calling) document.getElementById("btn-call").disabled = false;
                                       log("Device registered", "ok"); });
    device.on("unregistered", () => { log("Device unregistered — re-registering...", "warn");
                                       setStatus("Reconnecting...", "calling");
                                       setTimeout(() => device.register(), 2000); });
    device.on("error",        (e)  => { setStatus("Error: " + e.message, "error");
                                        log("Device error: " + e.message, "err");
                                        if (e.code === 31009 || e.code === 31005) {
                                          log("Transport error — re-registering in 3s...", "warn");
                                          setTimeout(() => { try { device.register(); } catch(_){} }, 3000);
                                        } });
    device.on("incoming",     (c)  => { log("Incoming call from " + c.parameters.From, "warn"); });
    device.on("tokenWillExpire", async () => {
      const r = await fetch("/client/token");
      const { token: t } = await r.json();
      device.updateToken(t);
      log("Token refreshed", "ok");
    });

    device.register();
    setStatus("Registering...", "calling");
  } catch(e) {
    setStatus("Init failed", "error");
    log("Init error: " + e.message, "err");
  }
}

function handleCallClick() {
  if (calling || !device) return;
  calling = true;
  document.getElementById("btn-call").disabled = true;
  startCall();
}

async function startCall() {
  try {
    setStatus("Connecting...", "calling");
    log("Placing call to IVR...");
    call = await device.connect({ params: {} });

    call.on("accept",     () => { setStatus("Connected", "active");
                                   setButtons(true); startTimer();
                                   log("Call connected", "ok"); });
    call.on("disconnect", () => { calling = false;
                                   setStatus("Ready — click Call IVR", "ready");
                                   setButtons(false); stopTimer(); muted = false;
                                   document.getElementById("btn-mute").classList.remove("muted");
                                   document.getElementById("btn-mute").textContent = "Mute";
                                   log("Call ended"); });
    call.on("error",      (e) => { calling = false; log("Call error: " + e.message, "err"); });
  } catch(e) {
    calling = false;
    setStatus("Call failed", "error");
    log("Call failed: " + e.message, "err");
    document.getElementById("btn-call").disabled = false;
  }
}

function toggleMute() {
  if (!call) return;
  muted = !muted;
  call.mute(muted);
  const btn = document.getElementById("btn-mute");
  btn.textContent = muted ? "Unmute" : "Mute";
  btn.classList.toggle("muted", muted);
  log(muted ? "Muted" : "Unmuted", "warn");
}

function hangUp() {
  if (call) call.disconnect();
}

init();
</script>
</body>
</html>"""


# ── Live log viewer (SSE) ─────────────────────────────────────────────────────

@router.get("/logs/stream")
async def log_stream(request: Request):
    """SSE endpoint — streams log lines to the browser in real-time."""
    q = log_bus.subscribe()

    async def event_gen():
        # Send buffered history first so the page isn't blank on connect
        for line in log_bus.get_history():
            yield f"data: {line}\n\n"
        # Then stream live lines as they arrive
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"   # keepalive — prevents proxy timeout
        finally:
            log_bus.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/logs", response_class=HTMLResponse)
async def log_viewer():
    return _LOGS_HTML


_LOGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>IVR Live Logs</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f172a; color: #e2e8f0; font-family: monospace;
         display: flex; flex-direction: column; height: 100vh; }
  .toolbar {
    background: #1e293b; padding: 10px 16px; display: flex;
    align-items: center; gap: 12px; border-bottom: 1px solid #334155;
    flex-shrink: 0;
  }
  h1 { font-size: 14px; color: #94a3b8; font-weight: 600; flex: 1; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #475569;
         flex-shrink: 0; transition: background .3s; }
  .dot.live { background: #22c55e; animation: pulse 2s infinite; }
  .dot.err  { background: #ef4444; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .btn { background: #334155; color: #e2e8f0; border: none; border-radius: 6px;
         padding: 5px 12px; font-size: 12px; cursor: pointer; }
  .btn:hover { background: #475569; }
  .btn.active { background: #3b82f6; }
  #log { flex: 1; overflow-y: auto; padding: 12px 16px; font-size: 12px; line-height: 1.7; }
  .ll { display: block; white-space: pre-wrap; word-break: break-all; }
  .ll-info   { color: #94a3b8; }
  .ll-ok     { color: #22c55e; }
  .ll-warn   { color: #f59e0b; }
  .ll-error  { color: #ef4444; }
  .ll-http   { color: #64748b; }
  .ll-speech { color: #38bdf8; font-weight: bold; }
  .ll-call   { color: #a78bfa; }
  .ll-auth   { color: #fb923c; }
  .ll-graph  { color: #34d399; }
</style>
</head>
<body>
<div class="toolbar">
  <div class="dot" id="dot"></div>
  <h1>IVR Live Logs</h1>
  <span id="cnt" style="font-size:11px;color:#475569">0 lines</span>
  <button class="btn active" id="btn-scroll" onclick="toggleScroll()">Auto-scroll ON</button>
  <button class="btn" onclick="clearView()">Clear</button>
  <a href="/client" style="text-decoration:none">
    <button class="btn">&#8592; Softphone</button>
  </a>
</div>
<div id="log"></div>

<script>
let autoScroll = true;
let lineCount = 0;

function toggleScroll() {
  autoScroll = !autoScroll;
  const btn = document.getElementById('btn-scroll');
  btn.textContent = 'Auto-scroll ' + (autoScroll ? 'ON' : 'OFF');
  btn.className = 'btn' + (autoScroll ? ' active' : '');
}

function clearView() {
  document.getElementById('log').innerHTML = '';
  lineCount = 0;
  document.getElementById('cnt').textContent = '0 lines';
}

function classify(line) {
  if (line.includes('speech_received'))                              return 'll-speech';
  if (line.includes('call_started') || line.includes('call_ended')) return 'll-call';
  if (line.includes('graph_') || line.includes('node_'))            return 'll-graph';
  if (line.includes('auth') || line.includes('access_token'))       return 'll-auth';
  if (line.includes('[error') || line.includes('ERROR') ||
      line.includes('Traceback') || line.includes('Exception'))     return 'll-error';
  if (line.includes('[warning') || line.includes('WARNING') ||
      line.includes('redis_unavail'))                               return 'll-warn';
  if (line.includes('HTTP/1.1 2'))                                  return 'll-http';
  if (line.includes('[info') || line.includes('cno_ivr'))           return 'll-ok';
  return 'll-info';
}

function appendLine(text) {
  const el = document.getElementById('log');
  const span = document.createElement('span');
  span.className = 'll ' + classify(text);
  span.textContent = text;
  el.appendChild(span);
  lineCount++;
  document.getElementById('cnt').textContent = lineCount + ' lines';
  if (autoScroll) el.scrollTop = el.scrollHeight;
}

function connect() {
  const dot = document.getElementById('dot');
  dot.className = 'dot';

  const es = new EventSource('/client/logs/stream');

  es.onopen = () => { dot.className = 'dot live'; };

  es.onmessage = (e) => {
    if (e.data && e.data.trim()) appendLine(e.data);
  };

  es.onerror = () => {
    dot.className = 'dot err';
    es.close();
    setTimeout(connect, 3000);
  };
}

connect();
</script>
</body>
</html>"""
