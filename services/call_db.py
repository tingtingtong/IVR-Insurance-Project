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
import queue
import threading
import time
import structlog

log = structlog.get_logger()

_conn = None  # module-level sync psycopg2 connection
_last_attempt: float = 0.0  # epoch time of last connect attempt
_RETRY_INTERVAL = 30        # seconds to wait before retrying after a failure
_CONNECT_TIMEOUT = 3        # seconds for TCP connection attempt
_write_q: queue.Queue = queue.Queue(maxsize=2000)
_worker_started = False
_write_lock = threading.Lock()


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


def _ensure_worker() -> None:
    global _worker_started
    with _write_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_writer_loop, name="call-db-writer", daemon=True)
        t.start()
        _worker_started = True


def _writer_loop() -> None:
    while True:
        item = _write_q.get()
        if item is None:
            return
        call_sid, data = item
        _upsert_sync(call_sid, data)


def _upsert_sync(call_sid: str, data: dict) -> None:
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
        global _conn
        _conn = None


def upsert_call(call_sid: str, data: dict) -> None:
    """Enqueue a write. Never blocks the voice event loop; drops if the queue is full."""
    _ensure_worker()
    try:
        _write_q.put_nowait((call_sid, data))
    except queue.Full:
        log.warning("call_db_queue_full", call_sid=call_sid)


def ping() -> bool:
    """True if Postgres accepts a connection. Used by /health/ready."""
    conn = _get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        global _conn
        _conn = None
        return False


def list_calls(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Paginated call history for the dashboard. Returns (rows, total_count)."""
    conn = _get_conn()
    if conn is None:
        return [], 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ivr_call_records")
            total = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT data FROM ivr_call_records
                ORDER BY started_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = [row[0] for row in cur.fetchall()]
            return rows, total
    except Exception as e:
        log.warning("call_db_list_failed", error=str(e))
        return [], 0


def load_call(call_sid: str) -> dict:
    conn = _get_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM ivr_call_records WHERE call_sid = %s", (call_sid,))
            row = cur.fetchone()
            return row[0] if row else {}
    except Exception as e:
        log.warning("call_db_load_one_failed", call_sid=call_sid, error=str(e))
        return {}


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
