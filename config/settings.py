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
    bedrock_model: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    router_bedrock_model: str = "anthropic.claude-3-5-haiku-20241022-v1:0"

    # Groq (used for all LLM inference nodes)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"      # FAQ, policy, loan, payment, beneficiary
    router_model: str = "llama-3.1-8b-instant"        # router only — fast single-word classifier

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

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL
    database_url: str = "postgresql://insuranceCompany:cno_pass@localhost:5432/cno_ivr"

    # insuranceCompany Backend APIs
    cno_api_base_url: str = "https://api.insuranceCompany.example.com"
    cno_api_key: str = ""
    cno_jwt_secret: str = ""

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

    # WebSocket /stream auth token
    # Add ?token=<WS_AUTH_TOKEN> to the Media Stream URL in your TwiML.
    # Leave empty to skip validation (dev only).
    ws_auth_token: str = ""

    # CORS — comma-separated allowed origins; "*" for dev, restrict in prod
    allowed_origins: str = "*"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


settings = Settings()
