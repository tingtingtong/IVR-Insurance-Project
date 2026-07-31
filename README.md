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

## License

MIT
