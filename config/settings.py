from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Environment
    environment: str = "dev"

    # OpenAI (used for embeddings and Realtime API only)
    openai_api_key: str
    openai_embedding_model: str = "text-embedding-3-small"

    # LLM provider toggle: "groq" (default) or "bedrock"
    llm_provider: str = "groq"
    aws_region: str = "us-east-1"
    bedrock_model: str = "amazon.nova-micro-v1:0"
    router_bedrock_model: str = "amazon.nova-micro-v1:0"

    # Groq (used for all LLM inference nodes)
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"              # FAQ, policy, loan, payment, beneficiary
    router_model: str = "qwen/qwen3.8-27b"            # router only — fast single-word classifier

    # Feature flags (tune at runtime via env vars, no deploy needed)
    enable_rag: bool = True                    # False → skip pgvector, use canned FAQ fallback
    faq_fallback_to_escalate: bool = False     # True → no-RAG-match routes to agent instead of canned msg
    max_auth_attempts: int = 3                 # max PII retries before escalation

    # Deepgram STT
    deepgram_api_key: str
    deepgram_model: str = "nova-2"
    deepgram_language: str = "en-US"
    deepgram_encoding: str = "mulaw"
    deepgram_sample_rate: int = 8000
    deepgram_channels: int = 1
    deepgram_endpointing: int = 300          # ms of silence to finalize utterance
    deepgram_utterance_end_ms: int = 1000    # finalize after 1s of no new words
    deepgram_smart_format: bool = True       # auto-format dates, numbers, currencies
    deepgram_interim_results: bool = True    # stream partial transcripts
    deepgram_punctuate: bool = True
    deepgram_no_delay: bool = True

    # ElevenLabs TTS
    elevenlabs_api_key: str
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # default: Rachel
    elevenlabs_model: str = "eleven_monolingual_v1"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_optimize_streaming_latency: int = 3       # 0-4; 3 = low latency for IVR

    # OpenAI TTS (WebSocket stream path)
    openai_tts_model: str = "tts-1"
    openai_tts_voice: str = "nova"

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str = ""
    twilio_agent_phone_number: str = ""  # live agent transfer
    twilio_api_key: str = ""             # for browser client token generation
    twilio_api_secret: str = ""
    twilio_twiml_app_sid: str = ""       # TwiML App SID pointing to /webhook/voice

    # Redis / PostgreSQL — 127.0.0.1, not localhost: on Windows localhost tries IPv6
    # first and Docker binds IPv4 only, adding ~2 s to every new connection (#61)
    redis_url: str = "redis://127.0.0.1:6379/0"

    # PostgreSQL
    database_url: str = "postgresql://insuranceCompany:cno_pass@127.0.0.1:5432/cno_ivr"

    # UIC Backend APIs
    cno_api_base_url: str = "https://api.uic.example.com"
    cno_api_key: str = ""
    cno_jwt_secret: str = ""

    # LangSmith observability (auto-instruments LangChain/LangGraph — zero code changes)
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "uic-ivr-dev"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # Auth mode — "standard" (Deepgram STT + LangGraph) or "realtime" (OpenAI Realtime API)
    auth_mode: str = "standard"

    # Dashboard / browser-client auth (HTTP Basic)
    # Leave dashboard_password empty to disable auth (dev only).
    # In prod: set DASHBOARD_USERNAME and DASHBOARD_PASSWORD in .env.
    dashboard_username: str = "admin"
    dashboard_password: str = ""

    # Twilio webhook signature validation
    # Set VALIDATE_TWILIO_SIGNATURE=true and TWILIO_BASE_URL=https://your-host.ngrok.io in prod.
    validate_twilio_signature: bool = False
    twilio_base_url: str = ""

    # Auto-sync Twilio webhooks on startup
    # When true, updates TwiML app + phone number webhooks to TWILIO_BASE_URL automatically.
    # Local dev: TWILIO_BASE_URL is auto-detected from ngrok if empty.
    # AWS: set TWILIO_BASE_URL to your ALB/CloudFront URL.
    sync_twilio_webhooks: bool = True

    # WebSocket /stream auth token
    # Add ?token=<WS_AUTH_TOKEN> to the Media Stream URL in your TwiML.
    # Leave empty to skip validation (dev only).
    ws_auth_token: str = ""

    # Voice path (#83): "gather" (default, Twilio <Gather> + Polly) or "stream"
    # (Media Streams + Deepgram). Flip back to "gather" to put every new call back
    # on the Gather path; a stream that cannot start also falls back per call.
    voice_path: str = "gather"
    # Only used when VOICE_PATH=stream. Callers listed in STREAM_NUMBERS (comma
    # separated, e.g. "5551234567,client:browser_tester") always stream; every other
    # call streams with STREAM_CANARY_PERCENT probability (0-100, default 100).
    stream_numbers: str = ""
    stream_canary_percent: int = 100

    # CORS — comma-separated allowed origins; "*" for dev, restrict in prod
    allowed_origins: str = "*"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"

    # Cap concurrent LLM calls per process (80 parallel streams would otherwise stampede Groq)
    llm_max_inflight: int = 20

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() == "prod"

    @model_validator(mode="after")
    def _enforce_prod_security(self):
        """Refuse to boot in prod with open-by-default security controls."""
        if not self.is_prod:
            return self
        missing: list[str] = []
        if not self.dashboard_password:
            missing.append("DASHBOARD_PASSWORD")
        if not self.validate_twilio_signature:
            missing.append("VALIDATE_TWILIO_SIGNATURE=true")
        if not (self.twilio_base_url or "").lower().startswith("https://"):
            missing.append("TWILIO_BASE_URL (must be https://...)")
        if not self.ws_auth_token:
            missing.append("WS_AUTH_TOKEN")
        if self.allowed_origins.strip() == "*":
            missing.append("ALLOWED_ORIGINS (must not be *)")
        if not self.cno_jwt_secret or self.cno_jwt_secret == "dev-fallback-secret-do-not-use-in-prod":
            missing.append("CNO_JWT_SECRET")
        if missing:
            raise ValueError(
                "Refusing to start ENVIRONMENT=prod with open security controls. "
                "Set: " + ", ".join(missing)
            )
        return self


settings = Settings()
