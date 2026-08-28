# CNO IVR — RAG Architecture & Production Pipeline

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Target Architecture Overview](#2-target-architecture-overview)
3. [Async PGVector Fix](#3-async-pgvector-fix)
4. [Redis Caching Layer](#4-redis-caching-layer)
5. [Metadata Filtering](#5-metadata-filtering)
6. [Cross-Encoder Re-Ranking](#6-cross-encoder-re-ranking)
7. [Document Ingestion Pipeline](#7-document-ingestion-pipeline)
8. [Chunking Strategy](#8-chunking-strategy)
9. [Embedding Model Selection](#9-embedding-model-selection)
10. [Vector Store Evaluation](#10-vector-store-evaluation)
11. [Test Document Corpus (20 samples)](#11-test-document-corpus-20-samples)
12. [Evaluation Framework](#12-evaluation-framework)
13. [Production Checklist](#13-production-checklist)

---

## 1. Current State Assessment

### What exists today

```
services/rag.py          -- 46 lines, basic PGVector wrapper
seed_knowledge.py        -- 35 hardcoded text chunks, manual metadata
core/graph/nodes/faq.py  -- FAQ node that calls search_knowledge()
tests/eval_rag.py        -- LLM-as-judge evaluation (faithfulness, relevancy)
```

### Current flow

```
Caller question
  -> faq_node
    -> search_knowledge(query, k=3)
      -> PGVector.similarity_search()  [BLOCKING on async event loop]
    -> Groq LLM generates answer grounded in retrieved context
  -> TTS response to caller
```

### Problems identified

| # | Issue | Impact | Severity |
|---|-------|--------|----------|
| 1 | `similarity_search()` is synchronous SQLAlchemy call inside `async def` | Blocks the entire event loop -- all concurrent WebSocket streams freeze during DB query | Critical |
| 2 | No caching | Every FAQ question hits OpenAI Embeddings API + pgvector -- adds 200-400ms latency and cost per query | High |
| 3 | No re-ranking | Raw cosine similarity returns approximate results; irrelevant chunks can outrank relevant ones | Medium |
| 4 | No metadata filtering | A beneficiary query searches ALL 35 chunks including payment/loan/dividend ones | Medium |
| 5 | Static knowledge base | All 35 chunks are hardcoded in Python -- no file/PDF/URL ingestion pipeline | High |
| 6 | No chunking logic | Manual chunks with no overlap, no size normalization | Medium |
| 7 | No HNSW index | Sequential scan on pgvector -- O(n) per query instead of O(log n) | Medium (at scale) |

---

## 2. Target Architecture Overview

```
                    +-------------------+
                    |   Caller Query    |
                    +--------+----------+
                             |
                    +--------v----------+
                    |   Redis Cache     |  <-- TTL 1hr, SHA256(query) key
                    |   (hit? return)   |
                    +--------+----------+
                             | miss
                    +--------v----------+
                    | Category Detector |  <-- keyword matching -> metadata filter
                    +--------+----------+
                             |
                    +--------v-----------+
                    | PGVector Search     |  <-- async (run_in_executor)
                    | k*3 candidates      |      with metadata filter
                    | HNSW index          |      cosine distance
                    +--------+-----------+
                             |
                    +--------v----------+
                    | LLM Re-Ranker     |  <-- Groq Llama-3.1-8B scores 0-10
                    | top-k selection   |      parallel scoring
                    +--------+----------+
                             |
                    +--------v----------+
                    |  Cache Result     |
                    |  Return Context   |
                    +--------+----------+
                             |
                    +--------v----------+
                    |   FAQ Node LLM    |  <-- Groq Llama-3.3-70B
                    |   Grounded Answer  |     constrained to context only
                    +-------------------+
```

---

## 3. Async PGVector Fix

### Problem

```python
# CURRENT CODE -- services/rag.py line 34
async def search_knowledge(query: str, k: int = 3) -> str:
    store = _get_store()
    docs = store.similarity_search(query, k=k)  # BLOCKING CALL
```

`PGVector.similarity_search()` uses synchronous SQLAlchemy under the hood. When called inside an `async def`, it blocks the Python event loop. In our IVR system, this means:

- All active Twilio WebSocket streams freeze (callers hear silence)
- Deepgram STT transcription buffers overflow
- ElevenLabs TTS audio delivery stalls
- Other HTTP handlers (dashboard, health checks) queue up

A pgvector query takes 50-200ms. During that time, every concurrent call is frozen.

### Solution: `asyncio.run_in_executor()`

```python
# TARGET CODE
async def search_knowledge(query: str, k: int = 3) -> str:
    store = _get_store()
    loop = asyncio.get_event_loop()
    docs = await loop.run_in_executor(
        None,  # default ThreadPoolExecutor
        lambda: store.similarity_search(query, k=k)
    )
```

### Why `run_in_executor` instead of `asimilarity_search`?

| Option | Pros | Cons |
|--------|------|------|
| `run_in_executor` | Works with current PGVector class, no dependency changes, battle-tested | Thread pool overhead (~1ms), not true async IO |
| `asimilarity_search()` | True async IO, no thread overhead | `langchain_community.PGVector` does not implement async methods as of v0.3.14; requires migrating to `langchain_postgres.PGVector` (different package, breaking API changes) |
| Migrate to `langchain_postgres` | Native async, actively maintained | Breaking change: different constructor, different filter syntax, requires re-seeding the knowledge base |

**Decision**: Use `run_in_executor` for now. It solves the blocking problem with zero migration risk. Migrate to `langchain_postgres` native async in a future sprint.

### Thread pool sizing

Default `ThreadPoolExecutor` creates `min(32, os.cpu_count() + 4)` threads. For our IVR:

- Peak concurrent FAQ queries: ~10-20 (one per active call)
- Each thread holds a SQLAlchemy connection briefly (~100ms)
- Default pool size is sufficient

For production, use a dedicated executor with a bounded pool:

```python
from concurrent.futures import ThreadPoolExecutor
_rag_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="rag")
```

---

## 4. Redis Caching Layer

### Why cache RAG results?

| Metric | Without Cache | With Cache (hit) |
|--------|--------------|-------------------|
| OpenAI Embedding API call | 1 per query | 0 |
| PGVector DB query | 1 per query | 0 |
| Re-ranking LLM calls | k*3 per query | 0 |
| Total latency | 400-800ms | 2-5ms |
| Cost per query | ~$0.00005 | $0 |

IVR callers frequently ask identical questions: "What is my grace period?", "How do I make a payment?", "Can I take a loan?"

### Cache design

```
Key:    cno:rag:<sha256(normalized_query + k + category)[:16]>
Value:  concatenated context string (the final reranked result)
TTL:    3600 seconds (1 hour)
```

**Why SHA256 for keys?**
- Queries can be long and contain special characters
- SHA256 is deterministic and collision-resistant
- 16-char prefix gives 64 bits of entropy -- collision probability is negligible

**Why 1-hour TTL?**
- Insurance knowledge base changes infrequently (quarterly at most)
- 1 hour balances freshness with hit rate
- On knowledge base update, call `clear_cache()` to force refresh

**Why cache the final concatenated string (not individual docs)?**
- Simpler -- one cache entry per query
- Re-ranking is query-dependent, so caching pre-rerank docs doesn't help
- Avoids serializing/deserializing Document objects

### Cache invalidation

```python
async def clear_cache() -> int:
    keys = await redis.keys("cno:rag:*")
    if keys:
        await redis.delete(*keys)
    return len(keys)
```

### Graceful degradation

If Redis is unavailable:
- `_get_redis()` returns `None`
- `_cache_get()` returns `None` (cache miss)
- `_cache_set()` is a no-op
- Pipeline continues without caching -- no errors, just slower

This matches the existing pattern in `services/session.py` which falls back to `fakeredis`.

---

## 5. Metadata Filtering

### Problem

Without filtering, a query like "How do I change my beneficiary?" searches all chunks equally. Irrelevant chunks about ACH authorization or dividends consume top-k slots.

### Solution: Category-based pre-filtering

```
Step 1: Detect category from query keywords
  "How do I change my beneficiary?" -> category = "beneficiaries"

Step 2: Pass filter to pgvector
  store.similarity_search(query, k=9, filter={"category": "beneficiaries"})

Step 3: If filtered results < k, fall back to unfiltered search
```

### Category keyword map

| Category | Trigger Keywords |
|----------|-----------------|
| `policy_types` | whole life, term life, universal life, medicare supplement, type of policy |
| `policy_status` | active, lapsed, paid up, surrendered, policy status |
| `premiums` | premium, billing, autopay, paid to date, grace period, missed payment |
| `payments` | payment, pay my bill, credit card, debit card, ach, routing number |
| `cash_value` | cash value, savings, accumulation |
| `policy_loans` | loan, borrow, payoff, loan balance |
| `beneficiaries` | beneficiary, who gets the money, death benefit goes to |
| `documents` | document, statement, annual report, mail me, policy copy |
| `contact_changes` | address, phone number, update my, change my address |
| `owner_changes` | owner, ownership, transfer ownership |
| `privacy` | privacy, glba, opt out, personal information, data sharing |
| `claims` | claim, death certificate, file a claim, report a death |
| `dividends` | dividend, participating |
| `company_info` | cno, bankers life, colonial penn, history |
| `general` | agent, representative, transfer, authenticate |
| `compliance_scripts` | authorization, ach script, privacy script |

### Why keyword matching instead of LLM-based category detection?

| Approach | Latency | Cost | Accuracy |
|----------|---------|------|----------|
| Keyword matching | <1ms | $0 | ~85% |
| LLM classification | 200-400ms | ~$0.00003 | ~95% |
| Embedding similarity | 100-200ms | ~$0.00002 | ~90% |

**Decision**: Keyword matching. Runs in <1ms with zero cost. The fallback to unfiltered search handles the 15% miss rate.

---

## 6. Cross-Encoder Re-Ranking

### Why re-rank?

```
Bi-encoder (PGVector similarity search):
  - Encodes query and documents INDEPENDENTLY
  - Fast: O(1) per document (pre-computed embeddings)
  - Approximate: misses nuanced relevance

Cross-encoder (re-ranking):
  - Encodes query AND document TOGETHER
  - Slow: O(n) -- must score each pair
  - Precise: captures semantic relationships

Combined strategy:
  1. Bi-encoder retrieves 9 candidates (fast, approximate)
  2. Cross-encoder re-ranks to top 3 (slow, precise)
  -> Best of both: speed + accuracy
```

### Example: Re-ranking impact

Query: "What happens if I stop paying my premiums?"

**Without re-ranking (raw cosine similarity):**
1. "Your premium is the amount you pay to keep your life insurance..." (0.82)
2. "Online and phone payments post to your account within 24 to 48 hours..." (0.79)
3. "Autopay automatically deducts your premium from your bank account..." (0.77)

**With re-ranking:**
1. "If you miss a premium payment your policy enters a 30-day grace period..." (9/10)
2. "A Lapsed policy means coverage has ended because premiums were not paid..." (8/10)
3. "Your premium is the amount you pay to keep your life insurance..." (7/10)

The re-ranker correctly identifies that chunks about consequences of non-payment are more relevant than chunks about what a premium is.

### Implementation options compared

| Approach | Model | Latency | Cost | Deployment |
|----------|-------|---------|------|------------|
| **LLM re-ranking (chosen)** | Groq Llama-3.1-8B | ~200ms (parallel) | ~$0.0001/query | No extra infra |
| Sentence-transformers | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ~50ms (local) | $0 (self-hosted) | Requires GPU, 400MB model |
| Cohere Rerank API | `rerank-english-v3.0` | ~150ms | $0.002/query | Third-party dependency |
| BGE Reranker | `BAAI/bge-reranker-v2-m3` | ~80ms (local) | $0 (self-hosted) | Requires GPU, 1.2GB model |

**Decision**: LLM re-ranking via Groq. We already have the Groq connection. The 8B model is fast enough. No additional infrastructure. For production at scale, migrate to self-hosted cross-encoder on SageMaker.

### Re-ranking prompt

```
System: You are a relevance scorer. Reply with only a number 0-10.
User: Rate how relevant this text is to answering the question.
Question: {query}
Text: {chunk_text[:300]}
Reply with ONLY a number from 0 to 10.
```

**Why parallel scoring?**
All 9 candidates are scored concurrently with `asyncio.gather()`, reducing total re-ranking time from 9 x 200ms = 1.8s to ~200ms.

---

## 7. Document Ingestion Pipeline

### Current state: Hardcoded chunks

All 35 knowledge chunks are Python tuples in `seed_knowledge.py`. Adding knowledge requires editing code.

### Target: Multi-source ingestion pipeline

```
Input Sources          Loaders              Processing           Storage
+------------+     +-------------+     +----------------+     +----------+
| PDF files  | --> | PyPDFLoader | --> |                | --> |          |
+------------+     +-------------+     |  Text Splitter |     | PGVector |
+------------+     +-------------+     |  (Recursive    |     |   with   |
| URLs/HTML  | --> | WebLoader   | --> |   Character)   |     | metadata |
+------------+     +-------------+     |                |     |          |
+------------+     +-------------+     |  Metadata      |     |          |
| Text/MD    | --> | TextLoader  | --> |  Enrichment    |     |          |
+------------+     +-------------+     |                |     |          |
+------------+     +-------------+     |  Deduplication |     |          |
| CSV files  | --> | CSVLoader   | --> |                |     |          |
+------------+     +-------------+     +----------------+     +----------+
```

### Loader selection per source type

| Source | Loader | Why This Loader |
|--------|--------|-----------------|
| PDF | `PyPDFLoader` | Pure Python, no external deps, handles multi-page, extracts text per page. Alt: `UnstructuredPDFLoader` for scanned PDFs with OCR (adds 500MB+ dep) |
| URL/HTML | `WebBaseLoader` | Uses `requests` + `BeautifulSoup` -- already in dependency tree. For JS-rendered pages, use `PlaywrightURLLoader` |
| Markdown | `TextLoader` | Simpler than `UnstructuredMarkdownLoader`, sufficient since we don't need structural parsing |
| Plain text | `TextLoader` | Direct text ingestion, no parsing needed |
| CSV | `CSVLoader` | Each row becomes a document. For tabular FAQ data from CMS exports |

### Data directory structure

```
data/
  knowledge/
    pdfs/
      policy_guide.pdf
      claims_handbook.pdf
      billing_faq.pdf
    urls/
      urls_to_ingest.txt       # one URL per line
    text/
      grace_period_policy.md
      payment_methods.txt
      beneficiary_rules.md
    csv/
      faq_export.csv           # columns: question, answer, category
```

### Ingestion CLI

```bash
python ingest_documents.py --source ./data/knowledge/ --type pdf
python ingest_documents.py --source ./data/knowledge/ --type all
python ingest_documents.py --source https://example.com/faq --type url
python ingest_documents.py --reset     # clear and re-ingest everything
python ingest_documents.py --dry-run   # preview chunks without writing
```

---

## 8. Chunking Strategy

### Why chunking matters

A 20-page PDF cannot be embedded as one vector. It must be split into chunks that:
1. Fit the embedding model's optimal window (512-1024 tokens for `text-embedding-3-small`)
2. Preserve semantic coherence (don't split mid-sentence)
3. Include overlap so context isn't lost at boundaries

### Parameters chosen

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Splitter** | `RecursiveCharacterTextSplitter` | Tries `\n\n` -> `\n` -> `. ` -> ` ` in order. Preserves paragraph boundaries first, then sentences |
| **Chunk size** | 500 characters (~100-125 tokens) | Existing manual chunks average 200-350 chars. 500 chars allows complete thoughts without exceeding embedding sweet spot |
| **Chunk overlap** | 50 characters (~10-12 tokens) | 10% overlap ensures context continuity. >20% wastes storage and creates near-duplicate embeddings |
| **Length function** | `len` (character count) | Token-based counting adds ~5ms per chunk. Character count is a good proxy for English text |

### Why `RecursiveCharacterTextSplitter` over alternatives?

| Splitter | Behavior | Best For | Why Not Chosen |
|----------|----------|----------|----------------|
| **RecursiveCharacterTextSplitter** | Hierarchical separators | General text, mixed formats | -- (chosen) |
| `CharacterTextSplitter` | Single separator only | Uniform text | Splits mid-paragraph |
| `TokenTextSplitter` | Exact token count | Token-critical apps | Ignores semantic boundaries |
| `MarkdownHeaderTextSplitter` | Splits on `#`, `##` | Structured markdown | Only works for Markdown |
| `SemanticChunker` | Groups by embedding similarity | Research documents | Extremely slow, high cost |

### Metadata enrichment during chunking

```python
{
    "source": "data/knowledge/pdfs/policy_guide.pdf",
    "page": 3,
    "chunk_id": 42,
    "category": "policy_types",
    "subcategory": "whole_life",
    "ingested_at": "2026-08-04T10:30:00Z",
    "doc_type": "pdf",
    "char_count": 487,
}
```

---

## 9. Embedding Model Selection

### Models evaluated

| Model | Dimensions | Max Tokens | Cost/1K tokens | MTEB Score | Latency |
|-------|-----------|------------|----------------|------------|---------|
| **`text-embedding-3-small` (chosen)** | 1536 | 8191 | $0.00002 | 62.3 | ~80ms |
| `text-embedding-3-large` | 3072 | 8191 | $0.00013 | 64.6 | ~120ms |
| `text-embedding-ada-002` | 1536 | 8191 | $0.00010 | 61.0 | ~80ms |
| Cohere `embed-english-v3.0` | 1024 | 512 | $0.00010 | 64.5 | ~100ms |
| Self-hosted `all-MiniLM-L6-v2` | 384 | 512 | $0 (GPU cost) | 56.3 | ~10ms |
| Self-hosted `bge-large-en-v1.5` | 1024 | 512 | $0 (GPU cost) | 64.2 | ~30ms |

### Why `text-embedding-3-small`?

1. **Cost**: 5x cheaper than `ada-002`, 6.5x cheaper than `3-large`
2. **Quality**: MTEB 62.3 is sufficient for our domain-specific short insurance chunks
3. **Latency**: ~80ms -- acceptable for IVR (total RAG target: <500ms)
4. **Dimension reduction**: Supports `dimensions` parameter to reduce storage at scale
5. **Already integrated**: No migration needed

### When to migrate to self-hosted

- Query volume exceeds 100K/day
- Latency requirement drops below 50ms
- Regulatory requirement forbids sending data to OpenAI

Target: `bge-large-en-v1.5` on SageMaker `ml.g5.xlarge` ($1.41/hr)

---

## 10. Vector Store Evaluation

### Databases compared

| Vector DB | Self-hosted | Managed | Filtering | Hybrid Search | Cost |
|-----------|-------------|---------|-----------|---------------|------|
| **PGVector (chosen)** | Yes (Docker) | AWS RDS | JSONB filters | BM25 via pg_search | Free |
| Pinecone | No | Yes only | Rich | Yes | Per-vector pricing |
| Weaviate | Yes | Cloud | GraphQL | BM25 + vector | Open source |
| Qdrant | Yes | Cloud | Rich | Sparse + dense | Open source |
| ChromaDB | Yes | No | Basic | No | Open source |
| Milvus | Yes | Zilliz | Yes | Yes | Open source |

### Why PGVector?

1. **Already in the stack**: Used for LangGraph checkpointing -- no new service
2. **Operational simplicity**: One database to backup, monitor, scale
3. **ACID transactions**: No partial writes during ingestion
4. **Metadata filtering**: JSONB column supports rich filters
5. **HNSW indexing**: pgvector 0.5+ supports O(log n) ANN search
6. **Cost**: Free -- no per-query pricing

### HNSW index configuration

```sql
CREATE INDEX ON langchain_pg_embedding
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

### Scaling path

| Scale | Vectors | Strategy |
|-------|---------|----------|
| Current | ~50 | Sequential scan, single PG instance |
| Phase 2 | 500-5K | HNSW index, connection pooling (PgBouncer) |
| Phase 3 | 5K-50K | Read replicas, partitioned tables |
| Phase 4 | 50K+ | Dedicated vector DB (Qdrant/Pinecone) |

---

## 11. Test Document Corpus (20 samples)

### PDF Documents (5)

| # | Filename | Content | Pages | Category |
|---|----------|---------|-------|----------|
| 1 | `whole_life_product_guide.pdf` | Features, cash value growth, premium schedules, rider options | 8 | policy_types |
| 2 | `claims_filing_handbook.pdf` | Step-by-step claims process, required documents, timelines | 12 | claims |
| 3 | `billing_and_payment_faq.pdf` | Payment methods, autopay, grace period rules | 6 | premiums |
| 4 | `privacy_notice_2024.pdf` | GLBA notice, data practices, opt-out procedures | 4 | privacy |
| 5 | `policy_loan_terms.pdf` | Loan eligibility, interest rates, tax implications | 5 | policy_loans |

### URL/Web Pages (5)

| # | Source Page | Content | Category |
|---|-----------|---------|----------|
| 6 | Beneficiary changes FAQ | Add/change/remove beneficiaries, forms, processing time | beneficiaries |
| 7 | Policy reinstatement FAQ | Eligibility, documentation, timeframes | policy_status |
| 8 | Medicare supplement plans | Plan comparisons A through N, enrollment periods | policy_types |
| 9 | Company history page | Founding, milestones, brand portfolio, ratings | company_info |
| 10 | Document delivery FAQ | How to request statements, delivery methods | documents |

### Markdown/Text Documents (5)

| # | Filename | Content | Category |
|---|----------|---------|----------|
| 11 | `grace_period_policy.md` | Grace period rules by policy type, automatic premium loan | premiums |
| 12 | `cash_value_explained.md` | Cash value growth, tax-deferred accumulation, withdrawal vs loan | cash_value |
| 13 | `address_change_procedure.txt` | Address change steps, verification, security measures | contact_changes |
| 14 | `owner_vs_insured.md` | Difference between owner and insured, rights | owner_changes |
| 15 | `dividend_options_guide.md` | Dividend payment methods, paid-up additions | dividends |

### CSV/Structured Data (3)

| # | Filename | Content | Category |
|---|----------|---------|----------|
| 16 | `agent_transfer_scenarios.csv` | scenario, trigger_phrase, transfer_reason columns | general |
| 17 | `compliance_scripts.csv` | script_name, script_text, when_to_use columns | compliance_scripts |
| 18 | `payment_method_rules.csv` | method, accepted, restrictions, processing_time columns | payments |

### Edge Case Documents (2)

| # | Filename | Purpose |
|---|----------|---------|
| 19 | `empty_document.txt` | Tests pipeline handles empty input gracefully |
| 20 | `mixed_format_faq.md` | FAQ with tables, bullets, headers, special characters -- tests chunking robustness |

### Expected chunk counts

| Source Type | Documents | Estimated Total Chunks |
|------------|-----------|----------------------|
| PDFs (5) | ~47,500 chars | ~112 chunks |
| URLs (5) | ~15,000 chars | ~32 chunks |
| Text/MD (5) | ~10,000 chars | ~22 chunks |
| CSV (3) | ~150 rows | ~150 chunks |
| **Total** | **20 documents** | **~316 chunks** |

---

## 12. Evaluation Framework

### Metrics

| Metric | What It Measures | Target | Method |
|--------|-----------------|--------|--------|
| Faithfulness | Answer grounded in context? | >= 0.85 | LLM-as-judge |
| Contextual Relevancy | Retrieved context relevant? | >= 0.80 | LLM-as-judge |
| Answer Relevancy | Answer addresses question? | >= 0.85 | LLM-as-judge |
| Keyword Coverage | Expected keywords present? | >= 0.70 | String match |
| Retrieval Latency | Pipeline time | < 500ms p95 | Instrumentation |
| Cache Hit Rate | Queries served from Redis | >= 40% | Redis tracking |
| Re-rank Improvement | Relevancy lift from re-ranking | >= 10% | A/B comparison |

### Test queries (20+)

```
Policy Types:     "What is whole life insurance?"
                  "What is term life insurance?"
                  "What is Medicare Supplement?"
Policy Status:    "What does it mean if my policy lapsed?"
                  "What is a paid-up policy?"
Premiums:         "What is the grace period for a missed payment?"
                  "How does autopay work?"
Payments:         "What payment methods do you accept?"
                  "Can I pay with a prepaid card?"
Cash Value:       "How does cash value grow in my policy?"
Policy Loans:     "Can I take a loan against my policy?"
                  "What happens if I don't repay my policy loan?"
Beneficiaries:    "How do I change my beneficiary?"
                  "What is a contingent beneficiary?"
Documents:        "Can you email me my policy documents?"
Claims:           "How do I file a life insurance claim?"
Privacy:          "How do I opt out of data sharing?"
Dividends:        "What are my dividend options?"
General:          "How do I speak to a live agent?"
Compliance:       "What is the ACH authorization disclosure?"
```

---

## 13. Production Checklist

- [ ] Async fix: All pgvector calls use `run_in_executor`
- [ ] Redis caching: Active with 1hr TTL, graceful fallback
- [ ] Metadata filtering: Category detection + filtered search
- [ ] Re-ranking: LLM re-ranker with parallel scoring
- [ ] Document pipeline: Supports PDF, URL, text, CSV
- [ ] HNSW index: Created when chunk count exceeds 500
- [ ] Connection pooling: SQLAlchemy pool or PgBouncer
- [ ] Monitoring: Latency, cache hit rate, quality logged
- [ ] Cache invalidation: `clear_cache()` after every ingestion
- [ ] Evaluation: All 20+ test queries score >= 0.80 faithfulness
- [ ] Load test: 50 concurrent RAG queries < 1s p95
- [ ] Graceful degradation: RAG failure -> canned response
