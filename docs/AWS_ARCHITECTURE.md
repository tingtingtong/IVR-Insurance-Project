# CNO IVR - Complete AWS Production Architecture

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Network & VPC Design](#2-network--vpc-design)
3. [Compute Layer (ECS Fargate)](#3-compute-layer-ecs-fargate)
4. [Database Layer](#4-database-layer)
5. [Caching Layer (ElastiCache Redis)](#5-caching-layer-elasticache-redis)
6. [RAG Pipeline on AWS](#6-rag-pipeline-on-aws)
7. [Vector Database (RDS pgvector)](#7-vector-database-rds-pgvector)
8. [Embedding Model Hosting (SageMaker)](#8-embedding-model-hosting-sagemaker)
9. [LLM Integration Layer](#9-llm-integration-layer)
10. [Guardrails & Content Safety](#10-guardrails--content-safety)
11. [Multi-Modal Pipeline (STT/TTS)](#11-multi-modal-pipeline-stttts)
12. [Model Evaluation & MLOps](#12-model-evaluation--mlops)
13. [Document Ingestion Pipeline](#13-document-ingestion-pipeline)
14. [Observability & Monitoring](#14-observability--monitoring)
15. [Security Architecture](#15-security-architecture)
16. [CI/CD Pipeline](#16-cicd-pipeline)
17. [Disaster Recovery & HA](#17-disaster-recovery--ha)
18. [Cost Estimation](#18-cost-estimation)
19. [Scaling Strategy](#19-scaling-strategy)
20. [Migration Plan (Dev to Prod)](#20-migration-plan-dev-to-prod)

---

## 1. Architecture Overview

### High-level AWS topology

```
                              Internet
                                 |
                        +--------v--------+
                        |  CloudFront CDN |  (static assets, dashboard)
                        +--------+--------+
                                 |
                        +--------v--------+
                        | ALB (Application|
                        | Load Balancer)  |  TLS termination, path routing
                        +---+------+------+
                            |      |
              +-------------+      +-------------+
              |                                   |
    +---------v----------+           +-----------v-----------+
    | ECS Fargate        |           | ECS Fargate           |
    | IVR App Service    |           | Document Ingestion    |
    | (FastAPI + LangGraph)          | Worker Service        |
    | Auto-scaling 2-10  |           | (batch processing)    |
    +----+----+----+-----+           +-----------+-----------+
         |    |    |                              |
    +----v--+ | +--v-------+           +----------v----------+
    |Redis  | | |RDS PG    |           | S3 Bucket           |
    |Elasti | | |pgvector  |           | (knowledge docs)    |
    |Cache  | | |Multi-AZ  |           +---------------------+
    +-------+ | +----------+
              |
    +---------v-----------+
    | External APIs       |
    | - Groq (LLM)        |
    | - OpenAI (Embed)    |
    | - Deepgram (STT)    |
    | - ElevenLabs (TTS)  |
    | - Twilio (Telephony)|
    +---------+-----------+
              |
    +---------v-----------+
    | CloudWatch          |
    | X-Ray / Logfire     |
    | (Observability)     |
    +---------------------+
```

### AWS services used

| Service | Purpose | Why This Service |
|---------|---------|-----------------|
| ECS Fargate | Application hosting | Serverless containers -- no EC2 management, auto-scaling, pay-per-use |
| ALB | Load balancing + WebSocket | Layer 7 routing, WebSocket support for Twilio Media Streams, TLS termination |
| RDS PostgreSQL | LangGraph checkpoints + pgvector | Managed PG with Multi-AZ, automated backups, pgvector extension support |
| ElastiCache Redis | Session state + RAG cache | Managed Redis with replication, sub-ms latency for IVR session state |
| S3 | Document storage | Knowledge base PDFs/docs, model artifacts, evaluation results |
| SageMaker | Embedding model hosting (future) | Self-hosted embeddings when scale justifies GPU cost |
| CloudWatch | Metrics + alerting | Native AWS monitoring, custom metrics for RAG latency/cache hits |
| X-Ray | Distributed tracing | Trace requests across ECS -> Redis -> RDS -> external APIs |
| Secrets Manager | API key management | Rotate keys (Groq, OpenAI, Twilio, Deepgram, ElevenLabs) without redeployment |
| ECR | Container registry | Private Docker image storage, vulnerability scanning |
| WAF | Web application firewall | Protect ALB from DDoS, SQL injection, rate limiting |
| CloudFront | CDN | Dashboard static assets, geographic latency reduction |
| CodePipeline | CI/CD | Automated build, test, deploy pipeline |
| Lambda | Event-driven tasks | Document ingestion triggers, scheduled evaluations |
| SQS | Message queue | Decouple document ingestion from main app |
| VPC | Network isolation | Private subnets for DB/cache, public for ALB |

---

## 2. Network & VPC Design

### VPC layout

```
VPC: 10.0.0.0/16  (cno-ivr-vpc)
|
+-- Public Subnets (10.0.1.0/24, 10.0.2.0/24) -- 2 AZs
|   |-- ALB (internet-facing)
|   |-- NAT Gateway (for private subnet outbound)
|
+-- Private App Subnets (10.0.10.0/24, 10.0.11.0/24) -- 2 AZs
|   |-- ECS Fargate tasks (IVR app)
|   |-- ECS Fargate tasks (ingestion worker)
|
+-- Private Data Subnets (10.0.20.0/24, 10.0.21.0/24) -- 2 AZs
    |-- RDS PostgreSQL (Multi-AZ)
    |-- ElastiCache Redis (cluster mode)
```

### Security groups

| SG Name | Inbound | Outbound | Applied To |
|---------|---------|----------|------------|
| `sg-alb` | 443 from 0.0.0.0/0 | All to `sg-app` | ALB |
| `sg-app` | 8080 from `sg-alb` | 5432 to `sg-db`, 6379 to `sg-redis`, 443 to 0.0.0.0/0 (APIs) | ECS tasks |
| `sg-db` | 5432 from `sg-app` | None | RDS |
| `sg-redis` | 6379 from `sg-app` | None | ElastiCache |

### Why 2 AZs minimum?

- RDS Multi-AZ requires 2 AZs for automatic failover
- ECS distributes tasks across AZs for availability
- ElastiCache replication across AZs for Redis failover
- ALB requires subnets in at least 2 AZs

---

## 3. Compute Layer (ECS Fargate)

### Why ECS Fargate over alternatives?

| Option | Pros | Cons | Cost (20 concurrent calls) |
|--------|------|------|---------------------------|
| **ECS Fargate (chosen)** | No EC2 management, auto-scaling, pay per vCPU-second, WebSocket support | Slightly higher cost than EC2 at steady load | ~$150/month |
| ECS on EC2 | Cheaper at steady load, more control | EC2 management overhead, capacity planning | ~$100/month |
| EKS (Kubernetes) | Industry standard, portable | Massive overhead for a single-app deployment | ~$200/month + $73 control plane |
| Lambda | Pay-per-invocation, zero idle cost | 15-min timeout, no WebSocket, cold starts kill IVR latency | ~$50/month but unusable for WebSocket |
| App Runner | Simplest deployment | No WebSocket support, limited config | N/A for this use case |

### Task definition

```json
{
  "family": "cno-ivr-app",
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [{
    "name": "ivr-app",
    "image": "ECR_URI:latest",
    "portMappings": [{"containerPort": 8080}],
    "environment": [],
    "secrets": [
      {"name": "GROQ_API_KEY", "valueFrom": "arn:aws:secretsmanager:..."},
      {"name": "OPENAI_API_KEY", "valueFrom": "arn:aws:secretsmanager:..."}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/cno-ivr",
        "awslogs-region": "us-east-1"
      }
    }
  }]
}
```

### Auto-scaling policy

| Metric | Target | Scale Out | Scale In | Min/Max |
|--------|--------|-----------|----------|---------|
| CPU utilization | 60% | +1 task when >60% for 2 min | -1 task when <30% for 5 min | 2 / 10 |
| Active connections (ALB) | 100 per task | +1 when >100 | -1 when <30 | 2 / 10 |

**Why CPU-based?** Each IVR call consumes CPU for STT/TTS audio processing, LLM response parsing, and WebSocket management. CPU correlates directly with concurrent call count.

---

## 4. Database Layer

### RDS PostgreSQL + pgvector

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Engine | PostgreSQL 16 | pgvector extension support, latest features |
| Instance | `db.r6g.large` (2 vCPU, 16 GB RAM) | Sufficient for vector search workload |
| Storage | 100 GB gp3 (3000 IOPS) | pgvector indexes are memory-heavy but storage-light |
| Multi-AZ | Yes | Automatic failover, zero data loss |
| Read replica | 1 (optional) | Offload RAG read queries from primary |
| Backup | 7-day automated, daily snapshots | Point-in-time recovery |
| Encryption | AES-256 (at rest), TLS (in transit) | Compliance requirement |

### Schema

```
Databases:
  cno_ivr
    |-- Tables (LangGraph checkpointing):
    |     checkpoint, checkpoint_blobs, checkpoint_writes
    |
    |-- Tables (pgvector RAG):
    |     langchain_pg_collection    -- collection metadata
    |     langchain_pg_embedding     -- vectors + metadata (JSONB)
    |
    |-- Tables (application):
    |     call_history               -- call logs for dashboard
    |
    |-- Extensions:
          vector                     -- pgvector
```

### Connection pooling

| Option | Approach | Why |
|--------|----------|-----|
| **RDS Proxy (chosen)** | Managed connection pooler | Handles connection multiplexing, IAM auth, failover-aware. No extra infra. |
| PgBouncer on ECS | Sidecar container | Full control, but operational overhead |
| SQLAlchemy pool | Application-level | Already built-in, but doesn't help across ECS tasks |

---

## 5. Caching Layer (ElastiCache Redis)

### Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Engine | Redis 7.x | Latest features, Lua scripting for atomic ops |
| Node type | `cache.r6g.large` (2 vCPU, 13 GB) | Ample memory for session state + RAG cache |
| Cluster mode | Disabled (single shard) | Our data fits in one node; simpler ops |
| Replicas | 1 (Multi-AZ) | Read replica for failover |
| Encryption | In-transit (TLS) + at-rest | PII in session state |
| Eviction | `allkeys-lru` | Auto-evict least recently used when memory full |

### Data stored in Redis

| Key Pattern | Data | TTL | Size Estimate |
|-------------|------|-----|---------------|
| `cno:session:{call_sid}` | IVR session state (auth, slots, messages) | 1 hour | ~2 KB per session |
| `cno:rag:{hash}` | Cached RAG context strings | 1 hour | ~1 KB per entry |

### Why ElastiCache over alternatives?

| Option | Pros | Cons |
|--------|------|------|
| **ElastiCache Redis (chosen)** | Managed, Multi-AZ failover, sub-ms latency, encryption, monitoring | Cost (~$100/month for r6g.large) |
| MemoryDB for Redis | Durability (writes to disk), Multi-AZ | More expensive, durability not needed for cache |
| Self-hosted Redis on ECS | Cheaper, full control | Operational burden, no automatic failover |
| DynamoDB DAX | Serverless caching | Only caches DynamoDB queries, not general-purpose |

---

## 6. RAG Pipeline on AWS

### End-to-end flow on AWS

```
+-------------------+     +-------------------+     +--------------------+
| ECS Fargate       |     | ElastiCache Redis |     | RDS PG + pgvector  |
| (IVR App)         |     | (RAG Cache)       |     | (Vector Store)     |
|                   |     |                   |     |                    |
| 1. Caller query   |     |                   |     |                    |
| 2. Cache check ---|---->| 3. Hit? Return    |     |                    |
|    (2-5ms)        |     |    cached context  |     |                    |
|                   |     |                   |     |                    |
| 4. Category       |     |                   |     |                    |
|    detection      |     |                   |     |                    |
|    (<1ms)         |     |                   |     |                    |
|                   |     |                   |     |                    |
| 5. Embed query ---|---->| (OpenAI API call) |     |                    |
|    (~80ms)        |     |                   |     |                    |
|                   |     |                   |     |                    |
| 6. Vector search -|-----|-------------------|---->| 7. HNSW ANN search |
|    (run_in_exec)  |     |                   |     |    with metadata   |
|    (~50-100ms)    |     |                   |     |    filter          |
|                   |     |                   |     |    9 candidates    |
|                   |     |                   |     |                    |
| 8. Re-rank -------|---->| (Groq API call)   |     |                    |
|    (~200ms)       |     | parallel scoring  |     |                    |
|                   |     |                   |     |                    |
| 9. Cache result --|---->| 10. Store with    |     |                    |
|                   |     |     1hr TTL       |     |                    |
|                   |     |                   |     |                    |
| 11. Return        |     |                   |     |                    |
|     context       |     |                   |     |                    |
+-------------------+     +-------------------+     +--------------------+

Total latency:
  Cache hit:  2-5ms
  Cache miss: 350-500ms (embed 80 + search 100 + rerank 200 + overhead 50)
```

---

## 7. Vector Database (RDS pgvector)

### Why RDS pgvector over dedicated vector DBs on AWS?

| Option | Service | Cost/Month | Ops Overhead | Integration |
|--------|---------|-----------|--------------|-------------|
| **RDS pgvector (chosen)** | RDS PostgreSQL | ~$150 (db.r6g.large) | Low (managed) | Same DB as LangGraph checkpointing |
| Amazon OpenSearch Serverless | Managed | ~$300+ (compute units) | Low | Separate service, different query syntax |
| Pinecone (external) | SaaS | ~$70 (s1.x1) | None | External dependency, data leaves AWS |
| Self-hosted Qdrant on ECS | Fargate | ~$100 (compute) | Medium | Separate container, backup management |
| Amazon Neptune (graph + vector) | Managed | ~$200+ | Low | Overkill for our use case |

### pgvector on RDS setup

```sql
-- Enable extension (requires rds_superuser)
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify
SELECT * FROM pg_extension WHERE extname = 'vector';

-- HNSW index (create when vectors > 500)
CREATE INDEX CONCURRENTLY idx_embedding_hnsw
ON langchain_pg_embedding
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Metadata index for filtered searches
CREATE INDEX idx_embedding_category
ON langchain_pg_embedding
USING gin (cmetadata jsonb_path_ops);
```

### Performance tuning on RDS

```
-- postgresql.conf parameters (via RDS parameter group)
shared_buffers = 4GB              -- 25% of instance RAM
effective_cache_size = 12GB       -- 75% of instance RAM
work_mem = 256MB                  -- for vector sort operations
maintenance_work_mem = 1GB        -- for HNSW index builds
max_parallel_workers_per_gather = 2  -- parallel seq scan
```

---

## 8. Embedding Model Hosting (SageMaker)

### Current: OpenAI API (Phase 1)

```
ECS App --> HTTPS --> OpenAI API (text-embedding-3-small)
  Latency: ~80ms
  Cost: $0.00002 / 1K tokens
  Good for: < 100K queries/day
```

### Future: Self-hosted on SageMaker (Phase 2)

```
ECS App --> VPC Endpoint --> SageMaker Endpoint (bge-large-en-v1.5)
  Latency: ~30ms (within VPC)
  Cost: $1.41/hr (ml.g5.xlarge) = ~$1,015/month
  Good for: > 100K queries/day, or regulatory constraints
```

### SageMaker endpoint configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Model | `BAAI/bge-large-en-v1.5` | MTEB 64.2, 1024 dims, good quality-cost balance |
| Instance | `ml.g5.xlarge` (1 A10G GPU, 24GB) | Sufficient for single-model serving |
| Min instances | 1 | Always-on for IVR latency requirements |
| Max instances | 3 | Auto-scale for peak hours |
| Scaling metric | `InvocationsPerInstance` > 200/min | Scale out before latency degrades |

### When to migrate to SageMaker

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Daily query volume | > 100,000 | SageMaker becomes cheaper than API |
| Latency requirement | < 50ms p95 | In-VPC inference is 30ms vs 80ms API |
| Data residency | Must stay in AWS | Can't send to OpenAI |
| Cost per month (embeddings) | > $600 | SageMaker at $1,015/mo serves unlimited queries |

---

## 9. LLM Integration Layer

### Current architecture: External LLM APIs

```
+------------------+     +------------------+
| ECS App          |     | External LLM APIs|
|                  |     |                  |
| Router Node -----|---->| Groq Llama-3.1-8B|  (intent classification)
| FAQ Node --------|---->| Groq Llama-3.3-70B| (grounded answers)
| Re-ranker -------|---->| Groq Llama-3.1-8B|  (relevance scoring)
| Policy Node -----|---->| Groq Llama-3.3-70B| (policy inquiry)
| Payment Node ----|---->| Groq Llama-3.3-70B| (payment processing)
+------------------+     +------------------+
```

### Why Groq over AWS Bedrock?

| Option | Models | Latency | Cost | Integration |
|--------|--------|---------|------|-------------|
| **Groq (chosen)** | Llama 3.x (8B, 70B) | 50-200ms (fastest available) | ~$0.05-0.27/M tokens | Simple API, existing integration |
| AWS Bedrock | Claude, Llama, Titan | 200-500ms | ~$0.15-3.00/M tokens | Native AWS, IAM auth |
| OpenAI | GPT-4o, GPT-4o-mini | 300-1000ms | ~$2.50-10/M tokens | Simple API but expensive |
| Self-hosted (SageMaker) | Llama 3 70B | 500-1500ms | ~$4,000/month (p4d.24xl) | Full control but very expensive |

**Decision**: Groq for IVR because latency is critical. A 50ms router classification vs 300ms on Bedrock makes a noticeable difference in caller experience. For non-latency-critical batch tasks (evaluation, ingestion), use Bedrock.

### Resilience pattern

```
Primary:   Groq API (Llama-3.3-70B)     -- 50-200ms
Fallback:  AWS Bedrock (Claude Sonnet)   -- 300-500ms (if Groq 503/429)
Last resort: Canned response            -- 0ms (if all APIs fail)
```

### API key management

All LLM API keys stored in AWS Secrets Manager:
- `cno-ivr/groq-api-key`
- `cno-ivr/openai-api-key`
- `cno-ivr/deepgram-api-key`
- `cno-ivr/elevenlabs-api-key`
- `cno-ivr/twilio-credentials`

ECS task role has `secretsmanager:GetSecretValue` permission. Keys injected at container start via ECS secrets configuration. Rotation policy: 90 days.

---

## 10. Guardrails & Content Safety

### Why guardrails for IVR?

Insurance IVR handles sensitive financial data (policy numbers, SSN, bank accounts, payment info). Guardrails prevent:
- LLM hallucination of financial advice
- PII leakage in logs or responses
- Prompt injection via caller speech
- Off-topic responses (IVR must stay on-domain)

### Guardrail layers

```
Layer 1: Input Guardrails (before LLM)
  +-- PII detection & redaction in logs
  +-- Prompt injection detection
  +-- Topic boundary enforcement

Layer 2: RAG Guardrails (retrieval)
  +-- Context grounding (answer ONLY from retrieved chunks)
  +-- Source attribution tracking
  +-- Confidence threshold (skip if similarity < 0.5)

Layer 3: Output Guardrails (after LLM)
  +-- Financial advice prohibition
  +-- PII masking in TTS output
  +-- Response length limits (IVR: max 2 sentences)
  +-- Hallucination detection (cross-check with context)

Layer 4: Compliance Guardrails (regulatory)
  +-- GLBA privacy script enforcement
  +-- ACH authorization script enforcement
  +-- Call recording disclosure
  +-- Relationship restriction enforcement
```

### Implementation approach

| Guardrail | Implementation | AWS Service |
|-----------|---------------|-------------|
| PII redaction | `utils/pii_redactor.py` (regex + pattern matching) | Comprehend (optional, for ML-based detection) |
| Prompt injection | System prompt hardening + keyword detection | Bedrock Guardrails (optional) |
| Topic enforcement | Router node intent classification | Built-in (LangGraph) |
| Context grounding | FAQ node prompt: "Use ONLY the following context" | Built-in (RAG pipeline) |
| Financial advice block | System prompt: "Never provide financial/legal/medical advice" | Built-in (system prompt) |
| Response length | `max_tokens=200` on LLM calls | Built-in (LangChain) |
| PII in logs | `utils/pii_redactor.py` strips SSN, card numbers from log output | CloudWatch log filtering |
| Compliance scripts | RAG knowledge base includes exact script text | Built-in (seed_knowledge.py) |

### AWS Bedrock Guardrails (optional enhancement)

```
Bedrock Guardrail Policy:
  - Denied topics: investment advice, legal counsel, medical guidance
  - PII filters: SSN, credit card, bank account (BLOCK in output)
  - Content filters: hate speech, violence, sexual (BLOCK)
  - Grounding check: enabled (ensures answer matches context)
  - Word/phrase filters: competitor names, profanity
```

Cost: ~$1 per 1000 text units. Optional -- current built-in guardrails are sufficient for MVP.

---

## 11. Multi-Modal Pipeline (STT/TTS)

### Audio processing architecture on AWS

```
+----------+     +---------+     +----------+     +-----------+     +----------+
|  Twilio  |     |  ALB    |     | ECS App  |     | Deepgram  |     | Eleven   |
|  PSTN    |<--->| WebSock |<--->| WebSocket|<--->| Nova-2    |     | Labs     |
|  Call    |     |  Proxy  |     | Handler  |     | (STT)     |     | (TTS)    |
+----------+     +---------+     +----+-----+     +-----------+     +-----+----+
                                      |                                   |
                                      +------- LangGraph Processing ------+
                                      |                                   |
                                 mulaw 8kHz                          mulaw 8kHz
                                 audio chunks                        audio stream
```

### STT: Deepgram Nova-2

| Parameter | Value | Why |
|-----------|-------|-----|
| Model | `nova-2` | Best accuracy for telephony (8kHz mulaw), lowest latency |
| Encoding | `mulaw` | Twilio Media Streams format -- must match |
| Sample rate | 8000 Hz | Telephony standard |
| Endpointing | 300ms | Balance between responsiveness and false triggers |
| Smart format | Enabled | Auto-formats dates, currencies, phone numbers |
| Interim results | Enabled | Powers barge-in detection (VAD) |

**Why Deepgram over AWS Transcribe?**

| Feature | Deepgram Nova-2 | AWS Transcribe Streaming |
|---------|----------------|-------------------------|
| Latency | 100-200ms | 300-500ms |
| Accuracy (telephony) | 95%+ | 90-93% |
| Smart formatting | Built-in | Limited |
| Interim results | Yes (real-time) | Yes (but higher latency) |
| Cost | ~$0.0043/min | ~$0.024/min |
| WebSocket API | Native | Native |

### TTS: ElevenLabs

| Parameter | Value | Why |
|-----------|-------|-----|
| Model | `eleven_turbo_v2` | Lowest latency (~200ms first byte) |
| Voice | Rachel (21m00Tcm4TlvDq8ikWAM) | Professional, warm, insurance-appropriate |
| Streaming | WebSocket | Continuous audio stream, no chunking gaps |
| Optimize latency | Level 3 (of 4) | IVR-optimized -- slight quality trade for speed |

**Why ElevenLabs over Amazon Polly?**

| Feature | ElevenLabs | Amazon Polly |
|---------|------------|-------------|
| Voice quality | Near-human (neural) | Good (neural voices) but robotic at times |
| Latency (first byte) | ~200ms | ~300ms |
| Custom voices | Yes (voice cloning) | No |
| Streaming | WebSocket native | HTTP chunked |
| Cost | ~$0.30/1K chars | ~$0.016/1K chars |
| Natural prosody | Excellent | Good |

**Decision**: ElevenLabs for production quality. Amazon Polly as cost-saving fallback for non-critical prompts (hold messages, system announcements).

### Future: OpenAI Realtime API

The codebase already has `auth_mode: str = "realtime"` in settings for an OpenAI Realtime API path. This provides:
- Bidirectional audio WebSocket
- Combined STT + LLM + TTS in one API call
- Latency: ~500ms end-to-end (vs current 800-1200ms)
- Cost: Higher but simpler architecture

---

## 12. Model Evaluation & MLOps

### Evaluation pipeline on AWS

```
+-------------+     +-----------+     +-------------+     +-----------+
| EventBridge |     | Lambda    |     | ECS Fargate |     | S3 Bucket |
| (Scheduled  |---->| (Trigger) |---->| (Eval Task) |---->| (Results) |
|  weekly)    |     |           |     |             |     |           |
+-------------+     +-----------+     +------+------+     +-----+-----+
                                             |                   |
                                      +------v------+     +-----v--------+
                                      | Groq API    |     | CloudWatch   |
                                      | (LLM Judge) |     | (Metrics)    |
                                      +-------------+     +--------------+
```

### Evaluation metrics tracked

| Metric | Source | Target | CloudWatch Alarm |
|--------|--------|--------|-----------------|
| RAG Faithfulness | eval_rag.py (LLM-as-judge) | >= 0.85 | Alert if < 0.75 for 2 consecutive runs |
| Contextual Relevancy | eval_rag.py | >= 0.80 | Alert if < 0.70 |
| Answer Relevancy | eval_rag.py | >= 0.85 | Alert if < 0.75 |
| Intent Classification Accuracy | eval_intents.py | >= 0.90 | Alert if < 0.80 |
| Slot Extraction Accuracy | eval_slots.py | >= 0.85 | Alert if < 0.75 |
| E2E Call Success Rate | eval_e2e_twilio.py | >= 0.90 | Alert if < 0.80 |
| RAG Cache Hit Rate | Redis metrics | >= 40% | Alert if < 20% |
| p95 RAG Latency | Application logs | < 500ms | Alert if > 1000ms |

### MLflow on AWS

```
MLflow Tracking Server:
  Backend: RDS PostgreSQL (same instance, separate database: mlflow_db)
  Artifact store: S3 (s3://cno-ivr-mlflow-artifacts/)
  Hosting: ECS Fargate (separate service, internal ALB)

Tracked experiments:
  - rag_evaluation (weekly automated)
  - intent_classification (on model change)
  - slot_extraction (on prompt change)
  - e2e_call_flow (monthly)
```

### Model versioning & rollback

| Component | Version tracking | Rollback mechanism |
|-----------|-----------------|-------------------|
| LLM (Groq) | Model name in settings (`groq_model`) | Change env var, ECS redeploy |
| Embedding model | `openai_embedding_model` in settings | Change env var + re-embed all chunks |
| RAG knowledge base | S3 versioning on source docs + PG collection versioning | Re-ingest from S3 versioned docs |
| System prompts | Git-versioned in `core/prompts/` | Git revert + redeploy |
| LangGraph flow | Git-versioned in `core/graph/` | Git revert + redeploy |

---

## 13. Document Ingestion Pipeline

### Architecture on AWS

```
+----------+     +---------+     +----------+     +----------+     +-----------+
| S3 Bucket|     | Lambda  |     | SQS      |     | ECS Task |     | RDS PG    |
| (upload) |---->| (trigger|---->| (queue)  |---->| (worker) |---->| pgvector  |
|          |     |  + valid)|     |          |     |          |     |           |
+----------+     +---------+     +----------+     +----+-----+     +-----------+
                                                       |
                                                 +-----v------+
                                                 | OpenAI API |
                                                 | (embedding)|
                                                 +------------+
```

### Flow

1. **Upload**: User uploads PDF/text/CSV to `s3://cno-ivr-knowledge/incoming/`
2. **Trigger**: S3 event triggers Lambda function
3. **Validate**: Lambda validates file type, size (<50MB), virus scan (optional)
4. **Queue**: Lambda puts message on SQS queue with S3 key and metadata
5. **Process**: ECS worker task picks up SQS message
6. **Load**: Worker downloads file from S3, runs appropriate loader
7. **Chunk**: RecursiveCharacterTextSplitter (500 chars, 50 overlap)
8. **Enrich**: Auto-detect category, add metadata
9. **Embed**: Call OpenAI API to generate embeddings
10. **Store**: Insert into pgvector via PGVector.add_documents()
11. **Invalidate**: Clear Redis RAG cache (`clear_cache()`)
12. **Notify**: SNS notification on success/failure

### Why SQS between Lambda and ECS?

- **Decoupling**: Lambda can return immediately; processing happens async
- **Retry**: SQS retries failed messages (3 attempts, then DLQ)
- **Rate limiting**: ECS worker pulls at its own pace, won't overwhelm OpenAI API
- **Visibility**: SQS metrics show queue depth, processing rate

---

## 14. Observability & Monitoring

### Three pillars on AWS

```
+-------------------------------------------+
|             Observability Stack            |
+-------------------------------------------+
|                                           |
|  Logs:    CloudWatch Logs                 |
|           - Application logs (structlog)  |
|           - ECS task logs                 |
|           - ALB access logs               |
|                                           |
|  Metrics: CloudWatch Metrics              |
|           - Custom: rag_latency_ms        |
|           - Custom: rag_cache_hit_rate    |
|           - Custom: llm_latency_ms        |
|           - Custom: intent_distribution   |
|           - AWS: ECS CPU/Memory           |
|           - AWS: RDS connections/IOPS     |
|           - AWS: Redis hit rate/memory    |
|                                           |
|  Traces:  AWS X-Ray + Logfire             |
|           - Request -> Redis -> RDS       |
|           - Request -> Groq -> Response   |
|           - WebSocket lifecycle           |
|                                           |
+-------------------------------------------+
```

### CloudWatch dashboard

| Panel | Metric | Source |
|-------|--------|--------|
| Active calls | ECS active WebSocket connections | ALB metrics |
| RAG latency (p50/p95/p99) | `rag_latency_ms` | Custom metric |
| Cache hit rate | `rag_cache_hits / rag_total_queries` | Custom metric |
| LLM latency by node | `llm_latency_ms` per node | Custom metric |
| Intent distribution | `intent_count` by type | Custom metric |
| Error rate | 5xx responses | ALB metrics |
| DB connections | `DatabaseConnections` | RDS metrics |
| Redis memory | `BytesUsedForCache` | ElastiCache metrics |

### Alarms

| Alarm | Condition | Action |
|-------|-----------|--------|
| High RAG latency | p95 > 1000ms for 5 min | SNS -> PagerDuty |
| Low cache hit rate | < 20% for 15 min | SNS -> email |
| High error rate | 5xx > 5% for 2 min | SNS -> PagerDuty |
| DB connections high | > 80% max for 5 min | SNS -> email |
| ECS CPU high | > 80% for 5 min | Auto-scale + SNS |
| Redis memory high | > 80% for 10 min | SNS -> email |
| Evaluation quality drop | Faithfulness < 0.75 | SNS -> email |

---

## 15. Security Architecture

### Defense in depth

```
Layer 1: Network
  +-- VPC isolation (private subnets for DB/cache)
  +-- Security groups (least privilege)
  +-- WAF on ALB (DDoS, injection protection)
  +-- NACLs (subnet-level firewall)

Layer 2: Identity & Access
  +-- IAM roles for ECS tasks (least privilege)
  +-- RDS IAM authentication
  +-- Secrets Manager for API keys
  +-- No long-lived credentials in containers

Layer 3: Data Protection
  +-- RDS encryption at rest (AES-256, AWS KMS)
  +-- RDS encryption in transit (TLS 1.2+)
  +-- ElastiCache encryption (in-transit + at-rest)
  +-- S3 encryption (SSE-S3 or SSE-KMS)
  +-- PII redaction in logs

Layer 4: Application
  +-- Twilio webhook signature validation
  +-- WebSocket auth token
  +-- Dashboard HTTP Basic auth
  +-- CORS restriction (prod origins only)
  +-- Input validation / guardrails

Layer 5: Compliance
  +-- GLBA privacy controls
  +-- PCI DSS considerations (payment card data)
  +-- SOC 2 audit trail (CloudTrail)
  +-- HIPAA-eligible services (if health data)
```

### IAM roles

| Role | Permissions | Applied To |
|------|------------|------------|
| `cno-ivr-task-role` | secretsmanager:GetSecretValue, s3:GetObject, cloudwatch:PutMetricData, xray:PutTraceSegments | ECS IVR app task |
| `cno-ivr-ingestion-role` | s3:GetObject, sqs:ReceiveMessage, secretsmanager:GetSecretValue | ECS ingestion worker |
| `cno-ivr-lambda-role` | s3:GetObject, sqs:SendMessage, logs:PutLogEvents | Ingestion trigger Lambda |
| `cno-ivr-rds-proxy-role` | rds-db:connect | RDS Proxy |

---

## 16. CI/CD Pipeline

### Pipeline architecture

```
GitHub (main branch)
  |
  v
CodePipeline
  |
  +-- Stage 1: Source
  |     Pull from GitHub via CodeStar connection
  |
  +-- Stage 2: Build (CodeBuild)
  |     - docker build
  |     - Push to ECR
  |     - Run unit tests (pytest)
  |     - Run linting (ruff)
  |
  +-- Stage 3: Test (CodeBuild)
  |     - Run eval_intents.py
  |     - Run eval_rag.py (against staging DB)
  |     - Run eval_slots.py
  |     - Fail pipeline if faithfulness < 0.80
  |
  +-- Stage 4: Deploy Staging (ECS)
  |     - Update ECS service (staging cluster)
  |     - Run smoke tests
  |
  +-- Stage 5: Manual Approval
  |     - SNS notification to team
  |     - Reviewer approves in console
  |
  +-- Stage 6: Deploy Production (ECS)
        - Blue/green deployment via CodeDeploy
        - Health check validation
        - Auto-rollback on failure
```

### Blue/green deployment

```
                   ALB
                  /    \
    +------------+      +-------------+
    | Blue (v1)  |      | Green (v2)  |
    | (current)  |      | (new)       |
    +------------+      +-------------+

1. Deploy v2 to green target group
2. ALB health checks pass on green
3. Switch ALB traffic: blue -> green
4. Monitor for 5 minutes
5. If errors: auto-rollback to blue
6. If stable: drain and terminate blue
```

---

## 17. Disaster Recovery & HA

### RTO/RPO targets

| Component | RTO (Recovery Time) | RPO (Data Loss) | Strategy |
|-----------|-------------------|-----------------|----------|
| Application (ECS) | < 2 min | 0 (stateless) | Multi-AZ auto-scaling, ALB health checks |
| Database (RDS) | < 5 min | 0 (Multi-AZ sync replication) | Automatic failover to standby |
| Cache (Redis) | < 1 min | < 1 min (async replication) | Multi-AZ replica promotion |
| Knowledge base (S3) | 0 | 0 | S3 11-9s durability, versioning |

### Multi-AZ deployment

```
       us-east-1a                    us-east-1b
  +------------------+         +------------------+
  | Public Subnet    |         | Public Subnet    |
  |   ALB node       |         |   ALB node       |
  +------------------+         +------------------+
  | Private App      |         | Private App      |
  |   ECS Task 1     |         |   ECS Task 2     |
  +------------------+         +------------------+
  | Private Data     |         | Private Data     |
  |   RDS Primary    |         |   RDS Standby    |
  |   Redis Primary  |         |   Redis Replica  |
  +------------------+         +------------------+
```

### Backup strategy

| Resource | Backup Method | Retention | Frequency |
|----------|-------------|-----------|-----------|
| RDS | Automated snapshots + PITR | 7 days | Continuous (PITR), daily (snapshot) |
| S3 knowledge docs | S3 versioning | 30 days | On every upload |
| Redis | Not backed up (cache, regenerable) | N/A | N/A |
| Application config | Git (GitHub) | Permanent | On every commit |
| Secrets | Secrets Manager versioning | 10 versions | On rotation |

---

## 18. Cost Estimation

### Monthly cost breakdown (20 concurrent calls, production)

| Service | Configuration | Monthly Cost |
|---------|--------------|-------------|
| **ECS Fargate** | 2 tasks x 1 vCPU, 2GB (avg), 24/7 | ~$120 |
| **ALB** | Application Load Balancer + LCU | ~$25 |
| **RDS PostgreSQL** | db.r6g.large, Multi-AZ, 100GB gp3 | ~$300 |
| **ElastiCache Redis** | cache.r6g.large, 1 replica | ~$200 |
| **S3** | 10 GB storage + requests | ~$2 |
| **CloudWatch** | Logs (10GB), metrics, alarms | ~$30 |
| **Secrets Manager** | 6 secrets | ~$3 |
| **ECR** | 5 GB images | ~$1 |
| **NAT Gateway** | 1 NAT, ~50GB data | ~$40 |
| **WAF** | Basic rules | ~$10 |
| **CodePipeline** | 1 pipeline | ~$1 |
| **External: Groq** | ~500K tokens/day | ~$15 |
| **External: OpenAI** | Embeddings ~50K tokens/day | ~$5 |
| **External: Deepgram** | ~1000 min/day | ~$130 |
| **External: ElevenLabs** | ~500K chars/day | ~$150 |
| **External: Twilio** | ~500 calls/day, phone number | ~$200 |
| | | |
| **Total** | | **~$1,232/month** |

### Cost optimization opportunities

| Optimization | Savings | Trade-off |
|-------------|---------|-----------|
| Reserved Instances (RDS, 1yr) | ~30% on RDS ($90/mo) | 1-year commitment |
| Savings Plans (Fargate) | ~20% on compute ($24/mo) | 1-year commitment |
| Spot Fargate (ingestion worker) | ~70% on ingestion compute | Interruption risk (acceptable for batch) |
| Switch to Polly for system prompts | ~90% on some TTS ($50/mo) | Slightly lower voice quality |
| Self-hosted embeddings (SageMaker) | Break-even at 100K queries/day | GPU instance cost |
| Redis cache optimization | Reduce RDS/API load | Already planned |

---

## 19. Scaling Strategy

### Horizontal scaling triggers

| Component | Metric | Scale Out | Scale In |
|-----------|--------|-----------|----------|
| ECS Fargate | CPU > 60% | +1 task (max 10) | -1 task when CPU < 30% |
| ECS Fargate | ALB connections > 100/task | +1 task | -1 when < 30/task |
| RDS Read Replica | Read IOPS > 5000 | Add replica | Remove when < 1000 |
| SageMaker (future) | Invocations > 200/min | +1 instance | -1 when < 50/min |

### Capacity planning

| Concurrent Calls | ECS Tasks | RDS Size | Redis Size | Monthly Cost |
|-----------------|-----------|----------|------------|-------------|
| 10 | 2 | db.r6g.medium | cache.r6g.medium | ~$800 |
| 20 | 3 | db.r6g.large | cache.r6g.large | ~$1,200 |
| 50 | 5 | db.r6g.xlarge | cache.r6g.xlarge | ~$2,000 |
| 100 | 10 | db.r6g.2xlarge | cache.r6g.2xlarge | ~$4,000 |
| 500+ | 20+ | Aurora Serverless v2 | Redis cluster mode | ~$10,000+ |

### Database scaling path

```
Phase 1 (current): Single RDS instance
  - Up to 50 concurrent connections
  - Sequential pgvector scan (< 1000 vectors)

Phase 2 (500-5K vectors): RDS + Read Replica
  - RAG reads go to replica
  - HNSW index for fast ANN search
  - RDS Proxy for connection pooling

Phase 3 (5K-50K vectors): RDS + Multiple Replicas
  - Partitioned pgvector tables by category
  - Dedicated read endpoint for RAG queries

Phase 4 (50K+ vectors): Aurora Serverless v2 or Dedicated Vector DB
  - Aurora auto-scales compute
  - Or migrate to Qdrant/Pinecone for pure vector workload
```

---

## 20. Migration Plan (Dev to Prod)

### Phase 1: Infrastructure Setup (Week 1)

- [ ] Create VPC with public/private subnets across 2 AZs
- [ ] Set up security groups
- [ ] Deploy RDS PostgreSQL with pgvector extension
- [ ] Deploy ElastiCache Redis
- [ ] Set up NAT Gateway
- [ ] Create ECR repository
- [ ] Store all API keys in Secrets Manager
- [ ] Set up CloudWatch log groups

### Phase 2: Application Deployment (Week 2)

- [ ] Build Docker image, push to ECR
- [ ] Create ECS cluster, task definition, service
- [ ] Configure ALB with WebSocket support
- [ ] Set up TLS certificate (ACM)
- [ ] Configure WAF rules
- [ ] Deploy application to staging
- [ ] Run smoke tests

### Phase 3: Data Migration (Week 2-3)

- [ ] Create S3 bucket for knowledge documents
- [ ] Upload knowledge base documents to S3
- [ ] Run document ingestion pipeline
- [ ] Verify pgvector data integrity
- [ ] Create HNSW index if vectors > 500
- [ ] Run RAG evaluation suite

### Phase 4: Testing & Validation (Week 3)

- [ ] Run full evaluation suite (intents, RAG, slots, e2e)
- [ ] Load test: 50 concurrent calls
- [ ] Failover test: kill primary RDS, verify auto-failover
- [ ] Cache test: verify Redis failover
- [ ] Security scan: WAF, security groups, IAM policies
- [ ] Penetration test (optional)

### Phase 5: Go-Live (Week 4)

- [ ] Set up CI/CD pipeline (CodePipeline)
- [ ] Configure CloudWatch alarms and dashboards
- [ ] Set up on-call rotation (PagerDuty/SNS)
- [ ] DNS cutover (Route 53)
- [ ] Monitor first 24 hours
- [ ] Document runbook for operations team
