# Insurance IVR — LangGraph Voice Agent

An AI-powered Interactive Voice Response (IVR) system for insurance policy servicing, built with **LangGraph** for conversational state management and **FastAPI** for real-time WebSocket audio streaming.

## Architecture

```
Caller → Twilio → WebSocket → Deepgram STT → LangGraph → Groq LLM → ElevenLabs TTS → Twilio → Caller
```

### Core Stack
- **Orchestration**: LangGraph (stateful graph with persistent PostgreSQL checkpointing)
- **LLM Inference**: Groq (Llama 3.3 70B for intent nodes, Llama 3.1 8B for router)
- **Speech-to-Text**: Deepgram Nova-2 (real-time streaming with endpointing)
- **Text-to-Speech**: ElevenLabs (low-latency streaming, μ-law 8kHz for telephony)
- **Telephony**: Twilio Media Streams (bidirectional WebSocket audio)
- **Backend**: FastAPI + Uvicorn (async)
- **State**: PostgreSQL (checkpointer + call history) + Redis (session state)
- **RAG**: pgvector for FAQ knowledge base retrieval

## Features

- **Multi-step authentication** — Phone number → Policy number → Date of birth → Name verification with configurable retry limits
- **Intent routing** — LLM-based classifier routes to 10+ service nodes:
  - Policy information lookup
  - Payment processing (OTP-secured)
  - Loan inquiries
  - Beneficiary updates
  - Contact information changes
  - Document requests
  - Privacy/opt-out management
  - FAQ (RAG-powered)
  - Live agent escalation
- **Barge-in detection** — WebRTC VAD for caller interruption handling
- **Browser client** — Web-based softphone for testing without a real phone
- **Live dashboard** — Real-time call monitoring with SSE log streaming
- **Caller persona detection** — Identifies if caller is payor, insured, owner, or other
- **Graceful fallbacks** — PostgreSQL → in-memory checkpointer, RAG → canned FAQ responses

## Project Structure

```
├── main.py                    # FastAPI app, lifespan, router registration
├── config/
│   └── settings.py            # Pydantic settings (env-driven configuration)
├── core/
│   ├── graph/
│   │   ├── graph.py           # LangGraph definition — nodes, edges, routing
│   │   ├── state.py           # CNOState TypedDict (conversation state schema)
│   │   ├── auth_guard.py      # Authentication gate logic
│   │   └── nodes/             # Intent handler nodes
│   │       ├── router.py      # LLM intent classifier
│   │       ├── auth.py        # Multi-step PII authentication
│   │       ├── policy.py      # Policy info lookup
│   │       ├── payment.py     # Payment processing
│   │       ├── otp.py         # OTP / PCI-secure payment
│   │       ├── loan.py        # Loan inquiries
│   │       ├── beneficiary.py # Beneficiary management
│   │       ├── contact.py     # Contact info changes
│   │       ├── document.py    # Document requests
│   │       ├── privacy.py     # Privacy/opt-out
│   │       ├── faq.py         # RAG-powered FAQ
│   │       ├── escalation.py  # Live agent transfer
│   │       └── goodbye.py     # Call termination
│   ├── prompts/               # System prompts and templates
│   └── tools/                 # API integrations (auth token, party search, payments)
├── services/
│   ├── stt.py                 # Deepgram STT streaming client
│   ├── tts.py                 # ElevenLabs TTS streaming client
│   ├── vad.py                 # Voice Activity Detection (barge-in)
│   ├── rag.py                 # pgvector RAG retrieval
│   ├── session.py             # Redis session management
│   ├── call_db.py             # PostgreSQL call history
│   └── conversation_store.py  # In-memory call store for dashboard
├── webhooks/
│   ├── twilio_voice.py        # Twilio voice webhook (TwiML)
│   ├── twilio_stream.py       # Twilio Media Stream WebSocket handler
│   ├── browser_client.py      # Browser softphone endpoints
│   ├── chat.py                # Text chat endpoint (testing)
│   ├── dashboard.py           # Live monitoring dashboard
│   └── security.py            # Twilio signature validation
└── utils/
    └── tts_normalizer.py      # Text normalization for TTS output
```

## Setup

### Prerequisites
- Python 3.12+
- PostgreSQL with pgvector extension
- Redis
- Twilio account with Media Streams enabled
- API keys: Groq, Deepgram, ElevenLabs, OpenAI (embeddings)

### Installation

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

Expose via ngrok for Twilio webhooks:
```bash
ngrok http 8080
```

## AWS Deployment

Infrastructure is managed with Terraform (state in S3) and deployed via GitHub Actions.

### Prerequisites
- AWS CLI configured with appropriate credentials
- Terraform >= 1.5
- Docker Desktop running
- GitHub repo secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `TF_VAR_DB_PASSWORD`

### Spin Up Infrastructure
```bash
# Option 1: GitHub Actions (recommended)
# Go to Actions → Infrastructure → Run workflow → action: apply, environment: dev

# Option 2: Local
cd infra
terraform init
TF_VAR_db_password='your-password' terraform apply
```

### Build & Deploy
```bash
# Option 1: GitHub Actions — push to master triggers Deploy workflow

# Option 2: Local
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
docker build -t <account-id>.dkr.ecr.us-east-1.amazonaws.com/ivr-app:latest .
docker build -t <account-id>.dkr.ecr.us-east-1.amazonaws.com/ivr-app:mock-api -f Dockerfile.mock .
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/ivr-app:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/ivr-app:mock-api
aws ecs update-service --cluster ivr-cluster --service dev-ivr-svc --force-new-deployment
```

### Post-Deploy Checklist
1. Populate Secrets Manager (`dev/ivr/app-secrets`) with API keys if first deploy
2. Update Twilio phone number voice webhook to `http://<alb-dns>/twilio/voice`
3. Update TwiML App voice URL to `http://<alb-dns>/webhook/voice`
4. Enable CI/CD workflows: `gh workflow enable ci.yml && gh workflow enable deploy.yml`

### Tear Down
```bash
# GitHub Actions: Actions → Infrastructure → Run workflow → action: destroy, environment: dev
# Local: cd infra && TF_VAR_db_password='your-password' terraform destroy
```

## Browser Softphone (HTTP Troubleshooting)

The softphone at `/client` requires microphone access, which browsers only allow on **HTTPS** or **localhost** origins. When deployed behind an HTTP-only ALB:

1. **Recommended**: Call `+19087425347` from a real phone — bypasses browser restrictions entirely
2. **Chrome workaround**: Close all Chrome windows, then launch with:
   ```bash
   # Windows
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --unsafely-treat-insecure-origin-as-secure="http://<alb-dns>" --user-data-dir=C:\temp\chrome-dev

   # macOS / Linux
   google-chrome --unsafely-treat-insecure-origin-as-secure="http://<alb-dns>" --user-data-dir=/tmp/chrome-dev
   ```
3. **Permanent fix**: Add an ACM certificate + custom domain to the ALB for HTTPS

## License

MIT
