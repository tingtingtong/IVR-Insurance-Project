# AWS Architecture Study Plan — 10 Days, 8 hrs/day

Focused on: deploying a real-time WebSocket IVR (FastAPI + Twilio + Deepgram + ElevenLabs + LangGraph) to AWS at scale (3k calls/day, 100+ concurrent).

---

## DAY 1 — VPC & Networking (The Foundation)
**Why**: Everything in AWS lives inside a VPC. If you can't explain networking, nothing else makes sense.

### Hour 1-2: VPC Core Concepts (Read + Watch)
**What to learn:**
- What is a VPC? (your private network in AWS)
- CIDR blocks — how IP ranges work (10.0.0.0/16 = 65k IPs)
- Region vs Availability Zone (AZ) — AZ = physical data center

**Watch:** YouTube — "AWS VPC Beginner to Pro - Step by Step" by Stephane Maarek (~30 min)
**Read:** AWS docs — "How Amazon VPC works" (just the concepts page)

### Hour 3-4: Subnets, Route Tables, Internet Gateway, NAT Gateway
**What to learn:**
- Public subnet — has route to Internet Gateway (ALB lives here)
- Private subnet — no direct internet access (your app lives here)
- NAT Gateway — lets private subnet make outbound calls (to Groq, Deepgram APIs) without being publicly reachable
- Route table — rules that say "traffic to 0.0.0.0/0 goes to IGW or NAT"

**Key diagram to draw:**
```
VPC (10.0.0.0/16)
├── AZ-a
│   ├── Public subnet  (10.0.1.0/24)  ← ALB, NAT Gateway
│   └── Private subnet (10.0.3.0/24)  ← ECS tasks, RDS
├── AZ-b
│   ├── Public subnet  (10.0.2.0/24)  ← ALB (cross-AZ)
│   └── Private subnet (10.0.4.0/24)  ← ECS tasks, RDS standby
```

### Hour 5-6: Security Groups & NACLs
**What to learn:**
- Security Group = firewall at instance/container level (stateful — if you allow inbound, response is auto-allowed)
- NACL = firewall at subnet level (stateless — must allow both directions)
- For interviews, focus on Security Groups:
  - ALB SG: inbound 443 (HTTPS) from 0.0.0.0/0
  - ECS SG: inbound 8000 from ALB SG only
  - RDS SG: inbound 5432 from ECS SG only
  - Redis SG: inbound 6379 from ECS SG only

**Practice:** Draw the SG rules for your IVR on paper

### Hour 7-8: Hands-on (Free Tier)
- Log into AWS Console (free account)
- Create a VPC with the VPC Wizard (2 AZ, public+private subnets)
- Observe what it creates: subnets, route tables, IGW, NAT
- Delete everything after (to avoid charges)

**Interview answer you should be able to give:**
> "I'd deploy in a VPC with 2 AZs for high availability. ALB sits in public subnets, ECS tasks and databases in private subnets. NAT Gateway lets containers call external APIs like Groq and Deepgram without being publicly exposed. Security groups restrict traffic: only ALB can reach ECS on port 8000, only ECS can reach RDS on 5432 and Redis on 6379."

---

## DAY 2 — ECS Fargate (Compute)
**Why**: This is where your FastAPI app runs. Most interview questions center here.

### Hour 1-2: ECS Concepts
**What to learn:**
- **Cluster** — logical grouping (just a namespace, no servers with Fargate)
- **Task Definition** — like a docker-compose.yml for AWS. Specifies:
  - Docker image (from ECR)
  - CPU/memory (e.g., 2 vCPU, 4 GB)
  - Port mappings
  - Environment variables / secrets
  - Log configuration
- **Task** — a running instance of a task definition (= a running container)
- **Service** — ensures N tasks are always running, connects to ALB, handles rolling deploys
- **Fargate vs EC2 launch type** — Fargate = serverless (no EC2 to manage), EC2 = you manage the servers

**Watch:** YouTube — "AWS ECS Fargate Tutorial" by Be A Better Dev (~20 min)

### Hour 3-4: Task Definition Deep Dive
**Map your docker-compose.yml to ECS concepts:**
```
docker-compose.yml          →  ECS equivalent
─────────────────────────────────────────────
image: your-app             →  containerDefinitions[0].image (from ECR)
ports: "8080:8080"          →  containerDefinitions[0].portMappings
env_file: .env              →  secrets (from Secrets Manager) + environment
depends_on: redis, postgres →  These become separate AWS services (ElastiCache, RDS)
command: uvicorn ...        →  containerDefinitions[0].command
```

**Example task definition (know this structure):**
```json
{
  "family": "cno-ivr",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "containerDefinitions": [{
    "name": "ivr-app",
    "image": "123456789.dkr.ecr.us-east-1.amazonaws.com/cno-ivr:latest",
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "secrets": [
      {"name": "GROQ_API_KEY", "valueFrom": "arn:aws:secretsmanager:..."},
      {"name": "DEEPGRAM_API_KEY", "valueFrom": "arn:aws:secretsmanager:..."}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/cno-ivr",
        "awslogs-region": "us-east-1",
        "awslogs-stream-prefix": "ecs"
      }
    }
  }],
  "executionRoleArn": "arn:aws:iam::...:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::...:role/cnoIvrTaskRole"
}
```

### Hour 5-6: ECS Service Configuration
**What to learn:**
- Desired count = how many tasks to run (start with 2)
- Deployment configuration:
  - minimumHealthyPercent = 50 (keep at least 1 task during deploy)
  - maximumPercent = 200 (can temporarily run double)
- Load balancer integration — ALB target group
- **Deregistration delay** = 300s (critical for WebSocket — lets active calls finish)

**Why Fargate over Lambda for IVR:**
- Lambda max timeout = 15 minutes, calls can be longer
- Lambda has no WebSocket support (API Gateway WebSocket is request/response, not audio streaming)
- Lambda cold starts would kill real-time audio latency

### Hour 7-8: Hands-on (Free Tier)
- Push a simple Docker image to ECR (your Dockerfile already exists)
- Create an ECS cluster (Fargate)
- Create a task definition
- Run a standalone task (not a service yet)
- Check CloudWatch Logs for output
- Stop and clean up

**Interview answer:**
> "I'd use ECS Fargate because I don't want to manage EC2 instances. Each task runs 2 vCPU / 4 GB — a single call holds a WebSocket to Twilio plus outbound streams to Deepgram and ElevenLabs, so it's CPU-bound. I'd run a service with desired count 2 across 2 AZs, with a 300-second deregistration delay so active calls can finish during deployments. Lambda isn't viable because WebSocket connections need to persist for the entire call duration."

---

## DAY 3 — ALB + WebSocket + SSL
**Why**: ALB is the entry point. Twilio hits your ALB, which routes to ECS. WebSocket support is the critical piece.

### Hour 1-2: ALB Concepts
**What to learn:**
- ALB operates at Layer 7 (HTTP/HTTPS) — understands HTTP headers, paths, WebSocket upgrade
- **Listener** — port 443 (HTTPS), with SSL cert from ACM
- **Target Group** — group of ECS tasks that receive traffic
  - Health check: GET /health, interval 30s, healthy threshold 2
  - **Stickiness**: enabled — once a call connects to a task, it stays there
- **Routing rules** — path-based:
  - /stream → WebSocket target group (your media stream)
  - /voice/* → HTTP target group (Twilio webhooks)
  - /dashboard → HTTP target group
  - /health → HTTP target group

### Hour 3-4: WebSocket on ALB
**This is the most important interview topic for your IVR:**
- ALB supports WebSocket natively via HTTP/1.1 Upgrade header
- When Twilio sends `Connection: Upgrade`, ALB passes it through to ECS
- Connection becomes persistent bidirectional — audio flows both ways
- **Idle timeout** — ALB default is 60s. A silent pause > 60s kills the connection.
  - Set to **3600s** (1 hour) for phone calls
- **Sticky sessions** — ALB must route all requests from same Twilio call to same ECS task
  - Use application-based cookie or duration-based (for WebSocket, the connection is inherently sticky once established)

**Key insight for interviews:**
> "WebSocket connections are inherently sticky — once the upgrade handshake completes, the TCP connection is pinned to one target. The risk is during ALB target deregistration — if we remove a target, existing WebSocket connections are forcefully closed after the deregistration delay."

### Hour 5-6: Route 53 + ACM (Domain + SSL)
**What to learn:**
- **ACM (AWS Certificate Manager)** — free SSL certificates
  - Request cert for ivr.yourdomain.com
  - Validate via DNS (add CNAME to Route 53)
  - Attach to ALB listener
- **Route 53** — DNS service
  - Create A record (alias) pointing to ALB
  - This replaces ngrok entirely

**After setup:**
- Twilio webhook URL: `https://ivr.yourdomain.com/voice/incoming`
- Twilio stream URL: `wss://ivr.yourdomain.com/stream`

### Hour 7-8: Practice
- Draw the full request flow:
```
Twilio → DNS (Route 53) → ALB (port 443, TLS termination)
  → if /stream: WebSocket upgrade → ECS task (port 8000)
  → if /voice/*: HTTP POST → ECS task (port 8000)
```
- Write down ALB configuration values:
  - Idle timeout: 3600s
  - Deregistration delay: 300s
  - Health check: /health, 30s interval
  - Stickiness: enabled
  - Listener: 443 HTTPS with ACM cert

**Interview answer:**
> "ALB terminates TLS and handles WebSocket upgrade natively. When Twilio initiates a media stream, it sends an HTTP upgrade request. ALB passes this through to an ECS task, and the connection becomes a persistent bidirectional WebSocket for the duration of the call. I'd set the idle timeout to 3600 seconds since phone calls can have long pauses. Route 53 provides DNS, and ACM gives us a free SSL cert — this completely replaces ngrok."

---

## DAY 4 — RDS PostgreSQL
**Why**: Your conversation history and call records persist here.

### Hour 1-2: RDS Basics
**What to learn:**
- **RDS** = managed PostgreSQL (AWS handles patching, backups, replication)
- **Instance classes**: db.t3.micro (free tier), db.r6g.large (production)
- **Storage**: gp3 (general purpose SSD), io2 (high IOPS)
- **Multi-AZ**: primary in AZ-a, synchronous standby in AZ-b
  - Automatic failover if primary dies (~60s)
  - You connect to a single endpoint — RDS handles routing to active primary

### Hour 3-4: RDS Proxy
**Why it matters for ECS:**
- PostgreSQL has a hard limit on connections (~100-200 default)
- Each ECS task opens a connection pool (e.g., 10 connections)
- If you scale from 2 → 6 tasks, that's 60 connections
- During deployments, old + new tasks both connect = double connections
- **RDS Proxy** sits between ECS and RDS:
  - Multiplexes hundreds of app connections into a small pool to RDS
  - Handles failover transparently
  - Supports IAM authentication (no password in config)

```
ECS Task 1 ──┐
ECS Task 2 ──┼──→ RDS Proxy (connection pooling) ──→ RDS PostgreSQL
ECS Task 3 ──┘         50 app connections → 10 DB connections
```

### Hour 5-6: Backups, Security, pgvector
**What to learn:**
- Automated backups: daily snapshot, 7-day retention
- Point-in-time recovery: restore to any second within retention window
- Encryption at rest (KMS) and in transit (SSL)
- **pgvector extension**: your FAQ embeddings — RDS PostgreSQL supports it natively
- Security: RDS in private subnet, SG allows only ECS tasks on port 5432

### Hour 7-8: Practice
- Map your current local PostgreSQL to RDS:
```
Local                          →  AWS
───────────────────────────────────────
docker postgres:16             →  RDS PostgreSQL 16
localhost:5432                 →  cno-ivr.cluster-xxx.us-east-1.rds.amazonaws.com
POSTGRES_PASSWORD in .env      →  Secrets Manager or IAM auth via RDS Proxy
pgvector extension             →  Supported natively on RDS
conversation_store writes      →  Same code, different connection string
```

**Interview answer:**
> "RDS PostgreSQL 16 with Multi-AZ for automatic failover. I'd use RDS Proxy in front because ECS auto-scaling causes connection spikes — during a rolling deployment, both old and new tasks connect simultaneously. RDS Proxy multiplexes these into a small pool. pgvector is supported natively for our FAQ embeddings. The DB sits in a private subnet, only accessible from ECS tasks via security group rules."

---

## DAY 5 — ElastiCache Redis + Secrets Manager
**Why**: Redis holds session state (call_sid → LangGraph state). Secrets Manager holds all your API keys.

### Hour 1-3: ElastiCache Redis
**What to learn:**
- **ElastiCache** = managed Redis (patching, failover, backups handled by AWS)
- **Cluster mode disabled** (simpler): 1 primary + 1 replica
  - Primary handles reads/writes
  - Replica in different AZ for failover
  - Automatic failover promotes replica if primary dies
- **Node types**: cache.t3.micro (free tier), cache.r6g.medium (production)
- **Your use case**: session state per active call
  - Key: `session:{call_sid}` → JSON blob of LangGraph state
  - TTL: 3600s (1 hour — calls don't last longer)
  - At 100 concurrent calls, ~100 keys × ~5KB each = 500KB — tiny
- **Security**: private subnet, SG allows only ECS, encryption in-transit (TLS)

**Connection change in code:**
```python
# Local
REDIS_URL=redis://localhost:6379

# AWS
REDIS_URL=rediss://cno-ivr-redis.xxx.cache.amazonaws.com:6379
# Note: rediss:// (with double s) = TLS
```

### Hour 4-6: Secrets Manager + SSM Parameter Store
**What to learn:**
- **Secrets Manager** — for sensitive values (API keys, DB passwords)
  - Automatic rotation support
  - $0.40/secret/month
  - ECS pulls secrets at task startup (declared in task definition)
- **SSM Parameter Store** — for non-sensitive config (model names, feature flags)
  - Free for standard parameters
  - String, StringList, SecureString types

**Your secrets mapped:**
```
Secrets Manager:
  /cno-ivr/groq-api-key
  /cno-ivr/deepgram-api-key
  /cno-ivr/elevenlabs-api-key
  /cno-ivr/openai-api-key
  /cno-ivr/twilio-account-sid
  /cno-ivr/twilio-auth-token
  /cno-ivr/db-password
  /cno-ivr/ws-auth-token

SSM Parameter Store:
  /cno-ivr/auth-mode = "realtime"
  /cno-ivr/groq-model = "llama-3.3-70b-versatile"
  /cno-ivr/deepgram-model = "nova-2"
  /cno-ivr/elevenlabs-voice-id = "21m00Tcm4TlvDq8ikWAM"
```

**In ECS task definition:**
```json
"secrets": [
  {
    "name": "GROQ_API_KEY",
    "valueFrom": "arn:aws:secretsmanager:us-east-1:123456:secret:/cno-ivr/groq-api-key"
  }
],
"environment": [
  {
    "name": "AUTH_MODE",
    "value": "realtime"
  }
]
```

### Hour 7-8: IAM Roles for ECS
**What to learn:**
- **Execution Role** — lets ECS pull images from ECR and read secrets from Secrets Manager
- **Task Role** — lets your running app access AWS services (e.g., S3, CloudWatch)
- These replace hardcoded AWS credentials entirely

**Two roles:**
```
ecsTaskExecutionRole:
  - ecr:GetAuthorizationToken, ecr:BatchGetImage (pull Docker image)
  - secretsmanager:GetSecretValue (read API keys at startup)
  - logs:CreateLogStream, logs:PutLogEvents (send logs)

cnoIvrTaskRole:
  - s3:PutObject (if storing call recordings)
  - cloudwatch:PutMetricData (custom metrics)
```

**Interview answer:**
> "ElastiCache Redis for session state — each active call stores its LangGraph state keyed by call_sid with a 1-hour TTL. At 100 concurrent calls that's trivial memory. API keys are stored in Secrets Manager and injected into ECS tasks at startup via the task definition — no .env files in the container. The execution role allows pulling secrets and images, while the task role grants only the specific permissions the app needs at runtime."

---

## DAY 6 — CloudWatch Observability
**Why**: You need to monitor 3k calls/day and catch issues before callers notice.

### Hour 1-3: CloudWatch Logs
**What to learn:**
- **Log Groups** — `/ecs/cno-ivr` — all container stdout/stderr goes here
- **Log Streams** — one per ECS task (auto-created)
- Your app uses **structlog** (JSON) — CloudWatch parses JSON automatically
- **Log Insights** — SQL-like queries on logs:
```sql
-- Find all failed auth attempts in last hour
fields @timestamp, call_sid, @message
| filter @message like /auth_failed/
| sort @timestamp desc
| limit 50

-- Average graph invocation time
fields @timestamp, @message
| filter @message like /graph_invoke/
| stats avg(duration_ms) by bin(5m)
```
- **Retention**: set to 30 days (default is forever = expensive)

### Hour 4-6: CloudWatch Metrics & Alarms
**What to learn:**
- **Default metrics** (free): ECS CPU%, memory%, ALB request count, RDS connections
- **Custom metrics** (your app publishes):
  - `active_calls` — current concurrent WebSocket connections
  - `call_duration_avg` — from your conversation_store
  - `auth_success_rate` — % of calls that pass auth
  - `stt_latency_p99` — Deepgram response time
  - `tts_latency_p99` — ElevenLabs response time
  - `graph_invoke_p99` — LangGraph processing time

**Alarms to set:**
```
CRITICAL:
  - ECS CPU > 80% for 5 min → scale up + alert
  - active_calls > 90 → approaching capacity
  - ALB 5xx error rate > 5% → something broken
  - RDS connections > 80% of max → connection leak

WARNING:
  - auth_success_rate < 70% → auth flow broken
  - graph_invoke_p99 > 3s → Groq slow
  - ECS task count < 2 → HA compromised
```

### Hour 7-8: CloudWatch Dashboard
**Build a dashboard with these widgets:**
- Active calls (real-time number)
- Calls per hour (bar chart)
- Auth success rate (%)
- P99 latencies: STT, LLM, TTS (line chart)
- ECS CPU/Memory per task
- RDS connections
- Error rate

**Interview answer:**
> "Structured JSON logging via structlog goes to CloudWatch Logs with 30-day retention. I'd use Log Insights for ad-hoc debugging — filtering by call_sid to trace a single call's journey. Custom metrics track active calls, auth success rate, and P99 latencies for STT, LLM, and TTS independently. Alarms trigger on CPU above 80%, error rate above 5%, and auth success rate dropping below 70%. The dashboard gives real-time visibility into call volume and system health."

---

## DAY 7 — CI/CD Pipeline
**Why**: You need zero-downtime deployments that don't drop active calls.

### Hour 1-3: ECR (Elastic Container Registry)
**What to learn:**
- Private Docker registry in AWS
- Push image: `docker push 123456.dkr.ecr.us-east-1.amazonaws.com/cno-ivr:sha-abc123`
- Image scanning — scans for CVEs on push
- Lifecycle policies — auto-delete images older than 30 days (save storage cost)

**Your current flow uses GHCR — change to:**
```
GHCR (ghcr.io/your-repo)  →  ECR (123456.dkr.ecr.us-east-1.amazonaws.com/cno-ivr)
```

### Hour 4-6: GitHub Actions → ECR → ECS Deploy
**Updated pipeline:**
```yaml
# .github/workflows/deploy.yml
name: Deploy to AWS

on:
  push:
    branches: [master]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # 1. Authenticate to AWS
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::...:role/github-actions-deploy
          aws-region: us-east-1

      # 2. Login to ECR
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr

      # 3. Build and push image
      - run: |
          docker build -t ${{ steps.ecr.outputs.registry }}/cno-ivr:${{ github.sha }} .
          docker push ${{ steps.ecr.outputs.registry }}/cno-ivr:${{ github.sha }}

      # 4. Update ECS task definition with new image
      - uses: aws-actions/amazon-ecs-render-task-definition@v1
        id: render
        with:
          task-definition: task-definition.json
          container-name: ivr-app
          image: ${{ steps.ecr.outputs.registry }}/cno-ivr:${{ github.sha }}

      # 5. Deploy to ECS (rolling update)
      - uses: aws-actions/amazon-ecs-deploy-task-definition@v1
        with:
          task-definition: ${{ steps.render.outputs.task-definition }}
          service: cno-ivr-service
          cluster: cno-ivr-cluster
          wait-for-service-stability: true  # waits until new tasks healthy
```

### Hour 7-8: Rolling Deployment Mechanics
**How ECS rolling deploy works (critical for WebSocket):**
```
1. New task definition registered with new image tag
2. ECS launches new task (new version)
3. ALB health check passes on new task
4. ECS starts draining old task:
   - ALB stops sending NEW connections to old task
   - Existing WebSocket connections stay open
   - After deregistration delay (300s), force-close remaining connections
5. Old task stopped
6. Repeat for next task
```

**Key settings:**
- `minimumHealthyPercent: 50` — can take down 1 of 2 tasks
- `maximumPercent: 200` — can temporarily run 4 tasks (during deploy)
- `deregistrationDelay: 300` — 5 min for active calls to finish

**Interview answer:**
> "GitHub Actions builds the Docker image, pushes to ECR, then updates the ECS service with the new task definition. ECS does a rolling deployment — it launches a new task, waits for ALB health checks to pass, then drains the old task. The 300-second deregistration delay is critical because active WebSocket connections for ongoing phone calls need time to complete naturally. The deploy waits for service stability before marking success."

---

## DAY 8 — Auto-Scaling + HA + Graceful Shutdown
**Why**: 100+ concurrent calls means you need to scale, and scaling WebSocket apps has unique challenges.

### Hour 1-3: ECS Auto-Scaling
**What to learn:**
- **Application Auto Scaling** for ECS services
- **Scaling policies:**
  - Target tracking: "keep average CPU at 60%"
  - Step scaling: "if CPU > 70%, add 1 task; if CPU > 85%, add 2 tasks"
- **Scaling triggers for IVR:**
  - Primary: CPU utilization (each call = STT + LLM + TTS = CPU intensive)
  - Secondary: custom metric `active_calls_per_task` (from CloudWatch)
- **Scale-in protection**: don't kill tasks with active calls
  - ECS supports scale-in protection per task
  - Task marks itself protected when handling a call, unprotected when idle

**Configuration:**
```
Min tasks: 2 (always HA across 2 AZs)
Max tasks: 6 (handles 300 concurrent burst)
Scale-out: CPU > 60% for 2 min → add 1 task
Scale-in: CPU < 30% for 10 min → remove 1 task (respecting min)
Cooldown: 120s scale-out, 300s scale-in
```

### Hour 4-5: High Availability Design
**What to learn:**
- **Multi-AZ everything:**
  - ECS tasks spread across 2 AZs (ALB routes to both)
  - RDS Multi-AZ (automatic failover)
  - ElastiCache replica in second AZ
- **What happens when an AZ goes down:**
  - ALB stops routing to that AZ
  - ECS launches replacement tasks in healthy AZ
  - RDS fails over to standby (~60s)
  - Active calls in failed AZ are lost (acceptable — callers redial)
- **RTO (Recovery Time Objective)**: ~2 minutes
- **RPO (Recovery Point Objective)**: 0 (synchronous replication for RDS)

### Hour 6-8: Graceful Shutdown (SIGTERM handling)
**This is the hardest part and most impressive in interviews:**

When ECS wants to stop a task (deploy, scale-in, AZ failure):
1. ECS sends SIGTERM to your container
2. You have `stopTimeout` seconds (default 30, set to 120) to clean up
3. After timeout, ECS sends SIGKILL

**What your app should do on SIGTERM:**
```python
import signal
import asyncio

active_calls: set[str] = set()
shutting_down = False

def handle_sigterm(signum, frame):
    global shutting_down
    shutting_down = True
    # Stop accepting new WebSocket connections
    # Wait for active_calls to drain (or timeout)

signal.signal(signal.SIGTERM, handle_sigterm)

# In your /stream endpoint:
async def media_stream(websocket):
    if shutting_down:
        await websocket.close(code=1001)  # Going Away
        return
    active_calls.add(call_sid)
    try:
        await handler.run()
    finally:
        active_calls.discard(call_sid)

# Health check returns unhealthy when draining:
@app.get("/health")
async def health():
    if shutting_down:
        return JSONResponse({"status": "draining"}, status_code=503)
    return {"status": "ok"}
```

**Interview answer:**
> "Auto-scaling based on CPU with a target of 60%. Min 2 tasks across 2 AZs for HA, max 6 for burst. The tricky part is scale-in — I'd use ECS scale-in protection: when a task is handling active calls, it marks itself as protected. On SIGTERM during deployments, the app stops accepting new connections, returns 503 on health checks so ALB drains it, and waits for active calls to complete. The stop timeout is set to 120 seconds. Between the ALB deregistration delay (300s) and the SIGTERM handler, active calls get enough time to finish naturally."

---

## DAY 9 — Cost Optimization + Security
**Why**: Interviewers always ask "how would you reduce cost?" and "how do you secure it?"

### Hour 1-3: Cost Optimization
**Strategies for IVR:**
1. **Fargate Spot** for non-critical services (MLflow dashboard) — 70% cheaper
2. **Scheduled scaling** — if calls are business-hours only (8am-8pm):
   - Night: min tasks = 0 or 1
   - Day: min tasks = 2
   - Saves ~50% on compute
3. **NAT Gateway alternatives** — NAT Gateway is $45/mo + data charges
   - For outbound API calls only, consider VPC endpoints or NAT instances
4. **RDS Reserved Instances** — 1-year commitment = 40% savings
5. **Log retention** — 30 days not forever
6. **ECR lifecycle** — auto-delete old images

**Cost breakdown you should know:**
```
Monthly estimate for 3k calls/day:
  ECS Fargate (2 tasks avg)     ~$140
  RDS PostgreSQL (r6g.large)    ~$300  (or ~$180 with reserved)
  ElastiCache (r6g.medium x2)   ~$130
  ALB                           ~$25
  NAT Gateway                   ~$45
  CloudWatch                    ~$20
  ECR + Secrets Manager         ~$10
  ─────────────────────────────────
  Total                         ~$670/mo
  With reserved + scheduled     ~$450/mo
```

### Hour 4-6: Security Best Practices
**What to learn:**
1. **No secrets in code or Docker image** — all from Secrets Manager
2. **IAM least privilege** — task role only has permissions it needs
3. **Private subnets** — app and DB not publicly accessible
4. **TLS everywhere:**
   - ALB → client (ACM cert)
   - ECS → RDS (require SSL)
   - ECS → Redis (in-transit encryption)
5. **RDS encryption at rest** — KMS managed key
6. **ECR image scanning** — scan for CVEs on push
7. **WAF (Web Application Firewall)** — optional, on ALB
   - Rate limiting: max 100 requests/min per IP
   - Block known bad IPs
8. **VPC Flow Logs** — audit network traffic

### Hour 7-8: Security Interview Scenarios
**Q: "How do you handle API key rotation?"**
> "Secrets Manager supports automatic rotation. When a secret rotates, ECS tasks pick up the new value on next deployment or restart. For zero-downtime rotation, the app can also fetch secrets at runtime using the SDK with caching."

**Q: "What if someone gets access to your container?"**
> "The task role limits blast radius — it can only access CloudWatch and S3, not other AWS services. The container runs in a private subnet with no inbound internet access. Database credentials are fetched from Secrets Manager, not baked into the image. VPC Flow Logs would show unusual outbound traffic."

---

## DAY 10 — Full Architecture Whiteboard + Mock Q&A
**Why**: Tie everything together. This is your interview day.

### Hour 1-3: Draw the Complete Architecture
**Practice drawing this from memory on paper/whiteboard:**

```
[Twilio] ──HTTPS/WSS──► [Route 53] ──► [ALB]
                                          │ port 443, TLS termination
                                          │ idle_timeout=3600s
                                          │ deregistration_delay=300s
                                    ┌─────┴──────┐
                                    ▼            ▼
                              [ECS Task 1]  [ECS Task 2]     ◄── Auto-scaling 2-6
                              (AZ-a)        (AZ-b)               CPU target 60%
                              2vCPU/4GB     2vCPU/4GB
                                    │            │
                    ┌───────────────┼────────────┼───────────────┐
                    ▼               ▼            ▼               ▼
              [RDS Proxy]    [ElastiCache]  [Secrets Mgr]  [CloudWatch]
                    │          Redis 7       API keys        Logs/Metrics
                    ▼          (private)     (IAM access)    Alarms
              [RDS PostgreSQL 16]
              Multi-AZ (private)

              External APIs (via NAT Gateway):
              ├── Groq        (LLM)
              ├── Deepgram    (STT)
              ├── ElevenLabs  (TTS)
              └── OpenAI      (Realtime auth + embeddings)
```

**For each component, explain:**
1. Why this service (not an alternative)
2. How it connects (security group, subnet, port)
3. What happens when it fails
4. How it scales

### Hour 4-6: End-to-End Call Flow (Narrate This)
**Practice explaining a single call's journey:**

> "A caller dials the Twilio number. Twilio sends an HTTP POST to our ALB at /voice/incoming. Our FastAPI webhook responds with TwiML containing a Stream directive pointing to wss://ivr.ourdomain.com/stream.
>
> Twilio opens a WebSocket connection through the ALB to one of our ECS tasks. The ALB upgrades the connection and pins it to that specific task.
>
> Since AUTH_MODE is realtime, the task opens a session to OpenAI's Realtime API. Twilio streams raw mulaw audio to our task, which forwards it to OpenAI. OpenAI handles STT, NLU, and TTS for the auth conversation — collecting phone number, policy number, and date of birth via function calls. It streams audio responses back through our task to Twilio.
>
> Once auth succeeds, we close the Realtime session, acquire an access token from the CNO API, start a Deepgram STT WebSocket, and switch to the standard pipeline. Now the caller's audio goes to Deepgram for transcription, the transcript goes through LangGraph on Groq for intent detection and response generation, and the response text goes to ElevenLabs for TTS. Audio streams back to Twilio.
>
> Every turn is logged to the conversation store which writes to RDS via RDS Proxy. Session state is cached in ElastiCache Redis. When the call ends, we log metrics to CloudWatch and clean up."

### Hour 7-8: Mock Interview Questions
**Practice answering these out loud:**

1. "Walk me through the architecture" (use the diagram above)
2. "Why ECS Fargate over Lambda?" (WebSocket persistence, no 15-min timeout)
3. "Why ALB over NLB?" (WebSocket upgrade needs Layer 7, NLB is Layer 4)
4. "How do you deploy without dropping calls?" (rolling deploy + deregistration delay + SIGTERM handler)
5. "How do you handle an AZ failure?" (Multi-AZ ECS + RDS failover, active calls in failed AZ are lost)
6. "How do you scale to 100 concurrent?" (auto-scaling on CPU, ~50 calls per task, min 2 max 6)
7. "What's your monitoring strategy?" (CloudWatch: logs, custom metrics, alarms, dashboard)
8. "How do you secure API keys?" (Secrets Manager, injected via task definition, IAM roles)
9. "How do you handle database connection spikes?" (RDS Proxy multiplexing)
10. "What's the cost?" ($450-670/mo depending on reserved instances)
11. "Why not use Bedrock instead of Groq?" (Could, but Groq gives faster inference for Llama. Bedrock is an option for keeping everything in AWS.)
12. "What's the single point of failure?" (NAT Gateway per AZ — if it dies, no outbound API calls. Mitigate with NAT Gateway per AZ.)
13. "How do you handle a bad deployment?" (ECS circuit breaker — if new tasks keep failing health checks, auto-rollback to previous task definition)
14. "Why Redis for session state instead of DynamoDB?" (Sub-millisecond reads for real-time audio pipeline. DynamoDB single-digit ms is fine but Redis is simpler for key-value with TTL, and we already use it locally.)

---

## After the 10 Days

You will be able to:
- Draw the architecture from memory in 5 minutes
- Explain every component choice with trade-offs
- Answer follow-up questions about scaling, security, cost, and failure scenarios
- Narrate the complete call flow end-to-end

You built the app. Now you understand the infra. That combination is rare and valuable.
