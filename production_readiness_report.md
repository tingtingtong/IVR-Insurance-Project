# CNO IVR — Production Readiness Report

**Date:** 2026-07-08
**Assessed by:** Senior Solutions Architect / DevOps Review
**Codebase:** `C:\Users\nithi\cno_ivr`
**Git branch:** `master`
**Assessment scope:** Architecture, scalability, cost, security, observability, migration roadmap

---

## Executive Summary

The CNO IVR system is a well-structured, functionally capable LangGraph-based telephony AI system. The code quality is high — the graph design is clean, error handling is thoughtful, and the dual-path architecture (webhook mode + WebSocket/Media Streams mode) is sophisticated. However, **the system is not production-ready in its current state** due to critical gaps in security, state persistence architecture, observability, and scalability. These are all fixable in 4–8 weeks with the right prioritization.

**Production readiness score: 5.5 / 10**

| Area | Score | Notes |
|------|-------|-------|
| Code quality | 8/10 | Clean, well-structured, good separation |
| Functionality | 8/10 | Auth, RAG, DTMF, barge-in all implemented |
| Security | 3/10 | No webhook auth, no dashboard auth, wildcard CORS |
| Scalability | 4/10 | In-memory store, single connection checkpointer |
| Observability | 4/10 | Structlog present, metrics missing, no alerting |
| Infrastructure | 5/10 | Docker-compose good for dev, not for prod |
| Cost efficiency | 7/10 | Good LLM choices, Redis present, PGVector smart |

---

## SECTION 1: Current Architecture Assessment

### 1.1 Architecture Diagram

```
                          INBOUND PSTN CALL
                                |
                         [Twilio Voice]
                         /             \
                        /               \
          Webhook Path (default)    WebSocket Path (Media Streams)
          POST /webhook/voice       WSS /stream
                 |                        |
          POST /webhook/gather      [CallHandler]
                 |                  /           \
                 |             [STTService]   [VADService]
                 |             Deepgram         WebRTC VAD
                 |              |                    |
                 |         transcript           barge-in signal
                 |              |
                  \____________/
                        |
               [LangGraph — cno_graph]
                  compiled at startup
                  checkpointer: PostgresSaver | MemorySaver
                        |
               [router_node] ← ChatGroq (llama-3.3-70b-versatile)
                        |
          +-------------+-------------------+
          |             |                   |
      [auth_node]  [faq_node]     [policy/payment/loan/
          |          |  |          beneficiary/contact/
     party_search  RAG  LLM        document/privacy/otp/
     auth_token   PGVector         escalation/goodbye]
          |
   [CNO Backend APIs]
   POST /party/search
   POST /auth/token
   GET  /policy/*
   POST /payment/*
                        |
               [TTSService] — OpenAI TTS-1
               PCM 24kHz → mulaw 8kHz
                        |
               [Twilio — audio back to caller]

PERSISTENCE LAYER:
  Redis       — per-call session state (WebSocket path)
               fallback: fakeredis (in-memory, single-process)
  PostgreSQL  — LangGraph checkpointer (conversation graph state)
  pgvector    — FAQ knowledge base embeddings (RAG)

TRANSIENT / IN-MEMORY:
  _calls dict — conversation_store.py (SPOF — wiped on restart)
  _chats dict — webchat sessions (same)

DASHBOARD:
  GET /dashboard/* — unauthenticated HTML/JSON API
  exposes: call transcripts, PII summary, config (masked), live logs
  POST /dashboard/config — .env file writer (password: "pass@2026" hardcoded)
```

### 1.2 What Works Well

1. **Dual-path architecture.** Webhook path (Twilio Gather STT + Polly TTS) and WebSocket Media Streams path (Deepgram + OpenAI TTS + barge-in) are both implemented and can coexist. The webhook path is battle-hardened Twilio behavior; the stream path enables real-time barge-in.

2. **LangGraph state machine.** The graph with 13 nodes is well-designed. Context switch enforcement (locked/escalate_only/open modes) is a mature pattern for multi-step telephony flows. The conditional routing is clean and testable.

3. **Authentication node.** The multi-step auth flow (phone → DOB → name fallback → caller persona) with slot-level retry counters, IDK handling, and fuzzy name matching is production-quality logic. The two-factor auth pairing (phone+DOB, phone+name, policy+DOB, policy+name) is sound.

4. **Redis for session state.** `SessionService` uses Redis with a 1-hour TTL and falls back to fakeredis gracefully. This means the WebSocket path session survives short Redis restarts during development.

5. **Exponential backoff on Groq.** `_invoke_llm_with_retry` in router.py retries on 503/429 with 1s, 2s delays — a direct bug fix for Groq rate limits under concurrent load.

6. **PII redaction in conversation_store.** Human turns are passed through `redact_turn()` before storage. This is a good privacy baseline.

7. **Structlog.** JSON-structured logging is the right choice for production log aggregation.

8. **DTMF payment collection.** The `DTMFCollector` class for PCI-compliant card/bank entry is well-implemented with backspace support and field-by-field prompting.

9. **Graceful degradation on RAG.** When PGVector is unavailable, `search_knowledge` returns `""` and the FAQ node returns a canned response instead of hallucinating.

10. **Health check.** `GET /health` exists and returns environment — ready for load balancer health probes.

### 1.3 What Is Fundamentally Not Production-Ready

1. **`_calls` dict in conversation_store.py is wiped on every process restart.** This is the dashboard's data source. Every deployment loses the call history. In a multi-instance deployment, each instance has its own dict — calls handled by instance A are invisible to instance B.

2. **Dashboard has zero authentication.** `/dashboard/calls` exposes masked PII (caller names, partially masked policy numbers, auth state) with no login. `/dashboard/config` writes to the `.env` file with a hardcoded password (`"pass@2026"`) visible in source code.

3. **Twilio webhook signature is not validated.** Any actor who discovers `/webhook/voice` or `/webhook/gather` can POST fake call events. Twilio provides an HMAC-SHA1 signature header (`X-Twilio-Signature`) for exactly this purpose — it is not checked anywhere.

4. **CORS is `allow_origins=["*"]`.** The browser client and dashboard are accessible from any origin. This exposes the dashboard API to cross-site requests from any webpage.

5. **PostgresSaver uses a single synchronous psycopg connection.** `psycopg.connect()` (blocking) is called once at startup and shared across all async graph invocations. Under concurrent load this will serialize all checkpointer reads/writes through one connection, creating a bottleneck and potentially raising `InterfaceError: connection already closed` under sustained load.

6. **The docker-compose.yml runs uvicorn with `--reload`** in the `command` line — this is a development flag that reloads Python modules on file change. It must not be used in production.

7. **No Twilio failover URL configured.** If the primary server is unreachable, Twilio has no fallback URL to try. Calls will fail with "Application Error" to the caller.

8. **OpenAI TTS has a global `_RESAMPLE_STATE = None`** in `tts.py` (unused — per-call state is correctly local). This is not a bug but is confusing and could become one if the code is refactored.

9. **`fakeredis` is listed in `services/session.py` but is not in `requirements.txt`.** If Redis goes down in production, the fallback import will fail with `ModuleNotFoundError`, crashing the WebSocket handler for every new call.

10. **No rate limiting on any endpoint.** The `/webhook/gather` endpoint runs a full LangGraph invocation (LLM + API calls) per request. A bot or misconfigured Twilio retry loop could exhaust Groq rate limits and CNO API quotas instantly.

---

## SECTION 2: Scalability Analysis

### Assumptions

- Average call duration: 5 minutes
- Average turns per call: 10 (greeting + 2 auth turns + 1 intent + 3-4 service turns + goodbye)
- LLM calls per turn: 1 (router always) + 0.3 (service nodes with LLM — FAQ, name extraction) = ~1.3 LLM calls/turn
- LLM calls per call: ~13
- RAG searches per call: ~1–2 (FAQ calls only, ~20% of calls)
- CNO API calls per call: ~3 (party_search + auth_token + 1 service API)
- Average tokens per LLM call: ~600 input + ~50 output = 650 tokens
- Total tokens per call: ~8,450

### 2.1 At 1,000 Calls/Day

**Traffic profile:**
- 1,000 calls/day = 41.7 calls/hour average
- Peak hour (assume 3x average): ~125 calls/hour = ~2.1 calls/minute
- At 5 min average duration: ~10 simultaneous calls at peak
- Concurrent LangGraph invocations at peak: ~10–15 (turns overlap)

**Can the current single-instance FastAPI server handle this?**

Yes, with caveats. A single FastAPI + uvicorn process with 4 async workers can handle 10–15 concurrent calls comfortably IF:
- Groq responds in <1s (it does for llama-3.3-70b on the paid tier)
- The Postgres checkpointer connection is not serializing writes (it will be — see bottleneck #2)
- Redis is available (session reads/writes are O(1))

**What breaks first at 1K/day:**

1. **PostgresSaver single connection.** With 15 concurrent turns each doing a checkpointer read+write, the single psycopg connection will serialize all of them. Expect P95 latency to spike to 3–5s per turn on busy periods. The caller hears dead silence during this time.

2. **Groq rate limits.** Groq's free tier has a 30 req/min limit per model. At 1K calls/day peak you're doing ~15 concurrent LLM calls/minute — this will hit the free tier limit and trigger 429s. The retry logic handles this but adds 1–3s latency.

3. **`_calls` dict memory.** At 1K calls/day with a `maxlen=100` deque, only the last 100 calls are accessible in the dashboard. This is a UX issue, not a crash issue.

4. **aiohttp ClientSession per request.** `party_search` and `auth_token` create a new `aiohttp.ClientSession()` per call. Each session opens a new TCP connection to the CNO API. At 1K calls/day this is ~3K new TCP connections/day — acceptable but inefficient. Connection pooling with a shared session would be better.

**Minimum infrastructure for 1K/day:**
- 1x FastAPI app instance (2 vCPU, 4 GB RAM)
- 1x PostgreSQL (db.t3.medium or equivalent)
- 1x Redis (cache.t3.micro)
- Groq paid tier (required — free tier will rate-limit)

### 2.2 At 5,000 Calls/Day

**Traffic profile:**
- 5,000 calls/day = 208 calls/hour average
- Peak hour (3x): ~625 calls/hour = ~10.4 calls/minute
- At 5 min average: ~52 simultaneous calls at peak
- Concurrent LangGraph invocations: ~60–80

**What is mandatory at 5K/day:**

1. **Horizontal scaling is required.** A single FastAPI instance cannot handle 60–80 concurrent WebSocket connections + LangGraph invocations. You need at least 2–3 instances behind a load balancer.

2. **PostgresSaver MUST be connection-pooled.** Use `AsyncPostgresSaver` with `asyncpg` or use PgBouncer in front of Postgres. The current single synchronous connection will become a severe bottleneck.

3. **`_calls` dict must be replaced with Redis or Postgres.** With 3 app instances, the dashboard will show different call lists depending on which instance handles the HTTP request for `/dashboard/calls`.

4. **Redis must be a managed cluster.** ElastiCache (Redis) single-node is fine at 1K/day. At 5K/day with session state per call, you want at minimum a 1-replica setup for availability.

5. **Load balancer with sticky WebSocket sessions.** Twilio Media Streams WebSocket connections must be routed to the same instance for the duration of the call. Standard sticky sessions by `CallSid` at the load balancer level (Nginx upstream hash or ALB target group stickiness).

6. **CNO API connection pooling.** Replace per-request `aiohttp.ClientSession` with a shared session pool, or use `httpx.AsyncClient` with connection limits.

7. **Groq paid tier with higher rate limits.** At 5K calls/day you're doing ~65K LLM calls/day = ~45 LLM calls/minute at peak. Groq paid tier starts at 6,000 req/min for llama models — this is sufficient, but you need to be on the paid tier.

---

## SECTION 3: Cost Analysis

All pricing as of July 2026. Estimates marked with (est.) where exact pricing was not confirmed at time of writing.

### 3.1 Twilio Costs Per Call

| Item | Rate | Per 5-min call |
|------|------|----------------|
| Inbound call (Programmable Voice) | $0.0085/min | $0.0425 |
| Speech Recognition (Gather with speech) | $0.01/15-sec segment | ~$0.06 (webhook path) |
| TTS via Polly (Say verb — Joanna) | Bundled in Twilio | $0.00 |
| Call recording (optional) | $0.0025/min | $0.0125 |
| WebSocket Media Streams | $0.004/min | $0.020 (stream path) |
| **Total (webhook path, no recording)** | | **~$0.10/call** |
| **Total (stream path, with recording)** | | **~$0.075/call** |

Note: The webhook path uses Gather speech recognition ($0.01/15-sec segment). The stream path uses Deepgram directly (not billed via Twilio) so the Twilio cost is lower.

### 3.2 Deepgram STT Costs (Stream Path)

| Item | Rate | Per 5-min call |
|------|------|----------------|
| Deepgram Nova-2 (streaming) | $0.0059/min | $0.0295 |
| **Total per call** | | **~$0.030/call** |

### 3.3 OpenAI TTS Costs (Stream Path)

| Item | Rate | Per call |
|------|------|---------|
| OpenAI TTS-1 | $15.00/1M characters | ~$0.018/call (est. 1,200 chars of TTS/call) |

### 3.4 LLM Costs (Groq)

**Groq free tier:** Up to 6,000 req/day (varies by model), 30 req/min. Suitable for development and low-volume UAT only.

**Groq paid tier (as of 2026):**
- llama-3.3-70b-versatile: $0.59/1M input tokens, $0.79/1M output tokens (est.)

| Metric | Value |
|--------|-------|
| Tokens per LLM call | ~650 (600 input + 50 output) |
| LLM calls per call | ~13 |
| Tokens per call | ~8,450 |
| Input cost per call | 8,450 × $0.59/1M = $0.0050 |
| Output cost per call | 650 × $0.79/1M = $0.0005 |
| **Total LLM cost per call** | **~$0.0055** |

### 3.5 OpenAI Embeddings (RAG)

| Item | Rate | Per call (20% FAQ rate) |
|------|------|------------------------|
| text-embedding-3-small | $0.020/1M tokens | ~$0.000002/call |

Embedding cost is negligible at this scale.

### 3.6 Infrastructure Costs

#### At 1,000 calls/day:

| Component | Spec | Monthly Cost (AWS us-east-1, est.) |
|-----------|------|-------------------------------------|
| EC2 FastAPI app | t3.medium (2 vCPU, 4 GB) | $30 |
| RDS PostgreSQL | db.t3.micro (pgvector extension) | $25 |
| ElastiCache Redis | cache.t3.micro | $15 |
| Data transfer | ~10 GB/month | $1 |
| **Total infra** | | **~$71/month** |

#### At 5,000 calls/day:

| Component | Spec | Monthly Cost (est.) |
|-----------|------|---------------------|
| EC2 FastAPI (x3) | t3.large (2 vCPU, 8 GB) × 3 | $180 |
| ALB load balancer | + data processing | $25 |
| RDS PostgreSQL | db.t3.medium (Multi-AZ) | $100 |
| ElastiCache Redis | cache.t3.small (1 replica) | $35 |
| Data transfer | ~50 GB/month | $5 |
| **Total infra** | | **~$345/month** |

### 3.7 Comprehensive Cost Comparison Table

| Component | 1K calls/day (monthly) | 5K calls/day (monthly) |
|-----------|------------------------|------------------------|
| Twilio (inbound + recording) | $3,750 | $18,750 |
| Deepgram STT | $885 | $4,425 |
| OpenAI TTS-1 | $540 | $2,700 |
| Groq LLM (paid tier) | $165 | $825 |
| OpenAI Embeddings | ~$1 | ~$3 |
| EC2 / Compute | $30 | $180 |
| RDS PostgreSQL | $25 | $100 |
| Redis | $15 | $35 |
| Load Balancer | $0 | $25 |
| **TOTAL / month** | **~$5,411** | **~$27,043** |
| **Cost per call** | **~$0.18** | **~$0.18** |

**Key insight:** Twilio is ~70% of the total cost. The infrastructure is almost negligible in comparison. Reducing cost means reducing call duration (faster auth = fewer turns) or switching telephony providers — not optimizing the LLM.

---

## SECTION 4: What Must Change for Production

### 4.1 In-Memory Store Must Become Persistent

**Current:** `_calls: dict` and `_chats: dict` in `conversation_store.py` are Python process-level dicts. `_call_order` is a `deque(maxlen=100)` — you can only see the last 100 calls.

**Problems:**
- All dashboard data (call transcripts, auth state, intent history, recording URLs) is lost on every restart or deployment
- Multi-instance deployment: instance A cannot see calls handled by instance B
- No persistence = no post-call analytics, no audit trail, no compliance evidence

**Recommended fix:**
Replace with Redis Hash or PostgreSQL `calls` table. Redis is already in the stack. Each call can be stored as `cno:call:{call_sid}` with a 30-day TTL. The dashboard API reads from Redis. Migration effort: 1–2 days.

Alternatively, write to a `calls` PostgreSQL table at call end, and use Postgres for the dashboard queries. This gives SQL-queryable history for reporting. Migration effort: 2–3 days.

### 4.2 LangGraph Checkpointer

**Current:** At startup, `main.py` attempts to connect to PostgresSaver. If Postgres is unreachable, it silently falls back to `MemorySaver`. In production, this fallback means:
- All conversation state is lost on restart mid-call
- No cross-instance sharing of graph state
- The fallback is logged at WARNING level — easy to miss

**Problems with current PostgresSaver:**
- Uses a single synchronous `psycopg.connect()` call, not async
- The connection is stored on the `checkpointer` object and shared across all concurrent async graph invocations
- Under concurrent load, this will serialize all reads/writes and create a bottleneck

**Recommended fixes:**
1. Use `AsyncPostgresSaver` with `asyncpg` (available in `langgraph-checkpoint-postgres >= 2.x`) — true async, connection-pooled
2. In production: remove the MemorySaver fallback entirely. If Postgres is down, fail fast at startup rather than silently degrading
3. Use PgBouncer in transaction pooling mode in front of PostgreSQL to handle connection spikes

Migration effort: 1 day.

### 4.3 Authentication and Security

**These are not optional for a financial services / insurance IVR:**

**a) Twilio Webhook Signature Validation**

Every POST to `/webhook/voice`, `/webhook/gather`, `/webhook/status`, `/webhook/recording-status` should validate the `X-Twilio-Signature` header using `twilio.request_validator.RequestValidator`. Without this, anyone who discovers your URL can inject fake calls.

```python
# Pattern to add to every webhook handler:
from twilio.request_validator import RequestValidator
validator = RequestValidator(settings.twilio_auth_token)
valid = validator.validate(url, form_data, signature_header)
```

Effort: 4 hours.

**b) Dashboard Authentication**

The `/dashboard/*` endpoints expose caller names, partial policy numbers, auth status, and masked PII. In a production environment, this must be protected at minimum with:
- HTTP Basic Auth (trivial — FastAPI `HTTPBasic` dependency)
- Or better: token-based auth with a separate secret

The `/dashboard/config` endpoint writes to `.env` with a hardcoded password `"pass@2026"` in plaintext in source code. This must be:
1. Moved to an environment variable (`DASHBOARD_PASSWORD`)
2. Hashed (bcrypt comparison) rather than plaintext comparison
3. Rate-limited to prevent brute force

Effort: 4–8 hours.

**c) CORS**

`allow_origins=["*"]` in `main.py` allows any webpage to make cross-origin requests to your API. For a production IVR backend that also serves a dashboard:
- Set `allow_origins` to the specific dashboard domain
- Or add `allow_origins=[]` (block all CORS) if the dashboard is served from the same origin

Effort: 30 minutes.

**d) Input Validation / Request Size Limits**

The `/webhook/gather` handler reads `form.get("SpeechResult")` without length validation. A malicious POST with a 10MB SpeechResult string would be passed to the LLM. Add `max_length` validation.

Effort: 1 hour.

### 4.4 Observability and Monitoring

**What is currently in place:**
- `structlog` with JSON output — good foundation
- `ivr.log` file via the `_Tee` class in `main.py` — basic but fragile (stdout/stderr redirection)
- `log_event()` calls in nodes for call-level events — good
- `/dashboard/calls` for call-level monitoring

**What is missing:**

| Missing | Why It Matters |
|---------|----------------|
| Prometheus metrics endpoint | Cannot alert on LLM latency, error rate, call success rate |
| Distributed tracing (OpenTelemetry) | Cannot diagnose which node is slow in a multi-service call |
| Alerting (PagerDuty / SNS / Slack) | No one is notified when Groq is down or error rate spikes |
| Health check for dependencies | `/health` returns `{"status": "ok"}` regardless of Groq/Postgres/Redis state |
| Call completion rate metric | No measure of calls that end in `goodbye` vs `escalation` vs error |
| LLM token usage tracking | Cannot detect prompt bloat or runaway token consumption |

**Recommended stack:**
- **Metrics:** Add `prometheus-fastapi-instrumentator` (2 lines of code) for HTTP metrics. Add custom counters for LLM calls, RAG hits, auth success rate, escalation rate.
- **Alerting:** AWS CloudWatch Alarms or Grafana alerts on error rate > 5% and P95 latency > 4s
- **Tracing:** OpenTelemetry SDK with OTLP export to Grafana Tempo or AWS X-Ray — worth adding once at 5K/day scale

Effort: 1–2 days for metrics + alerting basics.

### 4.5 High Availability

**Single points of failure:**

| Component | Current State | Failure Impact |
|-----------|---------------|----------------|
| FastAPI app | Single process | All calls fail |
| PostgreSQL | Single instance (docker-compose) | Checkpointer falls back to MemorySaver; call state lost on restart |
| Redis | Single instance | Session state unavailable; fakeredis fallback kicks in — all instances get isolated in-memory state |
| Groq API | External SaaS | Router retries (1s, 2s) then escalates caller. Good. |
| CNO Backend API | External SaaS | party_search fails → auth fails → caller escalated. Acceptable. |
| Deepgram | External SaaS | STT fails → transcript is empty → silence loop → caller escalated |

**Health check gap:** `GET /health` always returns `{"status": "ok"}`. It should verify Postgres reachability, Redis ping, and Groq reachability before returning 200. This allows a load balancer to remove the instance from rotation if its dependencies are unhealthy.

**Deployment strategy:** The current setup has no rolling deployment capability. A new deployment restarts the server, dropping all in-flight WebSocket connections (active calls). For zero-downtime deployment:
1. Deploy new version in a second instance
2. Stop routing new calls to the old instance via load balancer
3. Wait for in-flight calls to drain (max call duration = 10–15 min)
4. Terminate old instance

**Twilio failover:** Configure a fallback URL in the Twilio console under Phone Numbers → Voice. The fallback URL should point to a simple TwiML that says "We are experiencing technical difficulties, please call back later" rather than silent failure.

### 4.6 Groq LLM Dependency

**Rate limits:**
- **Free tier:** 30 req/min, 6,000 req/day for llama-3.3-70b. Completely insufficient for production.
- **Paid tier:** 6,000 req/min. Adequate for up to ~5K calls/day.

**Current behavior when Groq is down:** The `_invoke_llm_with_retry` function retries twice (1s + 2s delay = 3s total wait), then falls back to `_FLOW_TO_INTENT.get(active_flow, "escalate")`. This means:
- If mid-flow: the caller stays in their current flow (good — no disruption for policy/payment questions)
- If at first intent classification: the caller is escalated to an agent

This is acceptable graceful degradation, but the 3-second retry delay adds noticeable silence to the caller experience. Consider reducing retry delay to 500ms for voice applications.

**Fallback LLM options:**
- `langchain-openai` is already in `requirements.txt` — GPT-4o-mini can be used as a hot fallback
- Add an environment variable `LLM_FALLBACK=openai` and a try/except at the model level

**Intent caching:** The router_node classifies intents using the full utterance. Common phrases like "pay my bill", "check my balance", "beneficiary change" could be cached (Redis, TTL 1 hour) to skip LLM calls entirely. Estimated cache hit rate: 30–40% of turns, saving ~$0.002/call. Low priority at current scale.

### 4.7 Performance Benchmarks Needed

**Target latency for voice (caller-perceived):**
- Caller finishes speaking: T=0
- Deepgram endpointing fires (300ms silence): T+300ms
- LangGraph invocation: T+300ms to T+800ms (500ms for Groq at P50)
- OpenAI TTS first chunk: T+800ms to T+1,300ms (500ms for TTS-1)
- Caller hears first audio: **T+1,300ms (P50 target)**
- Acceptable maximum: T+3,000ms before caller perceives unacceptable delay

**Current bottleneck breakdown (estimated):**
| Stage | P50 | P95 | Notes |
|-------|-----|-----|-------|
| Deepgram endpointing | 300ms | 800ms | Configurable (currently 300ms) |
| LangGraph + Groq (router) | 400ms | 1,200ms | Groq llama-3.3-70b |
| LangGraph + Groq (service node) | 300ms | 900ms | ~30% of turns |
| CNO API calls | 200ms | 800ms | party_search, auth_token |
| OpenAI TTS first chunk | 400ms | 900ms | TTS-1 streaming |
| Postgres checkpointer | 50ms | 500ms | single connection bottleneck |
| **Total P50** | **~1,650ms** | | Within acceptable range |
| **Total P95** | **~4,100ms** | | Borderline — callers may notice |

**Recommendation:** Set up a simple load test (locust or k6 with simulated Twilio webhooks) before go-live to measure actual P95 under 10–15 concurrent calls.

### 4.8 PGVector / RAG

**Current state:**
- `services/rag.py` uses `langchain_community.vectorstores.PGVector` — synchronous `similarity_search()` called inside an async function with no `asyncio.to_thread()` wrapper. This blocks the event loop during the vector search.
- Vector store is lazily initialized on first call — no pre-warming at startup
- No explicit seeding script visible. If the `insuranceCompany_knowledge` collection in Postgres is empty, every FAQ call gets a canned "I don't have specific information" response

**Problems:**
1. Synchronous `similarity_search()` blocks the async event loop. Under concurrent load, every FAQ call will stall all other async operations on that event loop tick.
2. If Postgres restarts, `_vector_store` (module-level singleton) holds a stale connection. The next FAQ call will fail silently (exception caught, returns `""`). But the singleton is not reset, so all subsequent calls also fail until the process restarts.
3. No knowledge base seeding documented or scripted — unclear if the production DB has content.

**Recommended fixes:**
1. Wrap `similarity_search` in `asyncio.to_thread()` — 5 minute fix
2. Reset `_vector_store = None` in the exception handler so the connection is re-established on the next call
3. Add a `seed_knowledge_base.py` script and run it as part of the deployment process
4. At 5K/day scale, consider Pinecone ($70/month) for true async vector search with better SLA

### 4.9 Error Handling and Graceful Degradation

| Failure Scenario | Current Behavior | Acceptable? |
|------------------|------------------|-------------|
| Groq 503/429 | Retry x2 then escalate | Yes |
| Groq total outage | Escalate to agent after 3s | Yes |
| CNO party_search down | Returns `{success: False}` → auth fails → escalate | Yes |
| CNO auth_token down | Returns `""` → auth node returns escalation | Yes |
| Postgres down at startup | Falls back to MemorySaver (logged at WARNING) | No — should alert/fail in prod |
| Postgres down mid-call | MemorySaver still works for that process | Acceptable |
| Redis down | Falls back to fakeredis (if installed) | No — fakeredis not in requirements.txt |
| Deepgram error | STT connection closes; transcript callback stops | No recovery path visible |
| OpenAI TTS error | `_stream_tts` logs error; caller hears silence | Should speak fallback via Twilio Say |
| WebSocket disconnect mid-call | `_cleanup()` is called; Deepgram and TTS are stopped | Yes |

---

## SECTION 5: Recommended Production Architecture

### 5.1 For 1,000 Calls/Day

**Platform recommendation: AWS (or equivalent)**

Rationale: The system uses PostgreSQL (RDS), Redis (ElastiCache), and Docker containers. AWS ECS Fargate is the simplest managed container platform that integrates with these services without managing EC2 instances.

**Architecture:**

```
Internet
    |
[Twilio Edge Network]
    |  (webhooks + WebSocket)
    |
[AWS Application Load Balancer]
    |  HTTPS termination, WebSocket upgrade support
    |  Sticky sessions by X-CallSid header for WebSocket path
    |
[ECS Fargate — 2 tasks, 1 vCPU / 2 GB RAM each]
   task-1   task-2
    |           |
    +-----------+
          |
    [ElastiCache Redis — cache.t3.micro, 1 node]
    [RDS PostgreSQL — db.t3.small, pgvector extension]
          |
    [Secrets Manager — all API keys]
```

**Instance sizes:**
- ECS Fargate tasks: 1 vCPU, 2 GB RAM, 2 tasks (1 for redundancy, handles 10 concurrent calls each)
- RDS PostgreSQL: db.t3.small (2 vCPU, 2 GB) — sufficient for 10 concurrent connections
- ElastiCache Redis: cache.t3.micro (1 vCPU, 0.5 GB) — sufficient for 1K calls/day session state

**Estimated total monthly cost at 1K/day:**
- Twilio: $3,750
- Deepgram: $885
- OpenAI TTS: $540
- Groq paid: $165
- ECS Fargate (2 tasks): $25
- RDS db.t3.small: $35
- ElastiCache cache.t3.micro: $15
- ALB: $20
- Secrets Manager: $2
- **Total: ~$5,437/month**

**Team to operate:** 1 DevOps engineer (part-time), 1 backend developer on-call.

### 5.2 For 5,000 Calls/Day

**Architecture changes from 1K/day:**

```
[AWS Application Load Balancer]
    |
[ECS Fargate — 4 tasks, 2 vCPU / 4 GB RAM each]
   task-1  task-2  task-3  task-4
    |
[ElastiCache Redis — cache.t3.medium, 1 primary + 1 replica]
[RDS PostgreSQL — db.t3.medium, Multi-AZ, pgvector]
[PgBouncer sidecar] — connection pooling for PostgresSaver
```

**Auto-scaling triggers:**
- Scale ECS tasks up when: CPU > 70% for 3 minutes OR concurrent WebSocket connections > 40 per task
- Scale down when: CPU < 30% for 10 minutes
- Minimum tasks: 2 (always on for HA), Maximum tasks: 6

**When to add load balancer:** Already needed at 5K/day.

**When to add read replicas:** When dashboard query load (reads from `calls` table for reporting) impacts write latency on the primary. Typically at 10K+ calls/day.

**Estimated total monthly cost at 5K/day:**
- Twilio: $18,750
- Deepgram: $4,425
- OpenAI TTS: $2,700
- Groq paid: $825
- ECS Fargate (4 tasks): $100
- RDS db.t3.medium Multi-AZ: $100
- ElastiCache cache.t3.medium: $35
- ALB: $25
- **Total: ~$26,960/month**

---

## SECTION 6: Migration Roadmap

Priority order: Security first (compliance risk), then stability (data loss risk), then scale.

| Priority | Change | Effort | Impact | Approx Cost |
|----------|--------|--------|--------|-------------|
| P0 | Validate Twilio webhook signatures (X-Twilio-Signature) | 4 hours | Prevents fake call injection | $0 |
| P0 | Add auth to `/dashboard/*` endpoints | 4 hours | Prevents PII exposure | $0 |
| P0 | Move `_CFG_PASSWORD` to env var + hash comparison | 2 hours | Prevents .env file tampering | $0 |
| P0 | Remove `--reload` flag from docker-compose command | 10 min | Stability | $0 |
| P1 | Add `fakeredis` to requirements.txt | 5 min | Prevents crash on Redis failure | $0 |
| P1 | Replace `_calls` dict with Redis-backed store | 1–2 days | Dashboard survives restarts + multi-instance | $15/month Redis |
| P1 | Switch to `AsyncPostgresSaver` with connection pool | 1 day | Removes single-connection bottleneck | $0 |
| P1 | Restrict CORS to specific origins | 30 min | Security baseline | $0 |
| P1 | Add dependency health checks to `/health` | 4 hours | Enables proper LB health probing | $0 |
| P2 | Add Prometheus metrics endpoint | 1 day | Enables alerting and dashboards | $20/month Grafana Cloud |
| P2 | Add alerting (error rate > 5%, P95 > 4s) | 4 hours | On-call visibility | $0–$50/month |
| P2 | Wrap `similarity_search` in `asyncio.to_thread()` | 1 hour | Unblocks event loop on FAQ calls | $0 |
| P2 | Fix PGVector singleton stale connection bug | 1 hour | Prevents silent RAG failure after Postgres restart | $0 |
| P2 | Add Twilio failover URL in console | 30 min | Callers hear error message instead of silence | $0 |
| P2 | Document + script knowledge base seeding | 4 hours | Ensures FAQ content is loaded in prod | $0 |
| P2 | Add rate limiting on /webhook/gather | 4 hours | Prevents LLM quota exhaustion | $0 |
| P3 | Add LLM fallback (OpenAI GPT-4o-mini when Groq is down) | 1 day | Eliminates LLM as SPOF | ~$50/month buffer |
| P3 | Move to ECS Fargate + ALB | 3–5 days | Horizontal scalability, managed infra | $45/month |
| P3 | Add OpenTelemetry distributed tracing | 2 days | Root-cause diagnosis for latency spikes | $0–$50/month |
| P3 | Implement intent caching in Redis | 1 day | ~30% reduction in LLM calls | Save ~$50/month at 1K |
| P4 | Replace aiohttp per-request sessions with shared client | 4 hours | TCP connection efficiency | $0 |
| P4 | Load test with simulated Twilio traffic | 3 days | Validates P95 latency before go-live | $0 |

**Recommended sprint plan:**
- **Week 1:** All P0 items + P1 security items. No code ships to production without these.
- **Week 2:** P1 persistence and checkpointer fixes. These are the most impactful technical changes.
- **Week 3:** P2 observability, health checks, RAG fixes.
- **Week 4:** P3 infrastructure migration to ECS + load testing.

---

## SECTION 7: Cost-Effectiveness Trade-offs

### 7.1 Groq vs OpenAI vs Anthropic for LLM

The system currently uses Groq (llama-3.3-70b-versatile) for all inference: routing, name extraction, FAQ responses, and all service nodes.

| Provider | Model | Input price/1M tokens | Output price/1M tokens | P50 latency | Reliability |
|----------|-------|-----------------------|------------------------|-------------|-------------|
| Groq | llama-3.3-70b | $0.59 | $0.79 | ~300ms | Good (paid tier) |
| OpenAI | GPT-4o-mini | $0.15 | $0.60 | ~700ms | Excellent |
| Anthropic | Claude Haiku 3.5 | $0.80 | $4.00 | ~600ms | Excellent |
| Anthropic | Claude Haiku 3 | $0.25 | $1.25 | ~500ms | Excellent |

**Analysis:**
- **Groq is the best choice for latency-sensitive voice** at 1K–5K/day. The ~300ms P50 latency vs ~700ms for GPT-4o-mini directly translates to a 400ms improvement in caller-perceived response time.
- **Reliability concern:** Groq runs open-source models on specialized hardware. It has had capacity issues historically (hence the 503 retry logic in the codebase). For a production IVR serving real customers, this is a risk.
- **Recommendation for 1K/day:** Keep Groq as primary. Add GPT-4o-mini as fallback (only used when Groq returns 503 after 2 retries). Net cost impact: <$10/month for fallback traffic.
- **Recommendation for 5K/day:** Same. The latency advantage of Groq is worth the added complexity of a fallback.

**Cost at 1K/day:**
- Groq: $165/month
- GPT-4o-mini (fallback, 5% of traffic): ~$5/month
- Total: ~$170/month

### 7.2 Twilio vs Alternative Telephony

| Provider | Inbound voice | Speech recognition | Pros | Cons |
|----------|--------------|-------------------|------|------|
| Twilio | $0.0085/min | $0.01/15s segment | Excellent docs, SDKs, reliability | Most expensive |
| Plivo | $0.005/min | $0.008/15s segment | ~35% cheaper | Smaller support ecosystem |
| Vonage | $0.004/min | $0.006/15s | Cheapest | Less reliable STT; less active development |
| Bandwidth | $0.004/min | $0.008/15s | Carrier-grade reliability | Complex setup, enterprise-only pricing |

**Analysis:** Twilio costs ~$0.10/call vs Plivo at ~$0.065/call. At 1K calls/day that is a saving of ~$1,350/month. At 5K calls/day: ~$6,750/month saving. The savings are significant at scale.

**Recommendation:** Stay on Twilio for go-live (reliability, SDK quality). Evaluate Plivo at 5K+ calls/day when the cost delta justifies the migration risk.

**Important note:** The WebSocket Media Streams path (Deepgram STT) avoids Twilio's per-segment speech recognition charge. Using Deepgram instead of Twilio Gather saves ~$0.06/call in STT costs. This is already a good architecture choice.

### 7.3 PGVector vs Pinecone vs Weaviate for RAG

| Solution | Cost | Throughput | Async support | Complexity |
|----------|------|-----------|---------------|------------|
| PGVector (current) | $0 (shared Postgres) | Good for <100K chunks | No (blocks event loop) | Low |
| Pinecone Serverless | $0.096/1M queries | Excellent | Yes (REST API) | Low |
| Weaviate Cloud | $25/month starter | Excellent | Yes (REST API) | Medium |

**Analysis:** At 1K–5K calls/day with ~200 calls × 20% FAQ rate = 200 RAG queries/day, PGVector is entirely sufficient. At this query volume, Pinecone would cost ~$0.02/day — not worth the migration.

**Recommendation:** Keep PGVector. Fix the async blocking bug (wrap in `asyncio.to_thread()`). Only migrate to Pinecone or Weaviate if the knowledge base grows beyond 50K chunks or query latency exceeds 500ms.

### 7.4 Single Server vs Containerized vs Serverless

| Option | Pros | Cons | Suitable at |
|--------|------|------|-------------|
| Single EC2 | Simple, predictable | No HA, hard to scale | <500 calls/day |
| ECS Fargate (containers) | Managed, auto-scaling, no EC2 mgmt | Slightly more complex | 500–50K calls/day |
| EC2 Auto Scaling Group | Full control | EC2 patching overhead | 5K+ calls/day with custom needs |
| Serverless (Lambda) | Zero infra | Cannot hold WebSocket connections; cold start kills latency; LangGraph state model incompatible | Not suitable |

**Recommendation:** ECS Fargate with ALB. It handles the WebSocket sticky session requirement, auto-scales based on CPU/memory, and requires no EC2 management. The Dockerfile is already written — containerization is done.

**Serverless is explicitly not viable** for this system because:
1. Twilio Media Streams WebSocket connections must be held open for the duration of the call (minutes)
2. LangGraph's in-process graph compilation happens at startup — cold starts would add 5–10 seconds
3. Lambda's 15-minute timeout is too short for long calls with multiple retries

### 7.5 MemorySaver vs PostgresSaver

| Checkpointer | When it matters | Risk |
|--------------|-----------------|------|
| MemorySaver | Single instance, dev/test | State lost on restart; no sharing across instances |
| PostgresSaver | Multi-instance, production | Requires Postgres availability; adds ~50ms per turn |

**In production, PostgresSaver is mandatory** the moment you have more than one app instance, because:
- The LangGraph thread_id is the `call_sid`
- If turn 1 is handled by instance A and turn 2 is handled by instance B (possible with round-robin load balancing), instance B has no knowledge of turn 1's state without the shared checkpointer

The 50ms overhead per turn from PostgresSaver is negligible compared to the 300–700ms LLM latency.

**Note:** The current implementation uses sticky sessions (WebSocket connections must go to the same instance), which mitigates the multi-instance problem for the stream path. But the webhook path (`/webhook/gather`) is stateless HTTP — any instance can handle any turn. Without PostgresSaver, turn 2 of a webhook-path call might land on a different instance than turn 1, losing all auth state.

---

## SECTION 8: Quick Wins (Implement in Less Than 1 Week)

These changes can each be done in under 4 hours and together significantly increase production readiness:

**1. Add Twilio signature validation (4 hours, critical security)**
Add `RequestValidator` to every `/webhook/*` POST handler. This is a single shared FastAPI dependency using `twilio.request_validator.RequestValidator`. Without this, the system is open to fake call injection.

**2. Add HTTP Basic Auth to `/dashboard/*` (2 hours, critical security)**
One FastAPI `Depends(HTTPBasic)` on the dashboard router. Move credentials to env vars. This prevents PII exposure through the dashboard API.

**3. Move dashboard password to env var and hash it (1 hour, critical security)**
The `_CFG_PASSWORD = "pass@2026"` in `dashboard.py` is in plaintext in source code. Move to `DASHBOARD_CONFIG_PASSWORD` env var; compare with `secrets.compare_digest()`.

**4. Add `fakeredis` to requirements.txt (5 minutes, prevents crash)**
The `services/session.py` imports `fakeredis.aioredis` as a fallback when Redis is unavailable. If `fakeredis` is not installed (it is not in `requirements.txt`), the import fails and the WebSocket handler crashes on every new call when Redis is down.

**5. Remove `--reload` from docker-compose.yml command (10 minutes, stability)**
`uvicorn main:app ... --reload` restarts the server on every file change. This drops all in-flight calls. Change to `uvicorn main:app --host 0.0.0.0 --port 8080 --workers 4`.

**6. Restrict CORS origins (30 minutes, security baseline)**
Change `allow_origins=["*"]` to `allow_origins=[settings.dashboard_origin]` where `dashboard_origin` is a new env var. Default to `["http://localhost:3000"]` in development.

**7. Wrap `similarity_search` in `asyncio.to_thread()` (1 hour, performance)**
In `services/rag.py`, the synchronous `store.similarity_search(query, k=k)` blocks the async event loop. Wrap it: `docs = await asyncio.to_thread(store.similarity_search, query, k=k)`. This prevents FAQ calls from stalling all other concurrent call turns.

**8. Strengthen the `/health` endpoint to check dependencies (4 hours, operability)**
Current `/health` always returns `{"status": "ok"}`. Extend it to ping Redis and check Postgres connectivity. Return HTTP 503 if any critical dependency is down. This enables a load balancer to properly remove the instance from rotation.

**9. Add Twilio failover URL in Twilio console (30 minutes, caller experience)**
In the Twilio console: Phone Numbers → your number → Voice Configuration → "A call comes in" → set Fallback URL to a static TwiML Bin that says "We are experiencing technical difficulties. Please call back shortly." This ensures callers hear something useful if the server is down.

**10. Fix the `docker-compose.yml` port mismatch (10 minutes, correctness)**
The `Dockerfile` exposes port 8000 and the CMD starts uvicorn on port 8000. The `docker-compose.yml` maps `8080:8080` and the `command` also uses port 8080. The app settings use port 8080. The `Dockerfile` EXPOSE is therefore wrong — it should expose 8080. This is a documentation issue but will confuse anyone using the Dockerfile directly without docker-compose.

---

## Appendix: Files Assessed

| File | Status | Key Findings |
|------|--------|-------------|
| `main.py` | Reviewed | stdout/stderr Tee for live logs is fragile; PostgresSaver uses sync connect |
| `config/settings.py` | Reviewed | Clean pydantic-settings; all fields validated |
| `requirements.txt` | Reviewed | Missing `fakeredis`; all other deps appropriate |
| `docker-compose.yml` | Reviewed | `--reload` flag in prod command; port mismatch with Dockerfile |
| `Dockerfile` | Reviewed | Port 8000 vs app port 8080 mismatch |
| `.env.example` | Reviewed | Comprehensive; good documentation of each variable |
| `webhooks/twilio_voice.py` | Reviewed | No signature validation; clean TwiML construction |
| `webhooks/twilio_stream.py` | Reviewed | WebSocket handler is solid; barge-in implementation correct |
| `webhooks/dashboard.py` | Reviewed | No auth; hardcoded password in source; exposes config endpoint |
| `core/graph/graph.py` | Reviewed | Clean graph definition; 13 nodes; routing logic correct |
| `core/graph/state.py` | Reviewed | Well-typed TypedDict; all state fields documented |
| `core/graph/nodes/router.py` | Reviewed | Good retry logic; keyword pre-checks save LLM calls; context switch config excellent |
| `core/graph/nodes/auth.py` | Reviewed | Production-quality auth state machine; IDK handling; persona identification |
| `core/graph/nodes/faq.py` | Reviewed | RAG integration correct; AUTH_OVERRIDE guard is important |
| `services/conversation_store.py` | Reviewed | In-memory dict is the primary production blocker |
| `services/rag.py` | Reviewed | Sync call blocks event loop; stale connection not reset on exception |
| `services/session.py` | Reviewed | Redis with fakeredis fallback; TTL management correct |
| `services/stt.py` | Reviewed | Deepgram streaming correct; no recovery if connection drops mid-call |
| `services/tts.py` | Reviewed | OpenAI TTS PCM→mulaw pipeline correct; global `_RESAMPLE_STATE` is unused |
| `services/vad.py` | Reviewed | WebRTC VAD barge-in implementation correct; 60ms threshold is good |
| `core/tools/party_search.py` | Reviewed | New aiohttp session per call; fuzzy name matching with difflib |
| `core/tools/auth_token.py` | Reviewed | 10s timeout; correct empty-token escalation |

---

*Report generated 2026-07-08. Pricing estimates are based on publicly available information as of the report date and may vary. Infrastructure costs are estimates for AWS us-east-1.*
