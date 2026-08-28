
// ── Tab switching ─────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    document.querySelectorAll('.pane').forEach(x => x.classList.remove('on'));
    t.classList.add('on');
    document.getElementById('pane-' + t.dataset.pane).classList.add('on');
    if (t.dataset.pane === 'calls') loadCalls();
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

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>'); }

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
    return '<div class="sess-item' + (k===sid?' on':'') + '" onclick="loadSess(\''+k+'\')"><div class="sid">'
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
      const from = c.from_number.replace('client:','').replace('+1','');
      const active = c.status === 'active';
      const sel = c.call_sid === selCall ? ' on' : '';
      return '<div class="call-item'+sel+'" onclick="selCallFn(\''+c.call_sid+'\')"><div class="csid">'+c.call_sid.slice(0,24)+'</div>'
        + '<div class="cfrom">'+esc(from)+'</div>'
        + '<div class="cmeta"><span class="dot '+(active?'active':'ended')+'"></span>'+(active?'Active':'Ended')
        + ' &nbsp; '+c.turns.length+' turns &nbsp; '+c.started_at.slice(11,16)+'</div></div>';
    }).join('');
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

async function selCallFn(csid) {
  selCall = csid;
  loadCalls();
  try {
    const res = await fetch('/dashboard/calls/' + csid);
    const call = await res.json();
    const from = (call.from_number||'').replace('client:','');
    const status = call.status === 'active'
      ? '<span style="color:#22c55e;font-size:11px;margin-left:8px">● Live</span>'
      : '<span style="color:#64748b;font-size:11px;margin-left:8px">Ended ' + (call.ended_at||'').slice(11,19) + '</span>';
    const recBadge = call.recording_url
      ? ' <a href="'+call.recording_url+'" target="_blank" style="font-size:11px;color:#22c55e;margin-left:8px">▶ Recording</a>'
      : '';
    document.getElementById('call-hdr').innerHTML =
      esc(from) + ' — ' + (call.started_at||'').slice(0,19) + status + recBadge;

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
        const lines = (call.turns || []).map(t => (t.role==='human'?'USER: ':'BOT:  ') + t.text).join('\n');
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
  } catch(e) { console.error('selCall error', e); }
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
      }).join('\n');
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
  GROQ_MODEL:              {desc:'LLM for intent classification and all service nodes (policy, payment, loan, FAQ).',          best:'llama-3.3-70b-versatile — fastest + most accurate on Groq for IVR tasks.'},
  GROQ_API_KEY:            {desc:'Groq API key for LLM inference.',                                                           best:'Keep secret. Rotate every 90 days.'},
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
  // Backend
  CNO_API_BASE_URL:        {desc:'Base URL for the CNO backend API (party search, holding, payments).',                    best:'http://localhost:8001 for dev mock. Use production URL in prod.'},
  // Infra
  REDIS_URL:               {desc:'Redis URL for session state and LangGraph MemorySaver checkpointing.',                   best:'redis://localhost:6379/0 for dev. Redis Cloud or ElastiCache in prod.'},
  DATABASE_URL:            {desc:'PostgreSQL connection string for pgvector RAG knowledge base.',                           best:'Managed Postgres + pgvector in prod (Supabase, Neon, or RDS).'},
  ENVIRONMENT:             {desc:'Deployment environment tag. Controls logging and safety checks.',                         best:'"dev" locally. "prod" in production. Never run dev mode in prod.'},
  LOG_LEVEL:               {desc:'Logging verbosity.',                                                                      best:'"INFO" in dev and prod. "DEBUG" only for deep troubleshooting.'},
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
  valSpan.innerHTML = decodeURIComponent(origHtml.replace(/&#(\d+);/g,(_,n)=>String.fromCharCode(n)));
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
      + cfgSection('Infrastructure', d.infra, _cfgReadonly);
  } catch(e) {
    document.getElementById('cfg-body').innerHTML = '<div class="empty-msg" style="padding:40px">Failed to load config: '+esc(e.message)+'</div>';
  }
}

// Load config when tab clicked; allow refresh via button
document.querySelectorAll('.tab').forEach(t => {
  if (t.dataset.pane === 'cfg') t.addEventListener('click', loadConfig);
});

// ── DB Query ──────────────────────────────────────────────────
const DB_SAMPLES = [
  {
    name: 'Recent calls (20)',
    sql:  `SELECT call_sid, started_at,
       data->>'from_number'   AS from_number,
       data->>'status'        AS status,
       data->>'authenticated' AS authed,
       data->>'caller_persona' AS persona
FROM ivr_call_records
ORDER BY started_at DESC
LIMIT 20;`
  },
  {
    name: 'Authenticated calls today',
    sql:  `SELECT call_sid, started_at,
       data->>'caller_name'   AS caller_name,
       data->>'caller_persona' AS persona,
       data->>'from_number'   AS from_number
FROM ivr_call_records
WHERE data->>'authenticated' = 'true'
  AND started_at >= CURRENT_DATE
ORDER BY started_at DESC;`
  },
  {
    name: 'Intent breakdown (all time)',
    sql:  `SELECT intent, COUNT(*) AS count
FROM ivr_call_records,
     jsonb_array_elements_text(data->'intent_history') AS intent
GROUP BY intent
ORDER BY count DESC;`
  },
  {
    name: 'Full transcript for a call',
    sql:  `SELECT t->>'ts' AS ts, t->>'role' AS role,
       t->>'intent' AS intent, t->>'text' AS text
FROM ivr_call_records,
     jsonb_array_elements(data->'turns') AS t
WHERE call_sid = 'REPLACE_CALL_SID'
ORDER BY t->>'ts';`
  },
  {
    name: 'Calls that escalated',
    sql:  `SELECT call_sid, started_at, data->>'from_number' AS from_number
FROM ivr_call_records
WHERE data->'intent_history' ? 'escalate'
ORDER BY started_at DESC
LIMIT 50;`
  },
  {
    name: 'Auth failures / max attempts',
    sql:  `SELECT call_sid, started_at,
       data->>'auth_step'     AS auth_step,
       data->>'auth_attempts' AS attempts,
       data->>'from_number'   AS from_number
FROM ivr_call_records
WHERE data->>'auth_step' = 'failed'
   OR (data->>'auth_attempts')::int >= 3
ORDER BY started_at DESC
LIMIT 50;`
  },
  {
    name: 'Calls by persona',
    sql:  `SELECT data->>'caller_persona' AS persona, COUNT(*) AS count
FROM ivr_call_records
WHERE data->>'caller_persona' IS NOT NULL
  AND data->>'caller_persona' != ''
GROUP BY persona
ORDER BY count DESC;`
  },
  {
    name: 'Average turns per call',
    sql:  `SELECT ROUND(AVG(jsonb_array_length(data->'turns')), 2) AS avg_turns,
       MAX(jsonb_array_length(data->'turns'))                AS max_turns,
       COUNT(*)                                              AS total_calls
FROM ivr_call_records;`
  },
  {
    name: 'Calls with OTP / payment intent',
    sql:  `SELECT call_sid, started_at, data->>'caller_name' AS caller_name
FROM ivr_call_records
WHERE data->'intent_history' ? 'otp'
   OR data->'intent_history' ? 'payment'
ORDER BY started_at DESC
LIMIT 50;`
  },
  {
    name: 'Row count + date range',
    sql:  `SELECT COUNT(*)       AS total_calls,
       MIN(started_at) AS oldest,
       MAX(started_at) AS newest
FROM ivr_call_records;`
  },
];

function dbInitSamples() {
  const el = document.getElementById('db-sample-list');
  if (!el) return;
  el.innerHTML = DB_SAMPLES.map((s, i) =>
    `<div class="db-sample" onclick="dbUseSample(${i})">
      <span class="db-sample-name">${esc(s.name)}</span>
      ${esc(s.sql.split('\n')[0].slice(0, 60))}${s.sql.length > 60 ? '...' : ''}
    </div>`
  ).join('');
}

function dbUseSample(i) {
  document.getElementById('db-sql').value = DB_SAMPLES[i].sql;
  document.getElementById('db-status').textContent = 'Query loaded — click Run to execute.';
}

async function dbRun() {
  const sql = document.getElementById('db-sql').value.trim();
  if (!sql) return;
  const btn = document.getElementById('db-run-btn');
  const statusEl = document.getElementById('db-status');
  btn.disabled = true;
  statusEl.textContent = 'Running…';
  const t0 = Date.now();
  try {
    const res = await fetch('/dashboard/query', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sql}),
    });
    const d = await res.json();
    const ms = Date.now() - t0;
    if (!d.ok) {
      document.getElementById('db-results').innerHTML = `<div class="db-error">${esc(d.error)}</div>`;
      statusEl.textContent = 'Error.';
    } else {
      statusEl.textContent = `${d.count} row${d.count !== 1 ? 's' : ''} · ${ms}ms${d.count === 200 ? ' (capped at 200)' : ''}`;
      dbRenderTable(d.columns, d.rows);
    }
  } catch(e) {
    document.getElementById('db-results').innerHTML = `<div class="db-error">${esc(e.message)}</div>`;
    statusEl.textContent = 'Request failed.';
  } finally {
    btn.disabled = false;
  }
}

function dbRenderTable(cols, rows) {
  if (!cols.length) {
    document.getElementById('db-results').innerHTML = '<div class="db-empty">Query returned no rows.</div>';
    return;
  }
  const hdr = cols.map(c => `<th>${esc(c)}</th>`).join('');
  const body = rows.map(row =>
    '<tr>' + row.map(cell => {
      const s = cell === null ? '<em style="color:#475569">NULL</em>' : esc(typeof cell === 'object' ? JSON.stringify(cell, null, 2) : String(cell));
      return `<td title="${typeof cell === 'string' ? esc(cell) : ''}">${s}</td>`;
    }).join('') + '</tr>'
  ).join('');
  document.getElementById('db-results').innerHTML =
    `<table class="db-tbl"><thead><tr>${hdr}</tr></thead><tbody>${body}</tbody></table>`;
}

function dbClear() {
  document.getElementById('db-sql').value = '';
  document.getElementById('db-results').innerHTML = '<div class="db-empty">No results yet.</div>';
  document.getElementById('db-status').textContent = 'Enter a SELECT query and click Run. Results capped at 200 rows.';
}

// Ctrl+Enter to run
document.getElementById('db-sql').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); dbRun(); }
});

// Init samples on tab click
document.querySelectorAll('.tab').forEach(t => {
  if (t.dataset.pane === 'db') t.addEventListener('click', () => { dbInitSamples(); });
});

// ── Init ──────────────────────────────────────────────────────
connectLogs();
loadCalls();
setInterval(loadCalls, 10000);
initChats();
