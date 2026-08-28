"""
Generate HLD and LLD PDFs for cno_ivr project.
Usage: python generate_docs.py
Outputs: cno_ivr_HLD.pdf, cno_ivr_LLD.pdf
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, ListFlowable, ListItem,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

# ── Palette ──────────────────────────────────────────────────────────────────
BLUE_DARK   = colors.HexColor("#1A3A5C")
BLUE_MID    = colors.HexColor("#2E6EA6")
BLUE_LIGHT  = colors.HexColor("#D6E8F7")
GREY_LIGHT  = colors.HexColor("#F4F6F8")
GREY_MED    = colors.HexColor("#BDC3C7")
ORANGE      = colors.HexColor("#E67E22")
GREEN       = colors.HexColor("#27AE60")
RED         = colors.HexColor("#C0392B")
WHITE       = colors.white
BLACK       = colors.black

W, H = A4


def _styles():
    base = getSampleStyleSheet()

    def add(name, **kw):
        if name not in base:
            base.add(ParagraphStyle(name=name, **kw))
        return base[name]

    add("DocTitle",    fontSize=26, leading=32, textColor=WHITE,     alignment=TA_CENTER, fontName="Helvetica-Bold")
    add("DocSubtitle", fontSize=13, leading=18, textColor=BLUE_LIGHT, alignment=TA_CENTER, fontName="Helvetica")
    add("H1",          fontSize=16, leading=22, textColor=BLUE_DARK,  spaceBefore=14, spaceAfter=4,  fontName="Helvetica-Bold")
    add("H2",          fontSize=13, leading=18, textColor=BLUE_MID,   spaceBefore=10, spaceAfter=3,  fontName="Helvetica-Bold")
    add("H3",          fontSize=11, leading=15, textColor=BLUE_DARK,  spaceBefore=8,  spaceAfter=2,  fontName="Helvetica-Bold")
    add("Body",        fontSize=10, leading=15, textColor=BLACK,      spaceBefore=3,  spaceAfter=3,  fontName="Helvetica",     alignment=TA_JUSTIFY)
    add("Code",        fontSize=9,  leading=13, textColor=colors.HexColor("#2C3E50"), fontName="Courier",
        backColor=GREY_LIGHT, borderPadding=(4, 6, 4, 6))
    add("BulletItem",  fontSize=10, leading=14, textColor=BLACK,      fontName="Helvetica",
        leftIndent=16, bulletIndent=6, spaceBefore=1)
    add("TableHeader", fontSize=10, leading=13, textColor=WHITE,      fontName="Helvetica-Bold", alignment=TA_CENTER)
    add("TableCell",   fontSize=9,  leading=13, textColor=BLACK,      fontName="Helvetica")
    add("Note",        fontSize=9,  leading=13, textColor=colors.HexColor("#7F8C8D"), fontName="Helvetica-Oblique",
        leftIndent=10, borderPadding=4)
    add("Caption",     fontSize=8,  leading=11, textColor=GREY_MED,   alignment=TA_CENTER, fontName="Helvetica-Oblique")
    return base


def _cover(title, subtitle, version, date):
    """Fancy cover-page flowables."""
    s = _styles()
    items = []
    items.append(Spacer(1, 3 * cm))

    # Title box
    title_table = Table(
        [[Paragraph(title,    s["DocTitle"])],
         [Paragraph(subtitle, s["DocSubtitle"])]],
        colWidths=[W - 4 * cm],
    )
    title_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), BLUE_DARK),
        ("ROUNDEDCORNERS", [8]),
        ("TOPPADDING",   (0, 0), (-1, -1), 20),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 20),
        ("LEFTPADDING",  (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
    ]))
    items.append(title_table)
    items.append(Spacer(1, 1.2 * cm))

    meta = Table(
        [["Project", "insuranceCompany IVR — Agentic Voice IVR for insuranceCompany"],
         ["Version", version],
         ["Date",    date],
         ["Client",  "insuranceCompany (insuranceCompany)"],
         ["Stack",   "Python · FastAPI · LangGraph · Deepgram · ElevenLabs · Twilio"]],
        colWidths=[3.2 * cm, W - 7.2 * cm],
    )
    meta.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (0, -1), BLUE_MID),
        ("BACKGROUND",   (1, 0), (1, -1), GREY_LIGHT),
        ("TEXTCOLOR",    (0, 0), (0, -1), WHITE),
        ("TEXTCOLOR",    (1, 0), (1, -1), BLACK),
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",     (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 0), (-1, -1), 10),
        ("ALIGN",        (0, 0), (0, -1), "RIGHT"),
        ("ALIGN",        (1, 0), (1, -1), "LEFT"),
        ("TOPPADDING",   (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 7),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS",(0, 0),(-1,-1),[GREY_LIGHT, WHITE]),
        ("BOX",          (0, 0), (-1, -1), 0.5, GREY_MED),
        ("INNERGRID",    (0, 0), (-1, -1), 0.3, GREY_MED),
    ]))
    items.append(meta)
    items.append(PageBreak())
    return items


def _section(title, s):
    items = [
        HRFlowable(width="100%", thickness=2, color=BLUE_MID, spaceAfter=4),
        Paragraph(title, s["H1"]),
    ]
    return items


def _subsection(title, s):
    return [Paragraph(title, s["H2"])]


def _subsubsection(title, s):
    return [Paragraph(title, s["H3"])]


def _p(text, s):
    return [Paragraph(text, s["Body"])]


def _bullets(items_list, s):
    return [ListFlowable(
        [ListItem(Paragraph(i, s["BulletItem"]), bulletColor=BLUE_MID, leftIndent=18) for i in items_list],
        bulletType="bullet", bulletFontSize=8,
    )]


def _table(headers, rows, col_widths=None):
    s = _styles()
    data = [[Paragraph(h, s["TableHeader"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), s["TableCell"]) for c in row])
    if col_widths is None:
        col_widths = [(W - 4 * cm) / len(headers)] * len(headers)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  BLUE_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [GREY_LIGHT, WHITE]),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ALIGN",         (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("BOX",           (0, 0), (-1, -1), 0.5, GREY_MED),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, GREY_MED),
    ]))
    return [t]


def _code(text, s):
    lines = text.strip().split("\n")
    rows = [[Paragraph(ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), s["Code"])]
            for ln in lines]
    t = Table(rows, colWidths=[W - 4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREY_LIGHT),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("BOX",           (0, 0), (-1, -1), 0.5, GREY_MED),
    ]))
    return [t, Spacer(1, 4)]


# ══════════════════════════════════════════════════════════════════════════════
# HLD
# ══════════════════════════════════════════════════════════════════════════════
def build_hld():
    s = _styles()
    story = []

    story += _cover(
        "insuranceCompany IVR",
        "High Level Design (HLD)",
        "v1.0",
        "June 2026",
    )

    # ── 1. Executive Summary ────────────────────────────────────────────────
    story += _section("1. Executive Summary", s)
    story += _p(
        "The <b>insuranceCompany IVR</b> is an Agentic Voice Interactive Voice Response system built for "
        "<b>insuranceCompany (insuranceCompany)</b>. It replaces a legacy deterministic Amelia "
        "BPM flow with a fully agentic, real-time voice pipeline capable of natural, "
        "context-aware conversations over standard PSTN phone calls.", s)
    story += _p(
        "Callers interact with the system using natural speech. The system authenticates them, "
        "understands intent, queries backend APIs, and responds in a synthesized human voice — "
        "all in under 2 seconds end-to-end. Barge-in (interrupting the IVR mid-sentence) is "
        "fully supported.", s)

    story += _subsection("Key Capabilities", s)
    story += _bullets([
        "Real-time streaming Speech-to-Text via Deepgram Nova-2",
        "LLM-powered intent routing and natural language response generation (OpenAI GPT)",
        "Stateful multi-turn conversation using LangGraph state machine",
        "Streaming Text-to-Speech via ElevenLabs Turbo v2",
        "Barge-in detection using WebRTC VAD (60 ms detection latency)",
        "PCI-compliant one-time payment via Twilio DTMF (card data never passes through LLM)",
        "ACH authorization with verbatim compliance scripts",
        "Privacy opt-out (GLBA), document retrieval, contact/beneficiary changes",
        "Live agent escalation with reason handoff",
    ], s)

    story.append(Spacer(1, 0.3 * cm))

    # ── 2. System Context ────────────────────────────────────────────────────
    story += _section("2. System Context", s)
    story += _p(
        "The diagram below illustrates the system boundaries and external actors "
        "that interact with the insuranceCompany IVR.", s)

    ctx_rows = [
        ["Caller (PSTN)",         "External", "Places a phone call via standard telephone network"],
        ["Twilio",                "External", "Telephony platform — bridges PSTN to WebSocket Media Stream"],
        ["Deepgram",              "External", "Cloud STT — receives mulaw audio, returns real-time transcripts"],
        ["ElevenLabs",            "External", "Cloud TTS — receives text, streams back PCM audio"],
        ["OpenAI (GPT)",          "External", "LLM — powers intent routing and response generation"],
        ["insuranceCompany Backend APIs",      "External", "Policy, payment, loan, document, beneficiary REST APIs"],
        ["Redis",                 "Internal", "Session state persistence (per call_sid)"],
        ["PostgreSQL + pgvector", "Internal", "RAG vector store for FAQ knowledge base"],
        ["insuranceCompany IVR Application",   "Internal", "Core FastAPI/LangGraph application (this system)"],
    ]
    story += _table(
        ["Actor / System", "Boundary", "Role"],
        ctx_rows,
        col_widths=[4.5 * cm, 3 * cm, W - 4 * cm - 7.5 * cm],
    )
    story.append(Spacer(1, 0.3 * cm))

    # ── 3. Architecture Overview ─────────────────────────────────────────────
    story += _section("3. Architecture Overview", s)
    story += _p(
        "The insuranceCompany IVR uses a <b>real-time bidirectional WebSocket pipeline</b> to handle phone audio. "
        "Each incoming call triggers a new WebSocket session. All audio processing and LLM calls "
        "are asynchronous (Python asyncio) to meet sub-2-second response latency targets.", s)

    story += _subsection("3.1 High-Level Data Flow", s)
    flow_rows = [
        ["1", "PSTN → Twilio",           "Caller dials insuranceCompany number; Twilio accepts call"],
        ["2", "Twilio → WebSocket",       "Twilio streams mulaw 8kHz audio to /stream endpoint"],
        ["3", "WebSocket → Deepgram",     "Raw audio bytes forwarded to Deepgram live STT connection"],
        ["4", "Deepgram → App",           "Partial + final transcripts returned via callback"],
        ["5", "App → LangGraph",          "Final transcript fed into graph as HumanMessage"],
        ["6", "Router Node",              "GPT classifies caller utterance into one of 12 intents"],
        ["7", "Intent Node",              "Relevant agent node executes: calls APIs, generates TTS text"],
        ["8", "TTS Normalizer",           "Raw LLM response cleaned/formatted for voice output"],
        ["9", "App → ElevenLabs",         "Normalized text streamed to ElevenLabs Turbo v2 TTS"],
        ["10","ElevenLabs → WebSocket",   "PCM audio converted to mulaw, streamed back to Twilio"],
        ["11","Twilio → Caller",          "Audio played to caller over PSTN"],
        ["12","VAD (parallel)",           "WebRTC VAD monitors inbound audio during TTS playback; fires barge-in"],
    ]
    story += _table(
        ["Step", "Segment", "Description"],
        flow_rows,
        col_widths=[1 * cm, 4.5 * cm, W - 4 * cm - 5.5 * cm],
    )

    story += _subsection("3.2 Architectural Layers", s)
    story += _bullets([
        "<b>Transport Layer</b> — Twilio Media Streams WebSocket; mulaw G.711 8kHz; 20ms frames",
        "<b>Audio Processing Layer</b> — Deepgram STT (inbound) + ElevenLabs TTS (outbound) + WebRTC VAD (barge-in)",
        "<b>Intelligence Layer</b> — LangGraph StateGraph with 12 nodes; OpenAI GPT for routing and response",
        "<b>Integration Layer</b> — aiohttp async HTTP client calling insuranceCompany backend REST APIs",
        "<b>Persistence Layer</b> — MemorySaver (dev) / RedisCheckpointer (prod) for call state",
        "<b>Knowledge Layer</b> — PostgreSQL + pgvector for FAQ RAG retrieval",
    ], s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 4. Component Architecture ────────────────────────────────────────────
    story += _section("4. Component Architecture", s)

    comp_rows = [
        ["FastAPI App (main.py)",    "Web framework",   "Hosts /stream (WebSocket) and /health endpoints; entry point"],
        ["WebSocket Handler",        "Transport",       "Handles Twilio start/media/stop events; orchestrates pipeline"],
        ["STTService",               "Audio",           "Deepgram asynclive v1; Nova-2 model; mulaw 8kHz; interim results"],
        ["TTSService",               "Audio",           "ElevenLabs Turbo v2; pcm_8000 output → mulaw conversion via audioop"],
        ["VADService",               "Audio",           "WebRTC VAD aggressiveness=3; fires barge-in at 60ms sustained speech"],
        ["TTSNormalizer",            "Utility",         "17-step text normalization (currency, dates, policy numbers, abbreviations)"],
        ["LangGraph (cno_graph)",    "Intelligence",    "Compiled StateGraph; 12 nodes; MemorySaver per call_sid"],
        ["Router Node",              "Intelligence",    "GPT-based intent classifier; 12 intent classes; token-efficient prompt"],
        ["Auth Node",                "Intelligence",    "PII collection loop; verify_caller tool; 3-failure escalation rule"],
        ["Policy Node",              "Intelligence",    "Calls HOLDING_INQUIRY API; returns status, premium, paid-to-date"],
        ["Payment Node",             "Intelligence",    "Calls PAYMENT_HISTORY API; enforces G4 disclosure (24-48h posting)"],
        ["OTP Node",                 "Intelligence",    "One-time payment; card via DTMF; ACH authorization script"],
        ["Loan Node",                "Intelligence",    "Calls LOAN_INQUIRY API; loan balance, interest, payoff"],
        ["Beneficiary Node",         "Intelligence",    "Beneficiary info retrieval"],
        ["Contact Node",             "Intelligence",    "Address / phone update"],
        ["Document Node",            "Intelligence",    "Document retrieval; mail-only (no email delivery)"],
        ["Privacy Node",             "Intelligence",    "GLBA opt-out; reads verbatim privacy script"],
        ["FAQ Node",                 "Intelligence",    "RAG-based FAQ; pgvector similarity search"],
        ["Escalation Node",          "Intelligence",    "Sets transfer_to; reads farewell script; exits graph"],
        ["insuranceCompany API Tools",            "Integration",     "holding_inquiry, payment_history, loan_inquiry, process_card_payment, process_ach_payment"],
        ["Redis",                    "Persistence",     "Graph checkpointer (prod); session TTL = call duration + 5min"],
        ["PostgreSQL + pgvector",    "Persistence",     "FAQ knowledge base; cosine similarity embeddings"],
    ]
    story += _table(
        ["Component", "Layer", "Responsibility"],
        comp_rows,
        col_widths=[4.2 * cm, 2.8 * cm, W - 4 * cm - 7 * cm],
    )

    story.append(PageBreak())

    # ── 5. Tech Stack ─────────────────────────────────────────────────────────
    story += _section("5. Technology Stack", s)

    stack_rows = [
        ["Python 3.11",          "Runtime",         "Async-native; audioop built-in"],
        ["FastAPI 0.115",        "Web Framework",   "ASGI; WebSocket support; Uvicorn server"],
        ["LangGraph 0.2.66",     "Agent Framework", "StateGraph + conditional edges + checkpointing"],
        ["LangChain 0.3.14",     "LLM Abstraction", "ChatOpenAI wrapper; prompt templates"],
        ["OpenAI 1.54.0",        "LLM Provider",    "GPT — intent routing + response generation"],
        ["Deepgram SDK 3.7.3",   "STT",             "Nova-2; streaming; mulaw 8kHz; 300ms endpointing"],
        ["ElevenLabs 1.9.0",     "TTS",             "Turbo v2; PCM 8kHz → mulaw; stability 0.55"],
        ["webrtcvad-wheels 2.0", "VAD",             "WebRTC VAD; aggressiveness 3; 20ms frames"],
        ["Twilio 9.3.5",         "Telephony",       "Media Streams WebSocket; DTMF for PCI card capture"],
        ["Redis 5.1.1",          "State Store",     "hiredis; LangGraph RedisCheckpointer (prod)"],
        ["PostgreSQL + pgvector","Vector DB",        "FAQ embeddings; cosine similarity"],
        ["aiohttp 3.10",         "HTTP Client",     "Async; 10s timeout for insuranceCompany APIs"],
        ["PyJWT 2.13",           "Security",        "JWT generation for payment API (5-min expiry)"],
        ["pydantic-settings",    "Config",          "Env var validation; .env support"],
        ["structlog 24.4",       "Logging",         "Structured JSON logging"],
        ["Docker",               "Container",       "python:3.11-slim; port 8000"],
    ]
    story += _table(
        ["Technology", "Category", "Usage"],
        stack_rows,
        col_widths=[3.8 * cm, 3.5 * cm, W - 4 * cm - 7.3 * cm],
    )

    story.append(Spacer(1, 0.3 * cm))

    # ── 6. LangGraph Agent Design ────────────────────────────────────────────
    story += _section("6. LangGraph Agent Design", s)
    story += _p(
        "The core intelligence is a <b>LangGraph StateGraph</b> compiled with a MemorySaver "
        "checkpointer. Each phone call is identified by its Twilio <i>call_sid</i>, which serves "
        "as the LangGraph <i>thread_id</i>. This allows the graph to persist state across multiple "
        "caller turns within the same call.", s)

    story += _subsection("6.1 Node Inventory", s)
    node_rows = [
        ["router",      "Entry",       "Classifies every utterance into 12 intents using GPT + ROUTER_PROMPT"],
        ["auth",        "Core",        "Collects PII (phone, policy#, DOB, name, zip); calls verify_caller; 3-fail escalation"],
        ["policy",      "Service",     "Policy status, premium, paid-to-date via HOLDING_INQUIRY API"],
        ["payment",     "Service",     "Last 3 transactions via PAYMENT_HISTORY; G4 24-48h disclosure"],
        ["otp",         "Service",     "One-time payment; card (DTMF/JWT) or ACH (auth script)"],
        ["loan",        "Service",     "Loan balance, interest, payoff via LOAN_INQUIRY"],
        ["beneficiary", "Service",     "Beneficiary information retrieval"],
        ["contact",     "Service",     "Update address or phone number"],
        ["document",    "Service",     "Request documents; mail-only delivery enforced"],
        ["privacy",     "Service",     "GLBA opt-out (affiliated / non-affiliated / both)"],
        ["faq",         "Knowledge",   "RAG-based FAQ; pgvector similarity search"],
        ["escalation",  "Termination", "Sets transfer_to; closes call; handles: auth failure, unsupported request, caller request"],
    ]
    story += _table(
        ["Node", "Type", "Responsibility"],
        node_rows,
        col_widths=[2.8 * cm, 3 * cm, W - 4 * cm - 5.8 * cm],
    )

    story += _subsection("6.2 Routing Logic", s)
    story += _bullets([
        "<b>START → router</b>: every turn begins at the router node",
        "<b>router → intent node</b>: conditional edge driven by current_intent in state",
        "<b>auth → router</b>: on auth_step='complete', re-routes to the originally intended node",
        "<b>auth → escalation</b>: on auth_step='failed' (3 consecutive failures)",
        "<b>owner_change → escalation</b>: owner/beneficiary change not supported in IVR; escalates",
        "<b>all service nodes → END</b>: after responding, wait for next turn (WebSocket drives re-entry)",
    ], s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 7. Security & Compliance ─────────────────────────────────────────────
    story += _section("7. Security & Compliance", s)

    sec_rows = [
        ["PCI Compliance",    "Card number, expiry, CVV collected via Twilio DTMF only. Never passed through LLM context. JWT-authenticated payment API."],
        ["ACH Authorization", "Verbatim ACH script read to caller before processing. get_ach_script() returns exact compliant text; LLM cannot paraphrase."],
        ["GLBA Privacy",      "get_privacy_script() returns exact GLBA opt-out text. Choices: affiliated / non-affiliated / both."],
        ["Document Delivery", "Documents mailed or faxed to address on file only. Email delivery explicitly refused."],
        ["Prepaid Cards",     "Cards without cardholder name rejected with restriction explanation."],
        ["Payment Posting",   "Always disclosed: 24-48h for online payments; 7-10 business days for mailed."],
        ["Relationship Restr.","get_relationship_restriction_script() returns exact restriction language."],
        ["Auth Lockout",      "3 consecutive PII failures → immediate escalation to live agent. No retry."],
        ["JWT Expiry",        "Payment JWT expires in 300 seconds (5 minutes). Per-transaction generation."],
        ["TLS",               "All external API calls (Deepgram, ElevenLabs, OpenAI, insuranceCompany APIs) over HTTPS/WSS."],
    ]
    story += _table(
        ["Control", "Implementation"],
        sec_rows,
        col_widths=[4 * cm, W - 4 * cm - 4 * cm],
    )

    story.append(Spacer(1, 0.3 * cm))

    # ── 8. Deployment Architecture ───────────────────────────────────────────
    story += _section("8. Deployment Architecture", s)

    story += _subsection("8.1 Container", s)
    story += _p("The application is containerized with Docker (python:3.11-slim base). "
                "Uvicorn serves the ASGI app on port 8000. The container is stateless; "
                "all call state lives in Redis.", s)

    story += _subsection("8.2 Environment Configuration", s)
    env_rows = [
        ["OPENAI_API_KEY",        "OpenAI GPT API key"],
        ["DEEPGRAM_API_KEY",      "Deepgram streaming STT key"],
        ["ELEVENLABS_API_KEY",    "ElevenLabs TTS key"],
        ["ELEVENLABS_VOICE_ID",   "Voice ID for TTS (Marin or equivalent)"],
        ["TWILIO_ACCOUNT_SID",    "Twilio account SID"],
        ["TWILIO_AUTH_TOKEN",     "Twilio auth token"],
        ["CNO_API_BASE_URL",      "Base URL for insuranceCompany backend REST APIs"],
        ["CNO_JWT_SECRET",        "HMAC-SHA256 secret for payment JWT signing"],
        ["REDIS_URL",             "Redis connection URL (prod checkpointer)"],
        ["POSTGRES_DSN",          "PostgreSQL DSN for pgvector FAQ store"],
    ]
    story += _table(
        ["Environment Variable", "Purpose"],
        env_rows,
        col_widths=[5.5 * cm, W - 4 * cm - 5.5 * cm],
    )

    story += _subsection("8.3 Scalability", s)
    story += _bullets([
        "Each call is an independent async WebSocket session — horizontally scalable",
        "RedisCheckpointer (swap from MemorySaver) enables multi-process / multi-pod deployment",
        "Deepgram and ElevenLabs connections are per-call and short-lived",
        "insuranceCompany API calls are async (aiohttp) with 10-15s timeouts",
    ], s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 9. Non-Functional Requirements ──────────────────────────────────────
    story += _section("9. Non-Functional Requirements", s)

    nfr_rows = [
        ["Response Latency",  "< 2s",     "STT finalize → LLM → TTS first chunk to Twilio"],
        ["Barge-in Latency",  "~60ms",    "3 × 20ms VAD frames of sustained speech"],
        ["Availability",      "99.9%",    "Stateless container; Redis for state; multi-replica capable"],
        ["Concurrent Calls",  "N × pods", "One WebSocket per call; scales horizontally"],
        ["Audio Quality",     "MOS > 4",  "ElevenLabs Turbo v2; stability 0.55; speaker boost on"],
        ["STT Accuracy",      "WER < 5%", "Deepgram Nova-2; insuranceCompany domain keywords boosted"],
        ["Security",          "PCI DSS",  "Card data via DTMF only; JWT-signed payment API calls"],
        ["Compliance",        "GLBA",     "Privacy opt-out; verbatim scripts enforced"],
    ]
    story += _table(
        ["Requirement", "Target", "Notes"],
        nfr_rows,
        col_widths=[4 * cm, 3 * cm, W - 4 * cm - 7 * cm],
    )

    story.append(Spacer(1, 0.2 * cm))

    # ── 10. Assumptions & Constraints ────────────────────────────────────────
    story += _section("10. Assumptions & Constraints", s)
    story += _bullets([
        "Twilio Media Streams must be configured to send mulaw 8kHz audio to the /stream WebSocket endpoint",
        "All insuranceCompany backend APIs are RESTful HTTP/JSON and accessible from the IVR service",
        "OpenAI GPT is used as the LLM; model selection is configurable via environment variable",
        "ElevenLabs voice ID must be pre-configured in .env (Marin voice or equivalent)",
        "Redis is required for production multi-process deployment; MemorySaver sufficient for single-process",
        "Card data is never stored — only passed through to insuranceCompany payment API in a single synchronous call",
        "Owner/beneficiary change requests are always escalated to a live agent (not supported in IVR)",
        "Document delivery is restricted to address on file (no email) per insuranceCompany compliance policy",
    ], s)

    return story


# ══════════════════════════════════════════════════════════════════════════════
# LLD
# ══════════════════════════════════════════════════════════════════════════════
def build_lld():
    s = _styles()
    story = []

    story += _cover(
        "insuranceCompany IVR",
        "Low Level Design (LLD)",
        "v1.0",
        "June 2026",
    )

    # ── 1. Module Structure ───────────────────────────────────────────────────
    story += _section("1. Module & Directory Structure", s)
    story += _code("""\
cno_ivr/
├── main.py                          # FastAPI app; WebSocket /stream handler
├── Dockerfile                       # python:3.11-slim; port 8000; uvicorn
├── requirements.txt                 # pinned dependencies
├── config/
│   ├── __init__.py                  # re-exports settings
│   └── settings.py                  # pydantic-settings; reads .env
├── core/
│   ├── graph/
│   │   ├── graph.py                 # build_graph() + cno_graph (compiled)
│   │   ├── state.py                 # CNOState TypedDict definition
│   │   └── nodes/
│   │       ├── router.py            # router_node  — GPT intent classifier
│   │       ├── auth.py              # auth_node    — PII collection + verify
│   │       ├── policy.py            # policy_node  — HOLDING_INQUIRY
│   │       ├── payment.py           # payment_node — PAYMENT_HISTORY
│   │       ├── otp.py               # otp_node     — one-time payment
│   │       ├── loan.py              # loan_node    — LOAN_INQUIRY
│   │       ├── beneficiary.py       # beneficiary_node
│   │       ├── contact.py           # contact_node
│   │       ├── document.py          # document_node
│   │       ├── privacy.py           # privacy_node
│   │       ├── faq.py               # faq_node     — RAG + pgvector
│   │       └── escalation.py        # escalation_node — live agent transfer
│   ├── prompts/
│   │   ├── system_prompt.py         # CNO_SYSTEM_PROMPT + ROUTER_PROMPT
│   │   └── __init__.py
│   └── tools/
│       ├── holding_inquiry.py       # holding_inquiry, payment_history, loan_inquiry
│       ├── payment_api.py           # process_card_payment, process_ach_payment, get_ach_script
│       └── __init__.py
├── services/
│   ├── stt.py                       # STTService  — Deepgram asynclive
│   ├── tts.py                       # TTSService  — ElevenLabs Turbo v2
│   ├── vad.py                       # VADService  — WebRTC VAD barge-in
│   └── __init__.py
├── utils/
│   ├── tts_normalizer.py            # normalize_tts_text() — 17-step pipeline
│   └── __init__.py
├── webhooks/
│   └── __init__.py                  # (reserved for Twilio status callbacks)
└── tests/
    ├── test_nodes.py                # Unit tests for LangGraph nodes (no audio)
    ├── test_ws_stream.py            # E2E WebSocket simulator (WAV replay)
    └── test_utils.py                # Unit tests for TTSNormalizer""", s)

    story.append(Spacer(1, 0.3 * cm))

    # ── 2. State Schema ───────────────────────────────────────────────────────
    story += _section("2. CNOState — Graph State Schema", s)
    story += _p(
        "All state flowing between LangGraph nodes is defined as a TypedDict (<b>CNOState</b>). "
        "The checkpointer serializes this to Redis (prod) or in-memory (dev), keyed by call_sid.", s)

    state_rows = [
        ["call_sid",        "str",            "Twilio call SID — unique per call; used as LangGraph thread_id"],
        ["stream_sid",      "str",            "Twilio stream SID — identifies the media stream"],
        ["authenticated",   "bool",           "True when caller passes 2-factor PII verification"],
        ["auth_step",       "str",            "Enum: collecting_phone | collecting_policy | collecting_dob | collecting_name | collecting_zip | complete | failed"],
        ["auth_attempts",   "int",            "Total PII failure count (reset per field; escalate at 3 total)"],
        ["customer",        "dict",           "Verified customer data: firstName, lastName, policyNumber, dateOfBirth, phoneNumber, partyKey, companyCode, authStatus"],
        ["access_token",    "str",            "Bearer token for insuranceCompany backend API calls"],
        ["finalized_party", "dict",           "Full party record returned after successful auth"],
        ["pii_collected",   "dict",           "PII pieces collected so far (keyed by type)"],
        ["current_intent",  "str",            "Latest intent classification (auth | policy_info | payment | otp | loan | beneficiary | owner_change | document | contact_change | privacy | faq | escalate)"],
        ["current_node",    "str",            "Name of last active node (for logging/debugging)"],
        ["slot_attempts",   "dict[str,int]",  "Per-slot retry counters (slot_name → count)"],
        ["tts_text",        "str",            "Response text set by active node; consumed by WebSocket handler to drive TTS"],
        ["transfer_to",     "str",            "Agent queue/skill name for live transfer (set by escalation_node)"],
        ["otp_step",        "str",            "OTP flow sub-step: init | card_type | amount | collecting_card | collecting_ach | ach_auth | confirm | complete"],
        ["otp_data",        "dict",           "OTP in-progress data: payment_type, amount, card_last4, confirmation_number"],
        ["metric_data",     "dict",           "API call tracking for reporting"],
        ["messages",        "list[BaseMessage]", "LangChain message history for current call turn"],
    ]
    story += _table(
        ["Field", "Type", "Description"],
        state_rows,
        col_widths=[3.8 * cm, 3.2 * cm, W - 4 * cm - 7 * cm],
    )

    story.append(PageBreak())

    # ── 3. Services Layer ─────────────────────────────────────────────────────
    story += _section("3. Services Layer", s)

    # 3.1 STT
    story += _subsection("3.1 STTService (services/stt.py)", s)
    story += _p(
        "Wraps the Deepgram <b>asynclive v1</b> streaming connection. Receives raw mulaw bytes "
        "from Twilio and forwards them to Deepgram without buffering to minimize latency. "
        "Returns both interim and final transcripts via callback.", s)

    stt_rows = [
        ["Model",             "nova-2"],
        ["Language",          "en-US"],
        ["Encoding",          "mulaw (G.711)"],
        ["Sample Rate",       "8000 Hz"],
        ["Channels",          "1 (mono)"],
        ["Endpointing",       "300ms (treat 300ms silence as end of utterance)"],
        ["Utterance End",     "1000ms (finalize after 1s of no new words)"],
        ["Interim Results",   "Enabled (reduces perceived latency)"],
        ["Smart Format",      "Enabled (auto-formats dates, numbers, currencies)"],
        ["Domain Keywords",   "insuranceCompany, policy number, beneficiary, premium, paid-to-date, autopay"],
        ["Keepalive",         "Enabled (persistent connection for call duration)"],
    ]
    story += _table(["Parameter", "Value"], stt_rows, col_widths=[5 * cm, W - 4 * cm - 5 * cm])

    story += _subsubsection("Key Methods", s)
    method_rows = [
        ["start()",           "Opens Deepgram live connection; registers transcript and error handlers"],
        ["send_audio(bytes)", "Forwards raw mulaw bytes to Deepgram — called per Twilio media event"],
        ["finish()",          "Closes Deepgram connection cleanly on call end"],
        ["_handle_transcript","Extracts text + is_final flag; calls on_transcript callback"],
    ]
    story += _table(["Method", "Description"], method_rows, col_widths=[4.5 * cm, W - 4 * cm - 4.5 * cm])
    story.append(Spacer(1, 0.3 * cm))

    # 3.2 TTS
    story += _subsection("3.2 TTSService (services/tts.py)", s)
    story += _p(
        "Uses ElevenLabs <b>Turbo v2</b> streaming TTS. Receives normalized text from the graph, "
        "streams PCM audio at 8kHz, and converts each chunk to G.711 mulaw using Python's built-in "
        "<b>audioop.lin2ulaw()</b> before sending to Twilio.", s)

    tts_rows = [
        ["Model",             "eleven_turbo_v2"],
        ["Output Format",     "pcm_8000 (8kHz 16-bit PCM)"],
        ["Conversion",        "audioop.lin2ulaw(chunk, 2) → G.711 mulaw"],
        ["Stability",         "0.55 (balanced naturalness/consistency)"],
        ["Similarity Boost",  "0.75 (voice match)"],
        ["Style",             "0.0 (neutral — no exaggerated style)"],
        ["Speaker Boost",     "Enabled"],
        ["Streaming",         "convert_as_stream() — yields chunks as they are generated"],
    ]
    story += _table(["Parameter", "Value"], tts_rows, col_widths=[5 * cm, W - 4 * cm - 5 * cm])
    story.append(Spacer(1, 0.3 * cm))

    # 3.3 VAD
    story += _subsection("3.3 VADService (services/vad.py)", s)
    story += _p(
        "WebRTC VAD for real-time barge-in detection. Runs <b>in parallel</b> during TTS playback. "
        "When 3 consecutive 20ms speech frames are detected (~60ms), fires barge-in: "
        "TTS streaming is stopped and Twilio is sent a 'clear' command.", s)

    vad_rows = [
        ["Aggressiveness",     "3 (most aggressive — best for noisy phone lines)"],
        ["Sample Rate",        "8000 Hz"],
        ["Frame Duration",     "20ms (supported: 10ms, 20ms, 30ms)"],
        ["PCM Frame Size",     "320 bytes (160 samples × 2 bytes/sample 16-bit)"],
        ["Barge-in Threshold", "3 consecutive speech frames (60ms)"],
        ["Audio Conversion",   "audioop.ulaw2lin(bytes, 2) → PCM before VAD processing"],
        ["Decay",              "Non-speech frame decrements speech_frame_count by 1 (not reset to 0)"],
    ]
    story += _table(["Parameter", "Value"], vad_rows, col_widths=[5 * cm, W - 4 * cm - 5 * cm])

    story.append(Spacer(1, 0.2 * cm))
    story += _subsubsection("VADService State Machine", s)
    story += _bullets([
        "<b>Idle</b>: VAD not active (not during TTS playback)",
        "<b>Monitoring</b>: TTS is playing; every inbound audio frame is VAD-processed",
        "<b>Barge-in Fired</b>: 3 consecutive speech frames detected; TTS stopped; Twilio 'clear' sent; VAD reset",
        "<b>Reset</b>: reset() called after barge-in or end of TTS; counters zeroed",
    ], s)

    story.append(PageBreak())

    # ── 4. LangGraph Nodes ────────────────────────────────────────────────────
    story += _section("4. LangGraph Nodes — Detailed Design", s)

    # Router
    story += _subsection("4.1 router_node (core/graph/nodes/router.py)", s)
    story += _p("Uses a token-efficient ROUTER_PROMPT with OpenAI GPT to classify "
                "the latest caller utterance into exactly one of 12 intents. "
                "Does not modify auth state or call any APIs.", s)
    story += _bullets([
        "Input: last HumanMessage from state.messages + state.authenticated",
        "Output: state.current_intent = one of 12 intent labels",
        "LLM: ChatOpenAI; ROUTER_PROMPT (approx 150 tokens); temperature 0",
        "Returns ONLY the intent label string — no explanation",
    ], s)

    # Auth
    story += _subsection("4.2 auth_node (core/graph/nodes/auth.py)", s)
    story += _p("Stateful PII collection loop. Tracks auth_step in state to know "
                "which PII field is currently being collected. Calls verify_caller() "
                "after each collected piece.", s)
    auth_flow = [
        ["collecting_phone",   "Ask for / receive phone number (ANI pre-filled if available)"],
        ["collecting_policy",  "Ask for 10-digit alphanumeric policy number"],
        ["collecting_dob",     "Ask for date of birth (month/day/year)"],
        ["collecting_name",    "Ask for insured first and last name"],
        ["collecting_zip",     "Ask for zip code and street address"],
        ["complete",           "2 PII pieces verified → auth done; state.authenticated = True"],
        ["failed",             "3 consecutive total failures → escalation"],
    ]
    story += _table(["auth_step", "Description"], auth_flow, col_widths=[4.5 * cm, W - 4 * cm - 4.5 * cm])
    story += _bullets([
        "verify_caller() called after each PII piece is collected",
        "If match count reaches 2 → auth_step = 'complete'",
        "Per-field: up to 3 retries (format error, silence, denial handled separately)",
        "Denial: offer alternative PII field (rotate to next in sequence)",
    ], s)
    story.append(Spacer(1, 0.2 * cm))

    # Policy
    story += _subsection("4.3 policy_node (core/graph/nodes/policy.py)", s)
    story += _p("Calls HOLDING_INQUIRY API and returns policy status, premium amount, "
                "paid-to-date date, and coverage summary in a voice-friendly format.", s)
    story += _bullets([
        "Requires: state.authenticated = True, state.customer.policyNumber, state.access_token",
        "API: POST /holding/inquiry { PolicyNumber }",
        "Response: status, premium (currency), paidToDate (date), coverage",
        "TTS: normalized via TTSNormalizer (currency → words, date → spoken format)",
        "Error: if API fails, offers to connect caller to agent",
    ], s)

    # Payment
    story += _subsection("4.4 payment_node (core/graph/nodes/payment.py)", s)
    story += _p("Retrieves last 3 payment transactions and enforces the G4 compliance disclosure.", s)
    story += _bullets([
        "API: POST /payment/history { PolicyNumber, MaxRecords: 3 }",
        "G4 Disclosure (mandatory): '24 to 48 hours for online payments, 7 to 10 business days for mailed'",
        "Formats each transaction: date + amount in voice-friendly text",
        "If no transactions found: 'No payment history found for this policy'",
    ], s)

    # OTP
    story += _subsection("4.5 otp_node (core/graph/nodes/otp.py)", s)
    story += _p("Handles one-time payment (card or ACH). Card data collected via Twilio DTMF "
                "— never via voice/LLM. JWT-signed API call.", s)
    otp_flow = [
        ["init",            "Ask payment type: card or bank (ACH)"],
        ["card_type",       "Identify debit/credit"],
        ["amount",          "Collect payment amount"],
        ["collecting_card", "Redirect caller to DTMF card entry (Twilio Gather)"],
        ["collecting_ach",  "Collect routing + account number via DTMF"],
        ["ach_auth",        "Read verbatim ACH authorization script; request 'I authorize'"],
        ["confirm",         "Read back payment details; request confirmation"],
        ["complete",        "Call process_card_payment() or process_ach_payment(); read confirmation number"],
    ]
    story += _table(["otp_step", "Description"], otp_flow, col_widths=[4 * cm, W - 4 * cm - 4 * cm])
    story += _bullets([
        "Prepaid card check: if 'no name on card' → decline with restriction script",
        "JWT: _generate_jwt(policyNumber, amount) with 5-minute expiry; HS256",
        "Card data never stored in CNOState beyond the payment API call",
    ], s)

    # FAQ
    story += _subsection("4.6 faq_node (core/graph/nodes/faq.py)", s)
    story += _p("Retrieves relevant FAQ content using pgvector cosine similarity search, "
                "then generates a voice-concise answer with GPT.", s)
    story += _bullets([
        "Embeds caller utterance using OpenAI text-embedding-ada-002",
        "pgvector query: SELECT content FROM faq_embeddings ORDER BY embedding <=> $1 LIMIT 3",
        "Top-3 chunks fed as context into GPT with CNO_SYSTEM_PROMPT",
        "Response limited to 2-3 sentences per voice rules",
    ], s)

    # Escalation
    story += _subsection("4.7 escalation_node (core/graph/nodes/escalation.py)", s)
    story += _p("Terminal node. Sets transfer_to queue, generates farewell TTS, and ends graph execution.", s)
    story += _bullets([
        "Sets state.transfer_to = 'live_agent_queue' (or specific queue based on reason)",
        "Generates tts_text: 'Please hold while I connect you to a representative.'",
        "WebSocket handler reads transfer_to and sends Twilio <Dial> or transfer signal",
        "Triggered by: auth failure, owner_change intent, caller request, unsupported request",
    ], s)

    story.append(PageBreak())

    # ── 5. Tools / API Integration ────────────────────────────────────────────
    story += _section("5. Tools — insuranceCompany API Integration", s)

    story += _subsection("5.1 holding_inquiry.py", s)
    api_rows = [
        ["holding_inquiry",  "POST /holding/inquiry",  "{ PolicyNumber }",                          "policy status, premium, paidToDate, coverage"],
        ["payment_history",  "POST /payment/history",  "{ PolicyNumber, MaxRecords: 3 }",           "transactions[]: date, amount, type"],
        ["loan_inquiry",     "POST /loan/inquiry",     "{ PolicyNumber }",                          "loanBalance, interestRate, payoffAmount"],
    ]
    story += _table(
        ["Function", "Endpoint", "Request", "Response"],
        api_rows,
        col_widths=[3.5 * cm, 4 * cm, 5 * cm, W - 4 * cm - 12.5 * cm],
    )

    story += _subsection("5.2 payment_api.py", s)
    pay_rows = [
        ["process_card_payment", "POST /payment/card", "PolicyNumber, Amount, CardNumber, ExpiryDate, CVV", "ConfirmationNumber"],
        ["process_ach_payment",  "POST /payment/ach",  "PolicyNumber, Amount, RoutingNumber, AccountNumber, AccountType", "ConfirmationNumber"],
        ["get_ach_script",       "—",                  "None",                                                  "Verbatim ACH authorization text string"],
    ]
    story += _table(
        ["Function", "Endpoint", "Key Inputs", "Key Output"],
        pay_rows,
        col_widths=[3.5 * cm, 4 * cm, 5 * cm, W - 4 * cm - 12.5 * cm],
    )

    story += _subsubsection("JWT Generation (_generate_jwt)", s)
    story += _code("""\
payload = {
    "policyNumber": policy_number,   # policy being paid
    "amount":        amount,          # payment amount (float)
    "iat":           int(time.time()),
    "exp":           int(time.time()) + 300,  # 5-minute expiry
}
jwt.encode(payload, settings.cno_jwt_secret, algorithm="HS256")""", s)
    story += _p("The JWT is passed in the <i>X-Payment-JWT</i> header. The Authorization header "
                "simultaneously carries the session Bearer token.", s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 6. TTS Normalizer ─────────────────────────────────────────────────────
    story += _section("6. TTS Text Normalizer (utils/tts_normalizer.py)", s)
    story += _p(
        "A 17-step deterministic text normalization pipeline applied to all LLM output before "
        "sending to ElevenLabs. Python port of the TTSPreProcessor.groovy from the Amelia BPM system.", s)

    norm_rows = [
        ["1",  "Protect ${variables}",         "Saves template variables as tokens; restored at step 17"],
        ["2",  "Fix double periods",            "'..' → '.'"],
        ["3",  "Trailing space before punct",   "' ?' → '?'"],
        ["4",  "Odd hyphenation",               "'both- affiliated' → 'both, affiliated'"],
        ["5",  "Currency spacing",              "'$ 50' → '$50'"],
        ["6",  "Currency → words",              "'$1,234.56' → 'one thousand two hundred thirty-four dollars and fifty-six cents'"],
        ["7",  "Percentages → words",           "'5%' → 'five percent'"],
        ["8",  "Phone NXX-NXX-XXXX",            "'555-867-5309' → 'five five five, eight six seven, five three zero nine'"],
        ["9",  "10-digit phone (no dashes)",    "'5558675309' → spelled digit groups"],
        ["10", "Policy / case numbers",         "'P300123456' → 'P three zero zero one two three four five six'"],
        ["11", "Date MM/DD/YYYY",               "'01/22/1978' → 'January 22nd, 1978'"],
        ["12", "Date YYYY-MM-DD",               "'1978-01-22' → 'January 22nd, 1978'"],
        ["13", "Address abbreviations",         "'Blvd' → 'Boulevard', 'Ave' → 'Avenue', etc. (16 patterns)"],
        ["14", "Title abbreviations",           "'Mr.' → 'Mister', 'Dr.' → 'Doctor', etc."],
        ["15", "Missing space after sentence",  "'end.Next' → 'end. Next'"],
        ["16", "Normalize whitespace",          "Collapse multiple spaces; strip leading/trailing"],
        ["17", "Restore ${variables}",          "Replace tokens with original template variable strings"],
    ]
    story += _table(
        ["Step", "Transform", "Example"],
        norm_rows,
        col_widths=[1.2 * cm, 5.5 * cm, W - 4 * cm - 6.7 * cm],
    )

    story.append(Spacer(1, 0.2 * cm))

    # ── 7. Configuration ──────────────────────────────────────────────────────
    story += _section("7. Configuration (config/settings.py)", s)
    story += _p("Pydantic-settings reads all configuration from environment variables (.env file in dev). "
                "All keys are validated at startup — missing required keys raise a validation error.", s)
    story += _code("""\
class Settings(BaseSettings):
    # LLM
    openai_api_key:       str
    openai_model:         str = "gpt-4o"

    # STT
    deepgram_api_key:     str

    # TTS
    elevenlabs_api_key:   str
    elevenlabs_voice_id:  str

    # Telephony
    twilio_account_sid:   str
    twilio_auth_token:    str

    # insuranceCompany Backend
    cno_api_base_url:     str
    cno_jwt_secret:       str

    # Persistence
    redis_url:            str = "redis://localhost:6379"
    postgres_dsn:         str

    class Config:
        env_file = ".env"

settings = Settings()""", s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 8. WebSocket Handler ──────────────────────────────────────────────────
    story += _section("8. WebSocket Handler (main.py — /stream)", s)
    story += _p("The WebSocket handler is the main orchestration point. It manages the lifecycle "
                "of a single phone call: audio in → graph → audio out.", s)

    ws_rows = [
        ["start event",   "Initialize STTService, TTSService, VADService; set call_sid + stream_sid in state; send greeting TTS"],
        ["media event",   "Base64-decode mulaw bytes; feed to stt.send_audio(); if TTS playing, run vad.process(); on barge-in: stop TTS, send Twilio 'clear'"],
        ["STT callback (interim)",  "Buffer partial transcript; optionally show in logs"],
        ["STT callback (final)",    "Build HumanMessage; run cno_graph.ainvoke(state, config); extract tts_text; stream TTS"],
        ["TTS streaming",  "Iterate TTSService.stream(tts_text); base64-encode each mulaw chunk; send as Twilio media event"],
        ["stop event",    "Call stt.finish(); log call_sid; clean up resources"],
    ]
    story += _table(
        ["Event / Callback", "Handler Logic"],
        ws_rows,
        col_widths=[4.5 * cm, W - 4 * cm - 4.5 * cm],
    )

    story.append(Spacer(1, 0.2 * cm))

    # ── 9. Graph Routing — Decision Table ────────────────────────────────────
    story += _section("9. Graph Routing — Full Decision Table", s)

    route_rows = [
        ["auth",           "False",     "auth",        "Collect PII; verify; on complete re-route"],
        ["auth",           "True",      "auth",        "Re-verify or confirm already authenticated"],
        ["policy_info",    "True",      "policy",      "Call HOLDING_INQUIRY; return policy data"],
        ["policy_info",    "False",     "auth",        "Auth first; then re-route to policy"],
        ["payment",        "True",      "payment",     "Call PAYMENT_HISTORY; return last 3 txns"],
        ["otp",            "True",      "otp",         "One-time payment flow (card/ACH)"],
        ["loan",           "True",      "loan",        "Call LOAN_INQUIRY; return loan data"],
        ["beneficiary",    "True",      "beneficiary", "Return beneficiary information"],
        ["owner_change",   "True",      "escalation",  "Not supported in IVR — always escalate"],
        ["document",       "True",      "document",    "Request doc; mail/fax only"],
        ["contact_change", "True",      "contact",     "Update address or phone"],
        ["privacy",        "True",      "privacy",     "GLBA opt-out script"],
        ["faq",            "Any",       "faq",         "RAG-based FAQ — no auth required"],
        ["escalate",       "Any",       "escalation",  "Caller explicitly requests agent"],
    ]
    story += _table(
        ["Intent", "Auth Required", "Routes To", "Description"],
        route_rows,
        col_widths=[3.2 * cm, 3 * cm, 3.2 * cm, W - 4 * cm - 9.4 * cm],
    )

    story.append(Spacer(1, 0.2 * cm))

    # ── 10. Test Strategy ─────────────────────────────────────────────────────
    story += _section("10. Test Strategy", s)

    story += _subsection("10.1 Unit Tests — test_nodes.py", s)
    story += _p("Tests each LangGraph node in isolation using a mock CNOState (no audio, no Twilio). "
                "Requires OPENAI_API_KEY and a mock insuranceCompany API server.", s)
    test_rows = [
        ["test_router_node",    "Validates intent detection for: policy, escalate, loan"],
        ["test_auth_node",      "Verifies auth prompts phone number on collecting_phone step"],
        ["test_policy_node",    "Verifies policy node returns non-empty TTS text"],
        ["test_payment_node",   "Verifies G4 disclosure present in payment response"],
        ["test_escalation_node","Verifies transfer_to is set and TTS text is non-empty"],
        ["test_faq_node",       "Verifies FAQ node returns non-empty answer"],
    ]
    story += _table(["Test", "Validates"], test_rows, col_widths=[5 * cm, W - 4 * cm - 5 * cm])

    story += _subsection("10.2 Integration Test — test_ws_stream.py", s)
    story += _p("Full end-to-end WebSocket simulator. Replays a WAV file (or silence) as Twilio "
                "Media Stream events. Validates the complete pipeline: audio → STT → LangGraph → TTS → "
                "audio back over WebSocket.", s)
    story += _bullets([
        "Sends Twilio 'start' event with fake call_sid + stream_sid",
        "Streams 20ms mulaw chunks at real-time pacing",
        "Listens for outbound TTS 'media' events and 'clear' (barge-in) events",
        "Can replay real WAV recordings for regression testing",
        "Requires: app running on localhost:8000 + all API keys configured",
    ], s)

    story.append(Spacer(1, 0.2 * cm))

    # ── 11. Error Handling ───────────────────────────────────────────────────
    story += _section("11. Error Handling", s)
    err_rows = [
        ["Deepgram connection error",  "Log error; STT service continues (no crash); transcript callback not fired"],
        ["ElevenLabs API error",       "Log error; tts_text not streamed; WebSocket handler continues"],
        ["insuranceCompany API timeout (10s)",      "Returns {success: False, error: ...}; node generates 'having trouble' TTS; offers escalation"],
        ["insuranceCompany API 4xx/5xx",            "Parses ErrorBlock[0].ErrorMessage; logs; offers escalation"],
        ["LLM (OpenAI) error",         "LangChain raises; WebSocket handler catches; sends 'technical difficulty' TTS"],
        ["WebSocket disconnect",       "'stop' event fires; stt.finish() called; resources released"],
        ["Auth 3× failure",            "escalation_node triggered; live agent transfer initiated"],
        ["VAD exception",              "try/except per frame; single-frame exception skipped; VAD continues"],
    ]
    story += _table(
        ["Error Scenario", "Handling"],
        err_rows,
        col_widths=[5.5 * cm, W - 4 * cm - 5.5 * cm],
    )

    story.append(Spacer(1, 0.2 * cm))

    # ── 12. Sequence Diagram ─────────────────────────────────────────────────
    story += _section("12. Sequence — Authenticated Policy Inquiry", s)
    story += _p("Example call flow for a returning caller asking for their policy status:", s)
    story += _code("""\
Caller         Twilio         WebSocket      Deepgram       LangGraph       ElevenLabs
  |              |                |               |               |               |
  |--[calls]---->|                |               |               |               |
  |              |--[WS start]--->|               |               |               |
  |              |                |--[start()]-->|               |               |
  |              |                |               |               |               |
  |--[speaks "What is my policy status?"]-------->|               |               |
  |              |--[media]------>|               |               |               |
  |              |                |--[send_audio()]-->|               |               |
  |              |                |               |--[transcript(final)]-->        |
  |              |                |               |               |               |
  |              |                |<---[HumanMessage]------------>|               |
  |              |                |               |   [router: policy_info]       |
  |              |                |               |   [policy_node → API call]    |
  |              |                |               |   [tts_text set]              |
  |              |                |               |               |               |
  |              |                |--[stream(tts_text)]-------------------------------->|
  |              |                |<--[mulaw chunks]--------------------------------------|
  |              |<--[media]------|               |               |               |
  |<--[audio]----|               |               |               |               |""", s)

    return story


# ══════════════════════════════════════════════════════════════════════════════
# PDF generation
# ══════════════════════════════════════════════════════════════════════════════
def generate_pdf(filename, story_builder):
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=filename,
    )

    def _header_footer(canvas, doc):
        canvas.saveState()
        # Header bar
        canvas.setFillColor(BLUE_DARK)
        canvas.rect(2 * cm, H - 1.5 * cm, W - 4 * cm, 0.5 * cm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(2.2 * cm, H - 1.25 * cm, "insuranceCompany IVR — insuranceCompany")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(W - 2 * cm, H - 1.25 * cm,
                               "HLD" if "HLD" in filename else "LLD")
        # Footer
        canvas.setFillColor(GREY_MED)
        canvas.rect(2 * cm, 1.2 * cm, W - 4 * cm, 0.03 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#7F8C8D"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(2 * cm, 0.85 * cm, "CONFIDENTIAL — Internal Use Only")
        canvas.drawRightString(W - 2 * cm, 0.85 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(story_builder(), onFirstPage=_header_footer, onLaterPages=_header_footer)
    print(f"Generated: {filename}")


if __name__ == "__main__":
    generate_pdf("cno_ivr_HLD.pdf", build_hld)
    generate_pdf("cno_ivr_LLD.pdf", build_lld)
    print("Done.")
