# CNO IVR — MCP Integration Architecture

## Table of Contents

1. [What is MCP and Why It Fits Here](#1-what-is-mcp-and-why-it-fits-here)
2. [Five MCP Server Boundaries](#2-five-mcp-server-boundaries)
3. [Integration Pattern with LangGraph](#3-integration-pattern-with-langgraph)
4. [Security Architecture](#4-security-architecture)
5. [Performance Analysis](#5-performance-analysis)
6. [Multi-Agent Future Vision](#6-multi-agent-future-vision)
7. [Phased Rollout Plan](#7-phased-rollout-plan)
8. [File Structure for MCP Servers](#8-file-structure-for-mcp-servers)

---

## 1. What is MCP and Why It Fits Here

### MCP Overview

The **Model Context Protocol (MCP)** is an open standard from Anthropic that provides a unified JSON-RPC 2.0 interface for connecting AI models to external data sources and tools. It defines three primitives:

| Primitive | Direction | Purpose |
|-----------|-----------|---------|
| **Tools** | Model-invoked | Functions the LLM can call (e.g., `party_search`, `payment_history`) |
| **Resources** | Application-controlled | Read-only data exposed to the client (e.g., API base URL, ACH script) |
| **Prompts** | User-invoked | Reusable prompt templates with parameters (e.g., RAG grounding template) |

**Transport options:**

- **stdio** — Server runs as a child process; communication over stdin/stdout. Sub-5ms overhead. Ideal for single-machine deployments.
- **SSE (Server-Sent Events)** — Server runs as an HTTP service; communication over HTTP POST + SSE stream. 10-30ms overhead. Required for multi-machine or containerized deployments.

### Current Pain Points MCP Solves

**1. Tight coupling between graph nodes and backend APIs**

Every tool file (`party_search.py`, `holding_inquiry.py`, `payment_api.py`, `auth_token.py`) creates its own `aiohttp.ClientSession`, builds headers with `settings.cno_api_key`, and handles errors independently. There are 6 separate `aiohttp.ClientSession()` instantiations across 4 files, each with duplicated auth header construction:

```python
# Current: duplicated in every tool file
headers = {
    "Authorization": f"Bearer {settings.cno_api_key}",
    "Content-Type":  "application/json",
}
async with aiohttp.ClientSession() as session:
    async with session.post(url, json=payload, headers=headers, ...) as resp:
```

With MCP, the insurance backend server owns a single connection pool and auth strategy. Graph nodes call `tools/call` with parameters — they never see HTTP details.

**2. No tool reuse across agents**

The current tools are Python functions imported directly by graph nodes. A future chat agent, claims agent, or email agent would need to duplicate or re-import the same functions, creating version drift. MCP servers expose tools via `tools/list` — any MCP client can discover and call them.

**3. Backend lock-in**

- RAG is hardcoded to PGVector via `langchain_community.vectorstores.PGVector` in `services/rag.py`
- Telephony is hardcoded to Twilio REST API in `webhooks/twilio_stream.py` (line 527: `from twilio.rest import Client`)
- Analytics is coupled to MLflow + PostgreSQL JSONB

MCP creates an abstraction boundary. Swapping PGVector for Pinecone, or Twilio for Amazon Connect, means changing only the MCP server implementation — no graph node changes.

**4. Testing complexity**

Testing `auth_node` (839 lines) requires mocking `party_search()`, `check_auth_success()`, and `acquire_access_token()` at the Python import level. With MCP, tests spin up a mock MCP server that returns canned responses — no import patching needed.

---

## 2. Five MCP Server Boundaries

### Server 1: Insurance Backend (`cno-insurance-mcp`)

**Wraps:** `core/tools/party_search.py`, `core/tools/auth_token.py`, `core/tools/holding_inquiry.py`, `core/tools/payment_api.py`

#### Tools (9)

| Tool | Source Function | Input | Output |
|------|----------------|-------|--------|
| `party_search` | `party_search.party_search()` | `phone?, policy_number?, dob?, first_name?, last_name?, zipcode?` | `{success, parties[], error}` |
| `validate_pii_match` | `party_search.validate_pii_match()` | `party: object, pii_collected: object` | `matched_fields: string[]` |
| `check_auth_success` | `party_search.check_auth_success()` | `party: object, pii_collected: object, fuzzy_threshold?: float` | `boolean` |
| `acquire_access_token` | `auth_token.acquire_access_token()` | `party_key: string, company_code: string` | `token: string` |
| `holding_inquiry` | `holding_inquiry.holding_inquiry()` | `policy_number: string, access_token: string` | `{success, data, error}` |
| `payment_history` | `holding_inquiry.payment_history()` | `policy_number: string, access_token: string` | `{success, transactions[], error}` |
| `loan_inquiry` | `holding_inquiry.loan_inquiry()` | `policy_number: string, access_token: string` | `{success, data, error}` |
| `process_card_payment` | `payment_api.process_card_payment()` | `policy_number, access_token, amount, card_number, expiry, cvv` | `{success, confirmation, error}` |
| `process_ach_payment` | `payment_api.process_ach_payment()` | `policy_number, access_token, amount, routing_number, account_number, account_type?` | `{success, confirmation, error}` |

#### Resources (2)

| Resource URI | Source | Description |
|-------------|--------|-------------|
| `cno://config/api-base-url` | `settings.cno_api_base_url` | Backend API base URL (currently `https://api.insuranceCompany.example.com`) |
| `cno://config/ach-auth-script` | `payment_api.ACH_AUTHORIZATION_SCRIPT` | Verbatim ACH authorization script that must be read to the caller before ACH payment |

#### PCI Compliance

The payment tools handle PCI-sensitive data (card numbers, CVVs, bank account numbers). The current architecture already enforces a critical security boundary:

```
Caller ──DTMF──► Twilio ──digits──► DTMFCollector ──data──► payment_api ──JWT──► Backend
                                         ↑
                              Never passes through LLM
```

In the MCP architecture, this flow is preserved:
- Card/bank data arrives via DTMF (`twilio_stream.py:341-367`), never through the LLM conversation
- The `process_card_payment` and `process_ach_payment` MCP tools receive DTMF-collected data directly
- JWT generation (`payment_api._generate_jwt()`) stays server-side within the MCP server
- The `access_token` (from `acquire_access_token`) is managed server-side — the LLM never sees raw tokens

---

### Server 2: Knowledge Base (`cno-knowledge-mcp`)

**Wraps:** `services/rag.py`, PGVector via `langchain_community.vectorstores.PGVector`

#### Tools (2)

| Tool | Source Function | Input | Output |
|------|----------------|-------|--------|
| `search_knowledge` | `rag.search_knowledge()` | `query: string, k?: int (default 3)` | `context: string` (concatenated chunks) |
| `ingest_documents` | `rag.ingest_documents()` | `documents: Document[]` | `void` |

#### Prompts (2)

| Prompt Name | Template | Parameters |
|-------------|----------|------------|
| `rag_grounding` | `"Use ONLY the following information to answer. Do not add anything not in the context.\n\nContext:\n{context}"` | `context: string` |
| `auth_override` | `"CRITICAL: The caller has ALREADY been authenticated. Do NOT ask for phone number, policy number, date of birth, name, or any other verification. Respond only to the caller's question."` | (none) |

These prompts are currently hardcoded in `core/graph/nodes/faq.py:47-56`. Extracting them as MCP prompts enables:
- **Version control** — prompt changes don't require code deploys
- **A/B testing** — serve different grounding templates and measure faithfulness scores
- **Reuse** — a future chat agent uses the same grounding template

#### Value: Vector DB Swappability

Currently `services/rag.py` is tightly coupled to PGVector:

```python
# services/rag.py:1-3 — direct PGVector import
from langchain_community.vectorstores import PGVector
from langchain_openai import OpenAIEmbeddings
```

Behind an MCP boundary, the `search_knowledge` tool could swap to Pinecone, Weaviate, or Qdrant without any change to `faq_node` or any other consumer. The MCP server owns the embedding model choice (`text-embedding-3-small`), connection string, and collection name (`insuranceCompany_knowledge`).

---

### Server 3: Telephony (`cno-telephony-mcp`)

**Wraps:** Twilio REST API calls from `webhooks/twilio_stream.py`

#### Tools (4)

| Tool | Source Code | Input | Output |
|------|-------------|-------|--------|
| `transfer_call` | `twilio_stream.py:518-533` | `call_sid: string, phone_number: string` | `{success, error?}` |
| `send_dtmf_prompt` | (new — wraps TwiML `<Play digits>`) | `call_sid: string, prompt_text: string` | `{success}` |
| `clear_audio_buffer` | `twilio_stream.py:502-513` | `stream_sid: string` | `{success}` |
| `get_call_status` | (new — wraps Twilio `calls(sid).fetch()`) | `call_sid: string` | `{status, duration, ...}` |

#### Value: Platform Agnosticism

The current transfer implementation is Twilio-specific:

```python
# webhooks/twilio_stream.py:523-528
from twilio.rest import Client
client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
client.calls(self.call_sid).update(
    twiml=f'<Response><Dial>{phone_number}</Dial></Response>'
)
```

Behind an MCP server, `transfer_call` takes a `call_sid` and `phone_number`. The server implementation handles whether that means Twilio TwiML, Amazon Connect contact flow, or Genesys API. Graph nodes never import `twilio.rest.Client`.

#### Testability

Integration tests can run a mock telephony MCP server that logs calls instead of making real Twilio API requests. No need for ngrok tunnels or Twilio test credentials during CI.

---

### Server 4: Analytics (`cno-analytics-mcp`)

**Wraps:** `services/mlflow_tracker.py`, `services/call_db.py`, `services/conversation_store.py`

#### Tools (4)

| Tool | Source Function | Input | Output |
|------|----------------|-------|--------|
| `log_call_metrics` | `mlflow_tracker.log_call()` | `call: object` | `{success}` |
| `get_recent_calls` | `conversation_store.get_calls()` | `limit?: int` | `call_summaries[]` |
| `get_call_detail` | `conversation_store.get_call()` | `call_sid: string` | `call_record` |
| `search_calls` | `call_db.load_recent_calls()` | `limit?: int, filters?: object` | `call_records[]` |

#### Resources (3)

| Resource URI | Description |
|-------------|-------------|
| `cno://analytics/metrics-summary` | Aggregated metrics (avg duration, auth rate, escalation rate) |
| `cno://analytics/transcript/{call_sid}` | Full transcript for a specific call (from `mlflow_tracker._build_transcript()`) |
| `cno://analytics/experiment-runs` | MLflow experiment run listing (experiment: `CNO_IVR`, tracking URI: `sqlite:///mlflow.db`) |

#### Value

- Dashboard (`webhooks/dashboard.py`) becomes a pure MCP client — reads metrics via resources, not direct Python imports
- MLflow backend is swappable (local SQLite → remote MLflow server → Weights & Biases) without dashboard changes
- Call history queries can be optimized server-side (PostgreSQL JSONB indexes) without exposing query details to consumers

---

### Server 5: Session (`cno-session-mcp`) — DEFERRED

**Wraps:** `services/session.py` (Redis via `redis.asyncio`)

#### Recommendation: Defer

Session state management should **not** be wrapped in MCP for the following reasons:

1. **Latency sensitivity** — Session reads/writes happen on every turn of every call. Redis operations complete in sub-1ms. MCP overhead (2-5ms stdio, 10-30ms SSE) would add 200-3000% relative overhead to every session operation.

2. **Call frequency** — A single IVR turn triggers `get_state()` → graph invoke → `save_state()`. With 2 session operations per turn and ~8 turns per call, that's 16 MCP round-trips per call just for session. At 100 concurrent calls, that's 1,600 unnecessary MCP operations.

3. **Not an external boundary** — Session is an internal implementation detail. It stores ephemeral per-call state (auth step, PII collected, messages) that is only meaningful within a single call's lifetime (TTL: 3600s). No other agent or service needs to access another call's session.

4. **Existing fallback pattern** — `services/session.py` already has a clean fallback pattern (Redis → FakeRedis) that handles unavailability gracefully. MCP wouldn't improve this.

**When to reconsider:** If session state needs to be shared across multiple independently deployed agents (e.g., a handoff from IVR agent to chat agent mid-conversation), then a Session MCP server with SSE transport would make sense as a shared state coordination layer.

---

## 3. Integration Pattern with LangGraph

### MCP Client Initialization in `main.py` Lifespan

The MCP client pool initializes during FastAPI's lifespan, alongside the existing graph compilation and PostgreSQL setup:

```python
# main.py lifespan — proposed MCP additions
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing graph compilation ...

    # Phase 1: Initialize MCP clients (stdio transport)
    if settings.enable_mcp:
        from core.mcp_client import MCPClientPool
        mcp_pool = MCPClientPool()
        await mcp_pool.start_servers({
            "insurance": "mcp_servers/insurance_backend/server.py",
            "knowledge": "mcp_servers/knowledge_base/server.py",
            # Phase 3: "telephony", "analytics"
        })
        app.state.mcp = mcp_pool

    yield

    # Shutdown: close MCP connections
    if hasattr(app.state, "mcp"):
        await app.state.mcp.shutdown()
```

### Feature-Flagged Adapter for Gradual Migration

A thin adapter layer allows feature-flagged toggling between direct calls and MCP:

```python
# core/mcp_adapter.py
class InsuranceAdapter:
    """Feature-flagged adapter: MCP or direct calls."""

    def __init__(self, mcp_client=None):
        self._mcp = mcp_client

    async def party_search(self, **kwargs) -> dict:
        if self._mcp and settings.enable_mcp:
            return await self._mcp.call_tool("party_search", kwargs)
        # Fallback: direct call (existing code path)
        from core.tools.party_search import party_search
        return await party_search(**kwargs)

    async def holding_inquiry(self, policy_number: str, access_token: str) -> dict:
        if self._mcp and settings.enable_mcp:
            return await self._mcp.call_tool("holding_inquiry", {
                "policy_number": policy_number,
                "access_token": access_token,
            })
        from core.tools.holding_inquiry import holding_inquiry
        return await holding_inquiry(policy_number, access_token)
```

### Before/After: Auth Node

**Before (current — `core/graph/nodes/auth.py:3-4`):**
```python
from core.tools.party_search import party_search, check_auth_success
from core.tools.auth_token import acquire_access_token

# Inside _collecting_phone():
result = await party_search(phone=digits)

# Inside auth_node() token acquisition:
token = await acquire_access_token(
    party_key=customer.get("partyKey", ""),
    company_code=customer.get("companyCode", ""),
)
```

**After (MCP):**
```python
# auth_node receives adapter via state or dependency injection
adapter = state.get("_mcp_adapter") or InsuranceAdapter()

# Inside _collecting_phone():
result = await adapter.party_search(phone=digits)

# Inside auth_node() token acquisition:
token = await adapter.acquire_access_token(
    party_key=customer.get("partyKey", ""),
    company_code=customer.get("companyCode", ""),
)
```

### Before/After: FAQ Node

**Before (current — `core/graph/nodes/faq.py:6,39`):**
```python
from services.rag import search_knowledge

context = await search_knowledge(last_human, k=3) if settings.enable_rag else ""
```

**After (MCP):**
```python
# Knowledge adapter handles MCP vs direct
adapter = state.get("_knowledge_adapter") or KnowledgeAdapter()

context = await adapter.search_knowledge(last_human, k=3) if settings.enable_rag else ""
```

### Transport Choice

| Phase | Transport | Rationale |
|-------|-----------|-----------|
| Phase 1-2 | **stdio** | Single-machine deployment. Sub-5ms overhead. MCP servers run as child processes of the FastAPI app. Simple ops — no extra ports or services to manage. |
| Phase 3+ | **SSE** | Multi-container deployment (Docker Compose / ECS). Each MCP server runs as its own container. Enables independent scaling and deployment. Required for multi-agent architecture. |

---

## 4. Security Architecture

### PII Handling Through MCP

**stdio transport:** PII (phone numbers, DOB, names) flows through Unix pipes between parent and child processes. No network exposure. Process-level isolation means a compromised child process cannot access other servers' memory.

**SSE transport:** PII flows over HTTP. Mitigations:
- TLS required for all SSE connections (enforced at load balancer / reverse proxy)
- MCP servers bind to `127.0.0.1` or internal Docker network — never exposed to public internet
- Request-level API key authentication on SSE endpoints

### PCI Compliance Preservation

The current PCI architecture is preserved exactly:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PCI Data Flow                                │
│                                                                     │
│  Caller ──DTMF──► Twilio WebSocket ──digits──► DTMFCollector        │
│                                                      │              │
│                                                      ▼              │
│                                          ┌──────────────────────┐   │
│                   LLM (Groq)             │  cno-insurance-mcp   │   │
│                   Never sees             │  process_card_payment │   │
│                   card data              │  process_ach_payment  │   │
│                                          │  _generate_jwt()      │   │
│                                          └──────────┬───────────┘   │
│                                                     │               │
│                                                     ▼               │
│                                          CNO Payment Backend        │
└─────────────────────────────────────────────────────────────────────┘
```

Key guarantees:
- DTMF digits (`DTMFCollector` in `twilio_stream.py:49-118`) are collected outside the LLM conversation loop
- Card/bank data is passed directly to the MCP `process_card_payment` / `process_ach_payment` tools
- JWT tokens (`_generate_jwt()` using `settings.cno_jwt_secret`) are generated inside the MCP server — never exposed to clients
- The LLM only sees `{success: true, confirmation: "CONF-12345"}` — never raw card numbers

### Auth Token Flow

```
┌───────────────────────────────────────────────────────────────┐
│                    Token Management                           │
│                                                               │
│  auth_node ──party_key, company_code──► cno-insurance-mcp     │
│                                              │                │
│                                              ▼                │
│                                     acquire_access_token()    │
│                                     POST /auth/token          │
│                                     Bearer: settings.cno_api_key
│                                              │                │
│                                              ▼                │
│                                     Returns: access_token     │
│                                              │                │
│  auth_node ◄──────── token string ───────────┘                │
│       │                                                       │
│       ▼                                                       │
│  Stored in session state (Redis)                              │
│  Passed to subsequent MCP tool calls as parameter             │
│  Never sent to LLM                                            │
└───────────────────────────────────────────────────────────────┘
```

The `access_token` is stored in session state (`services/session.py`) and passed as a parameter to downstream MCP tools (`holding_inquiry`, `payment_history`, `loan_inquiry`, `process_card_payment`, `process_ach_payment`). The LLM orchestrates tool calls but never receives the token value.

### API Keys Stay Server-Side

| Key | Current Location | MCP Location |
|-----|-----------------|--------------|
| `settings.cno_api_key` | `party_search.py`, `auth_token.py` | `cno-insurance-mcp` server config |
| `settings.cno_jwt_secret` | `payment_api.py` | `cno-insurance-mcp` server config |
| `settings.groq_api_key` | `faq.py`, graph nodes | Remains in LangGraph app (not MCP) |
| `settings.openai_api_key` | `services/rag.py` (embeddings) | `cno-knowledge-mcp` server config |
| `settings.twilio_account_sid/auth_token` | `twilio_stream.py` | `cno-telephony-mcp` server config |
| `settings.deepgram_api_key` | `services/stt.py` | Remains in app (real-time audio stream) |
| `settings.elevenlabs_api_key` | `services/tts.py` | Remains in app (real-time audio stream) |

MCP servers each have their own `.env` / config — the main app no longer needs `cno_api_key`, `cno_jwt_secret`, `twilio_account_sid`, or `openai_api_key` (for embeddings).

---

## 5. Performance Analysis

### Latency Overhead by Transport

| Transport | Added Latency | Mechanism |
|-----------|--------------|-----------|
| **stdio** | 2-5ms | JSON-RPC serialization + pipe I/O |
| **SSE** | 10-30ms | HTTP POST + SSE response stream + TLS handshake (amortized with keep-alive) |

### Impact on Each Tool Category

| Tool Category | Current Latency | MCP Overhead (stdio) | Total | % Increase |
|--------------|----------------|---------------------|-------|------------|
| `party_search` | 200-500ms (HTTP to CNO backend) | +3ms | 203-503ms | +0.6-1.5% |
| `acquire_access_token` | 150-300ms | +3ms | 153-303ms | +1-2% |
| `holding_inquiry` | 200-400ms | +3ms | 203-403ms | +0.7-1.5% |
| `payment_history` | 200-400ms | +3ms | 203-403ms | +0.7-1.5% |
| `process_card_payment` | 300-500ms (includes JWT gen) | +3ms | 303-503ms | +0.6-1% |
| `search_knowledge` | 50-150ms (PGVector similarity) | +3ms | 53-153ms | +2-6% |
| `transfer_call` | 100-300ms (Twilio REST) | +3ms | 103-303ms | +1-3% |
| `log_call_metrics` | 50-200ms (MLflow write) | +3ms | 53-203ms | +1.5-6% |

**Verdict:** For all tools, MCP stdio overhead is <6% of total latency. This is well within acceptable bounds for an IVR system where the dominant latency contributors are:
- LLM inference via Groq: 300-800ms
- TTS streaming via ElevenLabs: 200-500ms first byte
- STT finalization via Deepgram: 300-1000ms (endpointing)

### Mitigation Strategies

**1. Connection pooling** — The MCP client pool maintains persistent stdio connections to each server. No per-request process spawn overhead.

**2. Composite tools** — For the auth flow, which currently calls `party_search` → `validate_pii_match` → `check_auth_success` sequentially, a composite `authenticate_caller` tool could reduce 3 MCP round-trips to 1:

```python
# Single MCP call instead of 3 sequential calls
result = await mcp.call_tool("authenticate_caller", {
    "phone": digits,
    "pii_collected": pii_collected,
    "fuzzy_threshold": 0.80,
})
# Returns: {party, matched_fields, auth_success, access_token?}
```

**3. Async concurrency** — Independent MCP calls can be made concurrently. For example, after authentication, `holding_inquiry` and `payment_history` for the same policy could be fetched in parallel:

```python
# Concurrent MCP calls
inquiry, payments = await asyncio.gather(
    mcp.call_tool("holding_inquiry", {"policy_number": pn, "access_token": token}),
    mcp.call_tool("payment_history", {"policy_number": pn, "access_token": token}),
)
```

---

## 6. Multi-Agent Future Vision

### Supervisor Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Supervisor Agent                                  │
│              (routes requests to specialized agents)                 │
│                                                                     │
│    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    │
│    │ IVR Agent│    │Chat Agent│    │Claims    │    │Email     │    │
│    │(current) │    │(web UI)  │    │Agent     │    │Agent     │    │
│    └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘    │
│         │               │               │               │          │
│    ┌────┴───────────────┴───────────────┴───────────────┘          │
│    │                                                               │
│    ▼              Shared MCP Servers                                │
│    ┌──────────────────────────────────────────────────────────┐    │
│    │  cno-insurance-mcp   │  cno-knowledge-mcp               │    │
│    │  (9 tools, 2 res.)   │  (2 tools, 2 prompts)            │    │
│    ├──────────────────────┼──────────────────────────────────│    │
│    │  cno-telephony-mcp   │  cno-analytics-mcp               │    │
│    │  (4 tools)           │  (4 tools, 3 res.)               │    │
│    └──────────────────────┴──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Shared MCP Servers Across Agents

The key value proposition: **every agent shares the same MCP servers**.

| MCP Server | IVR Agent | Chat Agent | Claims Agent | Email Agent |
|-----------|-----------|------------|--------------|-------------|
| `cno-insurance-mcp` | party_search, holding_inquiry, payments | party_search, holding_inquiry | holding_inquiry, claims tools | holding_inquiry |
| `cno-knowledge-mcp` | search_knowledge (FAQ) | search_knowledge | search_knowledge | search_knowledge |
| `cno-telephony-mcp` | transfer_call, DTMF | - | - | - |
| `cno-analytics-mcp` | log_call_metrics | log_chat_metrics | log_claim_metrics | log_email_metrics |

Adding a new agent (e.g., Claims Agent) requires:
1. Define the agent's LangGraph graph
2. Connect it to existing MCP servers
3. Add any claims-specific MCP tools to `cno-insurance-mcp`

No duplication of API integration code, auth token management, or RAG infrastructure.

### Tool Discovery via `tools/list`

MCP's `tools/list` method allows agents to dynamically discover available tools:

```json
// Request
{"jsonrpc": "2.0", "method": "tools/list", "id": 1}

// Response
{
  "tools": [
    {
      "name": "party_search",
      "description": "Search for a party record by PII fields (phone, policy, DOB, name, zip)",
      "inputSchema": {
        "type": "object",
        "properties": {
          "phone": {"type": "string"},
          "policy_number": {"type": "string"},
          "date_of_birth": {"type": "string"},
          "first_name": {"type": "string"},
          "last_name": {"type": "string"},
          "zipcode": {"type": "string"}
        }
      }
    },
    // ... 8 more tools
  ]
}
```

This enables a supervisor agent to inspect which tools each sub-agent has access to and route requests accordingly — without hardcoded knowledge of tool availability.

---

## 7. Phased Rollout Plan

### Phase 1: Insurance Backend MCP (Weeks 1-3)

**Scope:** Wrap `core/tools/*.py` into `cno-insurance-mcp` server

| Week | Deliverable |
|------|-------------|
| 1 | MCP server scaffolding (`mcp_servers/insurance_backend/`), stdio transport, 9 tool definitions |
| 2 | `core/mcp_adapter.py` with feature flag (`ENABLE_MCP=true/false` in `config/settings.py`), wire into `auth_node` |
| 3 | Integration tests (mock MCP server), A/B latency comparison, wire remaining nodes (policy, payment, loan) |

**Feature flag addition to `config/settings.py`:**
```python
# MCP integration (gradual rollout)
enable_mcp: bool = False  # True → use MCP servers, False → direct Python calls
```

**Validation:**
- `auth_node` passes all existing tests via both direct and MCP paths
- Latency delta < 10ms per tool call (stdio)
- Payment DTMF flow works end-to-end through MCP

### Phase 2: Knowledge Base MCP (Weeks 4-5)

**Scope:** Wrap `services/rag.py` into `cno-knowledge-mcp` server

| Week | Deliverable |
|------|-------------|
| 4 | MCP server with `search_knowledge` + `ingest_documents` tools, 2 prompt templates |
| 5 | Wire `faq_node` to use knowledge adapter, RAG eval suite (`tests/eval_rag.py`) validates no regression |

**Validation:**
- RAG faithfulness and relevancy scores match or exceed baseline
- `faq_node` works with MCP disabled (fallback to direct `services/rag.py`)

### Phase 3: Telephony + Analytics MCP (Weeks 6-8)

**Scope:** Wrap Twilio REST API calls and MLflow/call_db into MCP servers

| Week | Deliverable |
|------|-------------|
| 6 | `cno-telephony-mcp` server (4 tools), mock server for CI |
| 7 | `cno-analytics-mcp` server (4 tools, 3 resources), dashboard reads via MCP resources |
| 8 | End-to-end integration testing, stdio → SSE transport migration for containerized deployment |

**Validation:**
- Call transfers work through MCP telephony server
- Dashboard renders correctly reading from MCP analytics resources
- Docker Compose deployment with SSE transport works end-to-end

### Phase 4: Multi-Agent Orchestration (Weeks 9-12)

**Scope:** Supervisor agent + shared MCP servers

| Week | Deliverable |
|------|-------------|
| 9 | Supervisor agent graph (routes IVR vs chat vs claims) |
| 10 | Chat agent connected to shared `cno-insurance-mcp` + `cno-knowledge-mcp` |
| 11 | Claims agent with extended insurance tools |
| 12 | Production deployment, monitoring, load testing |

**Validation:**
- Multiple agents share MCP servers without interference
- Tool discovery via `tools/list` works across all servers
- Supervisor correctly routes requests to appropriate sub-agents

---

## 8. File Structure for MCP Servers

```
cno_ivr/
├── mcp_servers/
│   ├── insurance_backend/
│   │   ├── __init__.py
│   │   ├── server.py          # MCP server entry point (stdio transport)
│   │   ├── tools.py           # 9 tool implementations (wraps core/tools/*.py logic)
│   │   └── config.py          # Server-specific config (cno_api_base_url, cno_api_key, jwt_secret)
│   │
│   ├── knowledge_base/
│   │   ├── __init__.py
│   │   ├── server.py          # MCP server entry point
│   │   ├── tools.py           # search_knowledge, ingest_documents
│   │   └── prompts.py         # rag_grounding, auth_override prompt templates
│   │
│   ├── telephony/
│   │   ├── __init__.py
│   │   ├── server.py          # MCP server entry point
│   │   └── tools.py           # transfer_call, send_dtmf_prompt, clear_audio_buffer, get_call_status
│   │
│   └── analytics/
│       ├── __init__.py
│       ├── server.py          # MCP server entry point
│       ├── tools.py           # log_call_metrics, get_recent_calls, get_call_detail, search_calls
│       └── resources.py       # metrics_summary, transcript, experiment_runs resource handlers
│
├── core/
│   ├── mcp_adapter.py         # Feature-flagged adapter (MCP ↔ direct calls)
│   ├── mcp_client.py          # MCP client pool (manages stdio/SSE connections to servers)
│   ├── tools/                 # Existing tools (kept as fallback when enable_mcp=False)
│   │   ├── party_search.py
│   │   ├── auth_token.py
│   │   ├── holding_inquiry.py
│   │   └── payment_api.py
│   └── graph/
│       └── nodes/             # Graph nodes updated to use adapters
│           ├── auth.py
│           ├── faq.py
│           ├── policy.py
│           ├── payment.py
│           └── ...
│
└── config/
    └── settings.py            # New: enable_mcp flag, MCP server paths
```

### Key Design Decisions

1. **Existing tools preserved as fallback** — `core/tools/*.py` remain unchanged. The adapter routes to them when `enable_mcp=False`. This means zero risk during rollout — flip the flag to revert.

2. **MCP servers are self-contained** — Each server directory has its own config, tools, and server entry point. A server can be tested independently without the full IVR application.

3. **Adapter pattern, not direct replacement** — Graph nodes import from `core/mcp_adapter.py`, not from MCP servers directly. The adapter handles the MCP client lifecycle and fallback logic.

4. **Server-specific config** — Each MCP server reads only the secrets it needs. `cno-insurance-mcp` gets `cno_api_key` and `cno_jwt_secret`. `cno-knowledge-mcp` gets `openai_api_key` and `database_url`. This is better than the current `config/settings.py` which holds all 20+ secrets in one place.
