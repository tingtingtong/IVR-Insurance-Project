"""
PostgreSQL persistence for IVR call records.

Provides write-through storage for conversation_store so call history survives
server restarts.  All operations are fire-and-forget — a DB failure never
crashes the IVR; it just means that call is not persisted.

Schema (auto-created on first use):
  ivr_call_records(call_sid TEXT PK, data JSONB, started_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)

The full call dict (turns, events, metadata) is stored as JSONB so no schema
migration is needed when new fields are added.
"""
import json
import time
import structlog

log = structlog.get_logger()

_conn = None  # module-level sync psycopg2 connection
_last_attempt: float = 0.0  # epoch time of last connect attempt
_RETRY_INTERVAL = 30        # seconds to wait before retrying after a failure
_CONNECT_TIMEOUT = 3        # seconds for TCP connection attempt


def _get_conn():
    """Return a live psycopg2 connection, reconnecting if needed.

    When Postgres is unavailable, retries at most once every _RETRY_INTERVAL
    seconds so failing DB attempts never block the IVR webhook response path.
    """
    global _conn, _last_attempt

    # Fast-path: skip reconnect attempt if we recently failed
    if _conn is None and time.time() - _last_attempt < _RETRY_INTERVAL:
        return None

    try:
        import psycopg2
        from config import settings

        if _conn is None or _conn.closed:
            _last_attempt = time.time()
            _conn = psycopg2.connect(
                settings.database_url,
                connect_timeout=_CONNECT_TIMEOUT,
            )
            _conn.autocommit = True
            _ensure_table(_conn)
    except Exception as e:
        log.warning("call_db_connect_failed", error=str(e))
        _conn = None
    return _conn


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ivr_call_records (
                call_sid   TEXT PRIMARY KEY,
                data       JSONB NOT NULL,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ivr_call_started
            ON ivr_call_records (started_at DESC)
        """)


def upsert_call(call_sid: str, data: dict) -> None:
    """Persist (insert or update) a call record.  Silently no-ops if DB unavailable."""
    conn = _get_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ivr_call_records (call_sid, data, started_at, updated_at)
                VALUES (%s, %s::jsonb, NOW(), NOW())
                ON CONFLICT (call_sid) DO UPDATE
                    SET data = EXCLUDED.data,
                        updated_at = NOW()
                """,
                (call_sid, json.dumps(data, default=str)),
            )
    except Exception as e:
        log.warning("call_db_upsert_failed", call_sid=call_sid, error=str(e))
        # Reset connection so next call gets a fresh one
        global _conn
        _conn = None


def load_recent_calls(limit: int = 100) -> list[dict]:
    """Load the most recent `limit` call records from PostgreSQL.

    Returns an empty list if the DB is unavailable — the in-memory store
    starts empty in that case, which is the existing behaviour.
    """
    conn = _get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data FROM ivr_call_records
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [row[0] for row in rows]
    except Exception as e:
        log.warning("call_db_load_failed", error=str(e))
        return []
