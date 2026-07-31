import sys
import asyncio
import pathlib

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Tee stdout/stderr → log_bus (powers /client/logs/stream SSE live logs) ────
# This runs whether the server is started via run.py or uvicorn main:app directly.
_LOG_PATH = pathlib.Path(__file__).parent / "ivr.log"

class _Tee:
    def __init__(self, stream, file):
        self._s = stream; self._f = file; self._buf = ""
    def write(self, msg):
        self._s.write(msg); self._f.write(msg); self._f.flush()
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                from log_bus import push; push(line)
            except Exception:
                pass
    def flush(self): self._s.flush()
    def fileno(self): return self._s.fileno()
    def isatty(self): return False

if not isinstance(sys.stdout, _Tee):
    _log_fh = open(str(_LOG_PATH), "a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, _log_fh)
    sys.stderr = _Tee(sys.__stderr__, _log_fh)

import structlog
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings

# Configure structured logging
_log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
logging.basicConfig(level=_log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(_log_level)
)

log = structlog.get_logger()

# Async PG connection pool — held open for the lifetime of the server
_async_pg_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: compile graph with async checkpointer. Shutdown: close pool."""
    global _async_pg_pool
    from core.graph.graph import build_graph, set_graph
    from langgraph.checkpoint.memory import MemorySaver

    log.info("cno_ivr_started", environment=settings.environment, port=settings.app_port)

    checkpointer = None
    try:
        import socket
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from urllib.parse import urlparse

        db_url = settings.database_url
        if "+" in db_url.split("://")[0]:
            db_url = "postgresql://" + db_url.split("://", 1)[1]

        # Fast port-check before attempting full connect
        parsed = urlparse(db_url)
        pg_host = parsed.hostname or "localhost"
        pg_port = parsed.port or 5432
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        reachable = sock.connect_ex((pg_host, pg_port)) == 0
        sock.close()
        if not reachable:
            raise ConnectionRefusedError(f"PostgreSQL not reachable at {pg_host}:{pg_port}")

        # AsyncPostgresSaver with a single async psycopg connection
        import psycopg as _psycopg
        _async_pg_pool = await _psycopg.AsyncConnection.connect(
            db_url, autocommit=True, connect_timeout=5
        )
        checkpointer = AsyncPostgresSaver(_async_pg_pool)
        await checkpointer.setup()
        log.info("checkpointer_postgres_ready", db=f"{pg_host}:{pg_port}")
    except Exception as pg_err:
        log.warning("checkpointer_fallback_to_memory", error=str(pg_err))
        checkpointer = MemorySaver()
        if _async_pg_pool:
            try:
                await _async_pg_pool.close()
            except Exception:
                pass
        _async_pg_pool = None

    compiled = build_graph().compile(checkpointer=checkpointer)
    set_graph(compiled)
    log.info("graph_compiled", checkpointer=type(checkpointer).__name__)

    # Load call history from PostgreSQL so the dashboard is populated after restart
    from services.conversation_store import init_from_db
    init_from_db()
    log.info("conversation_store_loaded_from_db")

    yield  # server runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _async_pg_pool is not None:
        try:
            await _async_pg_pool.close()
            log.info("checkpointer_postgres_closed")
        except Exception:
            pass
    log.info("cno_ivr_shutdown")


app = FastAPI(
    title="insuranceCompany IVR — LangGraph + Hybrid STT/TTS",
    version="1.0.0",
    description="insuranceCompany IVR powered by LangGraph, Deepgram, and ElevenLabs",
    lifespan=lifespan,
)

_origins_restricted = settings.allowed_origins.strip() != "*"
_allowed_origins = (
    [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    if _origins_restricted
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_origins_restricted,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Twilio-Signature"],
)

from webhooks.twilio_voice import router as voice_router
from webhooks.twilio_stream import router as stream_router
from webhooks.browser_client import router as browser_router
from webhooks.chat import router as chat_router
from webhooks.dashboard import router as dashboard_router

# Routers
app.include_router(voice_router)
app.include_router(stream_router)
app.include_router(browser_router)
app.include_router(chat_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}
