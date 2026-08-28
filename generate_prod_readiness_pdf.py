"""
Generates CNO IVR Production Readiness + Observability Plan as a PDF.
Run: venv/Scripts/python.exe generate_prod_readiness_pdf.py
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import Flowable
import datetime

OUT = "CNO_IVR_Production_Readiness_Plan.pdf"

# ── Colour palette ─────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#0D2B4E")
BLUE    = colors.HexColor("#1565C0")
LTBLUE  = colors.HexColor("#E3F2FD")
GREEN   = colors.HexColor("#1B5E20")
LTGREEN = colors.HexColor("#E8F5E9")
AMBER   = colors.HexColor("#E65100")
LTAMB   = colors.HexColor("#FFF3E0")
RED     = colors.HexColor("#B71C1C")
LTRED   = colors.HexColor("#FFEBEE")
GREY    = colors.HexColor("#455A64")
LTGREY  = colors.HexColor("#ECEFF1")
WHITE   = colors.white
BLACK   = colors.HexColor("#212121")


# ── Styles ─────────────────────────────────────────────────────────────────────
def make_styles():
    base = getSampleStyleSheet()
    S = {}

    S["cover_title"] = ParagraphStyle("cover_title", fontSize=28, fontName="Helvetica-Bold",
        textColor=WHITE, alignment=TA_CENTER, leading=36, spaceAfter=6)
    S["cover_sub"] = ParagraphStyle("cover_sub", fontSize=14, fontName="Helvetica",
        textColor=colors.HexColor("#BBDEFB"), alignment=TA_CENTER, leading=20, spaceAfter=4)
    S["cover_meta"] = ParagraphStyle("cover_meta", fontSize=10, fontName="Helvetica",
        textColor=colors.HexColor("#90CAF9"), alignment=TA_CENTER, leading=14)

    S["h1"] = ParagraphStyle("h1", fontSize=16, fontName="Helvetica-Bold",
        textColor=NAVY, spaceBefore=18, spaceAfter=6, leading=22,
        borderPad=0, leftIndent=0)
    S["h2"] = ParagraphStyle("h2", fontSize=12, fontName="Helvetica-Bold",
        textColor=BLUE, spaceBefore=12, spaceAfter=4, leading=16)
    S["h3"] = ParagraphStyle("h3", fontSize=10, fontName="Helvetica-Bold",
        textColor=GREY, spaceBefore=8, spaceAfter=3, leading=14)

    S["body"] = ParagraphStyle("body", fontSize=9, fontName="Helvetica",
        textColor=BLACK, leading=14, spaceAfter=4, leftIndent=0)
    S["bullet"] = ParagraphStyle("bullet", fontSize=9, fontName="Helvetica",
        textColor=BLACK, leading=13, spaceAfter=2, leftIndent=12, bulletIndent=4)
    S["code"] = ParagraphStyle("code", fontSize=8, fontName="Courier",
        textColor=colors.HexColor("#1A237E"), leading=12, spaceAfter=2,
        leftIndent=12, backColor=colors.HexColor("#F5F5F5"))

    S["th"] = ParagraphStyle("th", fontSize=8.5, fontName="Helvetica-Bold",
        textColor=WHITE, alignment=TA_LEFT, leading=12)
    S["td"] = ParagraphStyle("td", fontSize=8.5, fontName="Helvetica",
        textColor=BLACK, alignment=TA_LEFT, leading=12)
    S["td_green"] = ParagraphStyle("td_green", fontSize=8.5, fontName="Helvetica-Bold",
        textColor=GREEN, alignment=TA_LEFT, leading=12)
    S["td_amber"] = ParagraphStyle("td_amber", fontSize=8.5, fontName="Helvetica-Bold",
        textColor=AMBER, alignment=TA_LEFT, leading=12)
    S["td_red"] = ParagraphStyle("td_red", fontSize=8.5, fontName="Helvetica-Bold",
        textColor=RED, alignment=TA_LEFT, leading=12)
    S["caption"] = ParagraphStyle("caption", fontSize=7.5, fontName="Helvetica-Oblique",
        textColor=GREY, alignment=TA_CENTER, spaceAfter=8)
    return S


class ColorBox(Flowable):
    """A solid-color rectangle (used for section headers and cover)."""
    def __init__(self, width, height, color, radius=3):
        Flowable.__init__(self)
        self.width = width; self.height = height
        self.color = color; self.radius = radius

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=0)


def section_header(title, S, width, color=NAVY):
    """Returns a section header block: colored bar + bold title."""
    return KeepTogether([
        HRFlowable(width=width, thickness=3, color=color, spaceAfter=4),
        Paragraph(title, S["h1"]),
    ])


def table(headers, rows, S, col_widths, row_colors=None):
    header_row = [Paragraph(h, S["th"]) for h in headers]
    data = [header_row]
    for r in rows:
        data.append([Paragraph(str(c), S["td"]) for c in r])

    style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), NAVY),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LTGREY]),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#CFD8DC")),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ])
    if row_colors:
        for row_idx, bg in row_colors:
            style.add("BACKGROUND", (0, row_idx), (-1, row_idx), bg)

    t = Table(data, colWidths=col_widths)
    t.setStyle(style)
    return t


def colored_table(data_rows, S, col_widths):
    """Table where each row has (cells_list, bg_color)."""
    data = []
    bg_cmds = []
    for i, (cells, bg) in enumerate(data_rows):
        data.append([Paragraph(str(c), S["td"]) for c in cells])
        if bg:
            bg_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))

    style = TableStyle([
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#CFD8DC")),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ] + bg_cmds)

    t = Table(data, colWidths=col_widths)
    t.setStyle(style)
    return t


# ── Page layout helpers ────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def header_footer(canvas, doc):
    canvas.saveState()
    # Header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 10*mm, PAGE_W, 10*mm, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(MARGIN, PAGE_H - 6.5*mm, "CNO IVR — Production Readiness & Observability Plan")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 6.5*mm, f"CONFIDENTIAL")

    # Footer
    canvas.setFillColor(LTGREY)
    canvas.rect(0, 0, PAGE_W, 8*mm, fill=1, stroke=0)
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(MARGIN, 2.8*mm, f"Generated {datetime.date.today().strftime('%B %d, %Y')}")
    canvas.drawCentredString(PAGE_W/2, 2.8*mm, "insuranceCompany Life Insurance Company — Internal Use Only")
    canvas.drawRightString(PAGE_W - MARGIN, 2.8*mm, f"Page {doc.page}")
    canvas.restoreState()


# ── Content builders ───────────────────────────────────────────────────────────
def build_cover(S):
    W = CONTENT_W
    story = []

    # Cover block
    cover_data = [[
        Paragraph("CNO IVR SYSTEM", S["cover_title"]),
    ]]
    cover_table = Table([[
        Paragraph("CNO IVR SYSTEM", S["cover_title"]),
    ]], colWidths=[W])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("TOPPADDING", (0,0), (-1,-1), 28),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
        ("ROUNDEDCORNERS", [6]),
    ]))

    sub_rows = [
        [Paragraph("Production Readiness &amp; Observability Plan", S["cover_sub"])],
        [Paragraph("LangGraph · Twilio · Groq · Deepgram · ElevenLabs · Railway", S["cover_meta"])],
        [Spacer(1, 6)],
        [Paragraph(f"Prepared: {datetime.date.today().strftime('%B %d, %Y')} &nbsp;|&nbsp; Version 1.0 &nbsp;|&nbsp; CONFIDENTIAL", S["cover_meta"])],
    ]
    sub_table = Table(sub_rows, colWidths=[W])
    sub_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), BLUE),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
    ]))

    story.append(Spacer(1, 40*mm))
    story.append(cover_table)
    story.append(sub_table)
    story.append(Spacer(1, 12))

    # Key metrics strip
    metrics = [
        ("6", "Call Flow\nScenarios\nPassing"),
        ("2", "CI Pipeline\nJobs"),
        ("5x", "Router Speed\nGain (8b model)"),
        ("3", "Feature Flags\nRuntime-Tunable"),
        ("4", "Observability\nLayers"),
    ]
    metric_data = [[Paragraph(f'<font size="22" color="#1565C0"><b>{v}</b></font><br/>'
                              f'<font size="7" color="#455A64">{l}</font>', S["td"])
                    for v, l in metrics]]
    mt = Table(metric_data, colWidths=[W/5]*5)
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), LTBLUE),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
        ("LINEAFTER", (0,0), (-2,-1), 0.5, colors.HexColor("#BBDEFB")),
        ("GRID", (0,0), (-1,-1), 0, WHITE),
    ]))
    story.append(mt)
    story.append(Spacer(1, 24))

    # Executive summary box
    exec_box = Table([[
        Paragraph(
            "<b>Executive Summary</b><br/><br/>"
            "This document describes the end-to-end production readiness posture for the CNO IVR system "
            "built on LangGraph + Twilio Media Streams. It covers the CI/CD pipeline, deployment architecture, "
            "runtime observability, go-live checklist, performance tuning decisions, and the change-management "
            "model that allows configuration changes without code deployments. "
            "All 6 automated call-flow scenarios pass against the live LangGraph with the production router model.",
            S["body"])
    ]], colWidths=[W])
    exec_box.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), LTBLUE),
        ("TOPPADDING", (0,0), (-1,-1), 12),
        ("BOTTOMPADDING", (0,0), (-1,-1), 12),
        ("LEFTPADDING", (0,0), (-1,-1), 14),
        ("RIGHTPADDING", (0,0), (-1,-1), 14),
        ("LINEAFTER", (0,0), (0,-1), 4, BLUE),
    ]))
    story.append(exec_box)
    story.append(PageBreak())
    return story


def build_stack(S):
    W = CONTENT_W
    story = [section_header("1. System Architecture & Stack", S, W)]
    story.append(Paragraph(
        "The CNO IVR is a stateful conversational system that processes live telephony audio "
        "through a LangGraph state machine. Each caller turn flows: Twilio → WebSocket → Deepgram STT "
        "→ router node (intent classification) → service node (auth-gated) → ElevenLabs TTS → caller.",
        S["body"]))
    story.append(Spacer(1, 6))

    rows = [
        ("Telephony", "Twilio Voice + Media Streams", "WebSocket audio relay, TwiML webhook"),
        ("STT", "Deepgram nova-2", "Streaming mulaw 8kHz, smart-format, barge-in"),
        ("LLM Router", "Groq llama-3.1-8b-instant", "Single-word intent classification (~5x faster than 70b)"),
        ("LLM Nodes", "Groq llama-3.3-70b-versatile", "FAQ, policy, loan, payment, beneficiary responses"),
        ("RAG", "pgvector + OpenAI text-embedding-3-small", "38 knowledge categories, k=3 retrieval"),
        ("TTS", "ElevenLabs eleven_turbo_v2", "Streaming PCM → mulaw, latency opt=3"),
        ("Orchestration", "LangGraph StateGraph", "12 nodes, MemorySaver (dev) / PostgresSaver (prod)"),
        ("Session State", "Redis 7", "Per-call session, in-memory fallback in CI"),
        ("Persistence", "PostgreSQL 16 + pgvector", "LangGraph checkpoints, call history, RAG vectors"),
        ("Runtime", "FastAPI + Uvicorn", "Async, uvicorn[standard], port 8080"),
        ("Deployment", "Railway (Docker)", "Auto-deploy on master push; prod needs manual approval"),
        ("Auth", "Multi-step PII guard", "Phone → DOB → Name per-node, persona gate post-auth"),
    ]
    story.append(table(
        ["Layer", "Technology", "Notes"],
        rows, S,
        [38*mm, 62*mm, 75*mm]
    ))
    return story


def build_cicd(S):
    W = CONTENT_W
    story = [section_header("2. CI/CD Pipeline", S, W)]
    story.append(Paragraph(
        "Two GitHub Actions workflows govern all code changes. Every PR must pass CI before merging. "
        "Production deployments require a manual approval gate configured in GitHub Environments.",
        S["body"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("2.1  CI Workflow  (.github/workflows/ci.yml)", S["h2"]))
    ci_rows = [
        (["Job 1 — Unit Tests", "On every push / PR", "~30s", "No API keys needed",
          "23 tests: TTS normalizer, PII validator, date utils"], LTGREEN),
        (["Job 2 — Call Flow Tests", "After Job 1 passes", "~2 min", "GROQ_API_KEY secret",
          "6 end-to-end scenarios: auth paths A–F against live LangGraph + mock CNO API"], LTBLUE),
    ]
    ct = colored_table(
        [([h], None) for h in ["Job", "Trigger", "Duration", "Secrets Required", "What is tested"]][:0]
        + ci_rows,
        S, [38*mm, 28*mm, 18*mm, 35*mm, 56*mm]
    )
    # Use standard table instead
    story.append(table(
        ["Job", "Trigger", "Duration", "Secrets Required", "What is tested"],
        [
            ["Job 1 — Unit Tests", "Every push / PR", "~30s", "None",
             "23 tests: TTS normalizer, PII validator, date utils"],
            ["Job 2 — Call Flow", "After Job 1 passes", "~2 min", "GROQ_API_KEY",
             "6 E2E scenarios against live LangGraph + mock CNO API (ENABLE_RAG=false in CI)"],
        ],
        S, [38*mm, 28*mm, 16*mm, 30*mm, 63*mm],
        row_colors=[(1, LTGREEN), (2, LTBLUE)]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("2.2  Deploy Workflow  (.github/workflows/deploy.yml)", S["h2"]))
    story.append(table(
        ["Stage", "Trigger", "Target", "Gate", "Rollback"],
        [
            ["Build image", "master push", "GHCR (ghcr.io/org/cno-ivr:sha-xxxxx)", "CI must pass", "N/A"],
            ["Deploy Staging", "Automatic after build", "Railway staging service", "/health poll × 12",
             "Re-deploy previous SHA"],
            ["Deploy Production", "workflow_dispatch only", "Railway prod service",
             "GitHub Env required-reviewers + /health poll × 18", "Re-deploy previous SHA"],
            ["Notify", "On prod deploy (pass or fail)", "Slack webhook", "N/A", "N/A"],
        ],
        S, [28*mm, 35*mm, 52*mm, 38*mm, 22*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("2.3  Required GitHub Secrets", S["h2"]))
    story.append(table(
        ["Secret", "Where to get it", "Used by"],
        [
            ["GROQ_API_KEY", "console.groq.com → API Keys", "CI Job 2 (router + FAQ LLM calls)"],
            ["OPENAI_API_KEY", "platform.openai.com → API Keys", "CI optional — RAG embeddings (skipped if absent)"],
            ["RAILWAY_TOKEN_STAGING", "Railway project → Settings → Tokens", "deploy.yml staging step"],
            ["RAILWAY_TOKEN_PROD", "Railway project → Settings → Tokens", "deploy.yml production step"],
            ["STAGING_URL", "Railway staging public URL", "Health check after staging deploy"],
            ["PROD_URL", "Railway prod public URL", "Health check after prod deploy"],
            ["SLACK_WEBHOOK_URL", "Slack → Incoming Webhooks", "Optional — prod deploy notification"],
        ],
        S, [45*mm, 62*mm, 68*mm]
    ))
    return story


def build_observability(S):
    W = CONTENT_W
    story = [section_header("3. Observability", S, W)]
    story.append(Paragraph(
        "The system emits structured JSON logs via structlog on every significant event. "
        "Four observability layers are recommended: structured logs, dashboards, alerting, and call replay.",
        S["body"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("3.1  Structured Log Events (already instrumented)", S["h2"]))
    story.append(table(
        ["Event", "Node / Source", "Key Fields", "Use for"],
        [
            ["intent_detected", "router", "intent, input[:80]", "Intent distribution; mis-classification rate"],
            ["node_enter", "auth", "auth_step", "Auth funnel drop-off analysis"],
            ["auth_step_change", "auth", "from_step, to_step", "Step-by-step auth funnel"],
            ["auth_complete", "auth", "caller_name, persona", "Auth success rate; persona breakdown"],
            ["auth_failed", "auth", "attempts", "Auth failure rate; escalation trigger"],
            ["access_token_acquired", "auth", "party_key", "API availability (absence = CNO API down)"],
            ["rag_retrieved", "faq", "chunks, latency_ms", "RAG hit rate; pgvector latency"],
            ["faq_rag_miss", "faq", "query[:80]", "Knowledge gaps — topics to add to seed_knowledge.py"],
            ["llm_response", "faq / nodes", "latency_ms, chars", "LLM latency P50/P95 per node"],
            ["node_exit", "all nodes", "latency_ms, chars", "End-to-end node latency"],
        ],
        S, [38*mm, 24*mm, 42*mm, 71*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("3.2  Recommended Dashboards", S["h2"]))
    story.append(table(
        ["Dashboard", "Metric", "Target / Alert Threshold"],
        [
            ["Call Health", "Total calls/hr, avg duration, escalation rate", "Escalation rate < 25%"],
            ["Auth Funnel", "phone → DOB → name → complete conversion", "Auth success rate > 80%"],
            ["Latency (P95)", "Per-node latency: policy, payment, loan, faq", "P95 < 2.5s per node"],
            ["LLM Health", "Groq 503/429 rate, retry count, fallback-to-escalate count", "503 rate < 1/min"],
            ["RAG Quality", "FAQ RAG hit rate (chunks > 0 vs chunks = 0)", "Hit rate > 75%"],
            ["Error Rate", "5xx per webhook, API timeout count", "Error rate < 0.5%"],
            ["STT Quality", "Empty utterance rate (Deepgram returned blank)", "Blank rate < 5%"],
        ],
        S, [38*mm, 72*mm, 65*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("3.3  Alert Thresholds", S["h2"]))
    story.append(table(
        ["Alert", "Condition", "Severity", "Action"],
        [
            ["Auth success rate low", "< 70% over 15 min", "P1 — CRITICAL", "Check CNO party-search API; check Groq"],
            ["Avg turn latency high", "> 3s over 5 min", "P2 — HIGH", "Check Groq/Deepgram status pages"],
            ["Escalation rate high", "> 40% over 30 min", "P2 — HIGH", "Check router intent logs for misclassification"],
            ["Groq 503 rate", "> 5 errors/min", "P2 — HIGH", "Retry budget at risk; consider model fallback"],
            ["/health non-200", "Any failure", "P1 — CRITICAL", "PagerDuty; check Railway service logs"],
            ["RAG hit rate low", "< 50% over 1 hr", "P3 — MEDIUM", "Review faq_rag_miss events; re-seed knowledge"],
            ["FAQ fallback to escalate", "faq_fallback_to_escalate=true fires > 20/hr", "P3 — MEDIUM",
             "Add missing knowledge chunks to seed_knowledge.py"],
        ],
        S, [42*mm, 40*mm, 28*mm, 65*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("3.4  Log Aggregation Stack", S["h2"]))
    story.append(Paragraph(
        "The app writes structlog JSON to stdout. Railway's built-in log drain can forward to:", S["body"]))
    rows = [
        ["Grafana Cloud (free tier)", "Best fit for IVR volume; Loki for logs + Grafana dashboards; "
         "alerting via Grafana OnCall", "Recommended"],
        ["Datadog", "Richer APM + trace correlation; higher cost at scale", "Enterprise"],
        ["Papertrail", "Simple log search; no custom dashboards", "Low-cost fallback"],
        ["AWS CloudWatch", "If migrating to ECS Fargate later", "Future state"],
    ]
    story.append(table(
        ["Platform", "Notes", "Recommendation"],
        rows, S, [42*mm, 100*mm, 33*mm],
        row_colors=[(1, LTGREEN)]
    ))
    return story


def build_tuning(S):
    W = CONTENT_W
    story = [section_header("4. Performance Tuning", S, W)]
    story.append(Paragraph(
        "Tuning decisions made and rationale. All env-var settings are runtime-configurable "
        "without a code deploy.", S["body"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("4.1  LLM Model Selection", S["h2"]))
    story.append(table(
        ["Node", "Model", "Why"],
        [
            ["Router", "llama-3.1-8b-instant", "Single-word output only — 8b is ~5x faster with equal accuracy"],
            ["FAQ", "llama-3.3-70b-versatile", "Response quality matters; RAG grounding reduces hallucination risk"],
            ["Policy / Loan / Payment / Beneficiary", "llama-3.3-70b-versatile",
             "Reads structured API data aloud — quality + formatting accuracy needed"],
            ["Name extraction (auth)", "llama-3.3-70b-versatile",
             "Fuzzy name parsing from varied spoken input needs larger context"],
        ],
        S, [52*mm, 52*mm, 71*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("4.2  Deepgram STT Settings", S["h2"]))
    story.append(table(
        ["Setting", "Value", "Rationale"],
        [
            ["DEEPGRAM_ENDPOINTING", "300ms (try 400ms in prod)", "400ms gives callers more time — reduces false finals on long DOBs/policy numbers"],
            ["DEEPGRAM_UTTERANCE_END_MS", "1000ms", "Safety-net finalise — keeps latency acceptable"],
            ["DEEPGRAM_SMART_FORMAT", "true", "Auto-formats dates (July 15th), phone numbers, currencies — critical for PII parsing"],
            ["DEEPGRAM_INTERIM_RESULTS", "true", "Powers barge-in detection via webrtcvad"],
            ["DEEPGRAM_NO_DELAY", "true", "Disables Deepgram's internal output buffer — shaves 50-100ms"],
        ],
        S, [55*mm, 42*mm, 78*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("4.3  ElevenLabs TTS Settings", S["h2"]))
    story.append(table(
        ["Setting", "Value", "Rationale"],
        [
            ["ELEVENLABS_MODEL", "eleven_turbo_v2", "Lowest latency; acceptable quality for telephony"],
            ["ELEVENLABS_OPTIMIZE_STREAMING_LATENCY", "3 (try 4 in prod)", "4 = maximum latency optimisation; test voice quality first"],
            ["ELEVENLABS_STABILITY", "0.5", "Balanced — prevents robotic tone on long sentences"],
            ["ELEVENLABS_SIMILARITY_BOOST", "0.75", "Keeps voice consistent across short IVR phrases"],
        ],
        S, [65*mm, 30*mm, 80*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("4.4  Feature Flags (Runtime Tunable)", S["h2"]))
    story.append(table(
        ["Flag", "Default", "When to change"],
        [
            ["ENABLE_RAG", "true", "Set false if pgvector is down — FAQ returns canned response instead of crashing"],
            ["FAQ_FALLBACK_TO_ESCALATE", "false",
             "Set true when knowledge base is sparse at launch; flip to false once RAG hit rate > 75%"],
            ["MAX_AUTH_ATTEMPTS", "3", "Lower to 2 for tighter fraud controls; raise to 4 if callers complain of drop-offs"],
            ["ROUTER_MODEL", "llama-3.1-8b-instant", "Swap to llama-3.3-70b-versatile if router mis-classification rate rises above 5%"],
            ["GROQ_MODEL", "llama-3.3-70b-versatile", "Swap to llama-3.1-8b-instant as cost-saving measure once quality is validated"],
            ["DEEPGRAM_ENDPOINTING", "300", "Tune per call traffic — longer = more patience; shorter = faster turns"],
        ],
        S, [52*mm, 28*mm, 95*mm]
    ))
    return story


def build_change_mgmt(S):
    W = CONTENT_W
    story = [section_header("5. Change Management", S, W)]
    story.append(Paragraph(
        "Changes are classified by what action they require. The goal is: most tuning via env vars, "
        "code changes only for logic/flow modifications, and zero manual DB migrations for schema-less "
        "JSONB fields.", S["body"]))
    story.append(Spacer(1, 6))

    story.append(table(
        ["Change Type", "Example", "Action Required", "Downtime?"],
        [
            ["Prompt / verbiage text", "Change auth question wording", "Edit .py → git push → auto CI/CD", "None"],
            ["Feature flag", "ENABLE_RAG=false, MAX_AUTH_ATTEMPTS=2",
             "Update env var in Railway dashboard", "None (live)"],
            ["New intent route", "Add 'claims' flow", "New node .py + router.py + graph.py → push", "None (rolling)"],
            ["Knowledge base update", "Add new FAQ topic", "Edit seed_knowledge.py → run python seed_knowledge.py", "None"],
            ["LLM model swap", "ROUTER_MODEL=llama-3.3-70b-versatile", "Update env var in Railway", "None (live)"],
            ["Dependency update", "requirements.txt change", "Push → CI → image rebuild", "~30s"],
            ["Infrastructure change", "Add new Railway service", "IaC / Railway dashboard + push", "None"],
        ],
        S, [38*mm, 48*mm, 55*mm, 24*mm],
        row_colors=[(1, LTGREEN), (2, LTGREEN), (4, LTGREEN), (5, LTGREEN)]
    ))
    story.append(Paragraph("Green rows = no deployment required.", S["caption"]))

    story.append(Paragraph("5.1  Branch & Release Strategy", S["h2"]))
    story.append(table(
        ["Branch", "Purpose", "Deploy target", "Approval"],
        [
            ["feature/xxx", "New feature or fix", "None (local only)", "PR review"],
            ["master", "Stable integration branch", "Staging (auto)", "CI pass"],
            ["(any tag / SHA)", "Production release", "Production (manual)", "GitHub Env required-reviewers"],
        ],
        S, [35*mm, 60*mm, 45*mm, 35*mm]
    ))
    return story


def build_checklist(S):
    W = CONTENT_W
    story = [section_header("6. Go-Live Checklist", S, W)]

    def check_table(items, bg=LTGREY):
        rows = [(["\u2610  " + item[0], item[1]], bg if i % 2 == 0 else WHITE)
                for i, item in enumerate(items)]
        return colored_table(rows, S, [100*mm, 75*mm])

    story.append(Paragraph("6.1  One Week Before", S["h2"]))
    story.append(table(
        ["Checklist Item", "Owner / Notes"],
        [
            ["\u2610  Deploy to staging with real CNO API (not mock)", "DevOps — swap CNO_API_BASE_URL"],
            ["\u2610  Run test_call_flow.py against staging — all 6 pass", "QA"],
            ["\u2610  Run seed_knowledge.py on staging — verify RAG hit rate > 75%", "Dev"],
            ["\u2610  Set VALIDATE_TWILIO_SIGNATURE=true + TWILIO_BASE_URL", "DevOps"],
            ["\u2610  Set WS_AUTH_TOKEN to a strong random secret", "DevOps"],
            ["\u2610  Set DASHBOARD_PASSWORD (strong, random)", "DevOps"],
            ["\u2610  Set ALLOWED_ORIGINS to prod dashboard domain only", "DevOps"],
            ["\u2610  Set ELEVENLABS_OPTIMIZE_STREAMING_LATENCY=4", "Dev — test voice quality"],
            ["\u2610  Set DEEPGRAM_ENDPOINTING=400 — test with real callers", "Dev"],
            ["\u2610  Load test: 10 concurrent simulated calls via Twilio test numbers", "QA"],
            ["\u2610  Confirm Groq rate limits are sufficient for expected call volume", "Dev"],
            ["\u2610  Set up Grafana Cloud / Datadog log drain from Railway", "DevOps"],
            ["\u2610  Configure alert rules (auth rate, latency, 5xx)", "DevOps"],
        ],
        S, [110*mm, 65*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("6.2  Launch Day", S["h2"]))
    story.append(table(
        ["Checklist Item", "Owner / Notes"],
        [
            ["\u2610  Deploy production image via workflow_dispatch", "DevOps — select environment=production"],
            ["\u2610  Point Twilio phone number webhook URL to prod /webhook/voice", "DevOps"],
            ["\u2610  Verify /health returns {status: ok, environment: prod}", "QA"],
            ["\u2610  Place a live test call — complete full auth + policy inquiry", "QA"],
            ["\u2610  Monitor escalation rate for first 30 minutes", "QA / Dev on-call"],
            ["\u2610  Keep staging environment hot for rapid rollback", "DevOps"],
        ],
        S, [110*mm, 65*mm]
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("6.3  First Week Post-Launch", S["h2"]))
    story.append(table(
        ["Checklist Item", "Owner / Notes"],
        [
            ["\u2610  Review faq_rag_miss events daily — add missing knowledge", "Dev"],
            ["\u2610  Review auth funnel: where are callers dropping off?", "QA"],
            ["\u2610  Review Groq 503 rate — if > 1%, add model fallback", "Dev"],
            ["\u2610  Review intent_detected distribution — fix any mis-classified intents", "Dev"],
            ["\u2610  Tune DEEPGRAM_ENDPOINTING based on real-call feedback", "Dev"],
            ["\u2610  Set FAQ_FALLBACK_TO_ESCALATE=false once RAG hit rate > 75%", "Dev"],
            ["\u2610  Add Scenario G (FAQ utterances) to test_call_flow.py", "Dev"],
        ],
        S, [110*mm, 65*mm]
    ))
    return story


def build_secrets_ref(S):
    W = CONTENT_W
    story = [section_header("7. Environment Variables Reference", S, W)]
    story.append(Paragraph(
        "All runtime configuration is injected via environment variables. "
        "Set these in Railway Dashboard → Service → Variables.", S["body"]))
    story.append(Spacer(1, 6))

    story.append(table(
        ["Variable", "Required", "Default", "Notes"],
        [
            ["GROQ_API_KEY", "YES", "—", "Main LLM — router + all service nodes"],
            ["GROQ_MODEL", "No", "llama-3.3-70b-versatile", "FAQ, policy, loan, payment, beneficiary"],
            ["ROUTER_MODEL", "No", "llama-3.1-8b-instant", "Router only — fast single-word classifier"],
            ["OPENAI_API_KEY", "YES", "—", "Embeddings for pgvector RAG"],
            ["DEEPGRAM_API_KEY", "YES", "—", "STT"],
            ["ELEVENLABS_API_KEY", "YES", "—", "TTS"],
            ["TWILIO_ACCOUNT_SID", "YES", "—", "Webhook validation + browser client"],
            ["TWILIO_AUTH_TOKEN", "YES", "—", "Webhook signature validation"],
            ["DATABASE_URL", "YES", "—", "PostgreSQL + pgvector; LangGraph checkpoints + call history"],
            ["REDIS_URL", "YES", "redis://localhost:6379/0", "Session state"],
            ["CNO_API_BASE_URL", "YES", "—", "CNO party search + policy APIs"],
            ["VALIDATE_TWILIO_SIGNATURE", "Prod", "false", "Set true in prod"],
            ["WS_AUTH_TOKEN", "Prod", "", "WebSocket auth; set in Twilio Media Stream URL"],
            ["DASHBOARD_PASSWORD", "Prod", "", "Leave empty = no auth (dev only)"],
            ["ALLOWED_ORIGINS", "Prod", "*", "Restrict to prod dashboard domain in prod"],
            ["ENABLE_RAG", "No", "true", "Feature flag — false bypasses pgvector"],
            ["FAQ_FALLBACK_TO_ESCALATE", "No", "false", "Feature flag — true routes FAQ miss to agent"],
            ["MAX_AUTH_ATTEMPTS", "No", "3", "Feature flag — PII retries before escalation"],
            ["ENVIRONMENT", "No", "dev", "dev | uat | stg | prod — controls is_prod checks"],
            ["LOG_LEVEL", "No", "INFO", "DEBUG for troubleshooting; INFO for prod"],
        ],
        S, [52*mm, 18*mm, 42*mm, 63*mm],
        row_colors=[
            (1, LTBLUE), (2, WHITE), (3, WHITE), (4, LTBLUE), (5, LTBLUE),
            (6, LTBLUE), (7, LTBLUE), (8, LTBLUE), (9, LTBLUE), (10, LTBLUE),
            (11, LTBLUE),
        ]
    ))
    story.append(Paragraph("Blue rows = must be set in production. White rows = tunable.", S["caption"]))
    return story


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    doc = SimpleDocTemplate(
        OUT,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=14*mm, bottomMargin=12*mm,
        title="CNO IVR Production Readiness Plan",
        author="CNO IVR Team",
    )

    S = make_styles()
    story = []

    story += build_cover(S)
    story += build_stack(S)
    story.append(PageBreak())
    story += build_cicd(S)
    story.append(PageBreak())
    story += build_observability(S)
    story.append(PageBreak())
    story += build_tuning(S)
    story.append(PageBreak())
    story += build_change_mgmt(S)
    story.append(Spacer(1, 8))
    story += build_checklist(S)
    story.append(PageBreak())
    story += build_secrets_ref(S)

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(f"PDF written to: {OUT}")


if __name__ == "__main__":
    main()
