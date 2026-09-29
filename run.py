"""
Launcher: sets WindowsSelectorEventLoopPolicy BEFORE uvicorn creates the event loop.
Required on Windows so Deepgram's WebSocket (which uses extra_headers) works correctly.
Also tees all stdout/stderr to ivr.log so the browser log viewer always has fresh data.
"""
import sys
import pathlib
import asyncio

# ── tee stdout+stderr → ivr.log ───────────────────────────────────────────────
_LOG_PATH = pathlib.Path(__file__).parent / "ivr.log"

class _Tee:
    """Write to both a stream and the in-memory log bus (+ optional file)."""
    def __init__(self, stream, file):
        self._s = stream
        self._f = file
        self._buf = ""

    def write(self, msg):
        self._s.write(msg)
        self._f.write(msg)
        self._f.flush()
        # Buffer until we have complete lines, then push each to the bus
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            try:
                from log_bus import push
                push(line)
            except Exception:
                pass

    def flush(self):
        self._s.flush()

    def fileno(self):
        return self._s.fileno()

    def isatty(self):
        return False

_log_fh = open(str(_LOG_PATH), "a", encoding="utf-8", buffering=1)
sys.stdout = _Tee(sys.__stdout__, _log_fh)
sys.stderr = _Tee(sys.__stderr__, _log_fh)

# ─────────────────────────────────────────────────────────────────────────────

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
from dotenv import load_dotenv
load_dotenv()

import uvicorn

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8082"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    # uvicorn's default Windows loop is ProactorEventLoop, which psycopg async
    # (AsyncPostgresSaver) cannot use. Force SelectorEventLoop on Windows.
    if sys.platform == "win32":
        config = uvicorn.Config(
            "main:app",
            host=host,
            port=port,
            log_level=log_level,
        )
        config.get_loop_factory = lambda: asyncio.SelectorEventLoop
        uvicorn.Server(config).run()
    else:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            log_level=log_level,
        )
