"""
In-memory log broadcaster for real-time SSE log streaming.

run.py's _Tee pushes every line here.
browser_client.py's /logs/stream SSE endpoint subscribes and pushes to browser.
"""
import asyncio
from collections import deque

_buffer: deque[str] = deque(maxlen=500)
_queues: list[asyncio.Queue] = []


def push(line: str) -> None:
    """Called from _Tee (sync context). Safe to call from any thread."""
    line = line.rstrip("\r\n")
    if not line.strip():
        return
    _buffer.append(line)
    for q in list(_queues):
        try:
            q.put_nowait(line)
        except Exception:
            pass


def get_history() -> list[str]:
    return list(_buffer)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=300)
    _queues.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _queues.remove(q)
    except ValueError:
        pass
