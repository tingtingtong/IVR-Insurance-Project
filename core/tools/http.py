"""Shared aiohttp session with connection pooling and retry on 429/503/timeout."""
from __future__ import annotations

import asyncio
import aiohttp
import structlog

log = structlog.get_logger()

_session: aiohttp.ClientSession | None = None
_DEFAULT_TIMEOUT = 5
_RETRYABLE = {429, 503}


async def init_http() -> None:
    global _session
    if _session is not None and not _session.closed:
        return
    _session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=100, limit_per_host=20, keepalive_timeout=30),
        timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT),
    )


async def close_http() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def _session_or_init() -> aiohttp.ClientSession:
    if _session is None or _session.closed:
        await init_http()
    assert _session is not None
    return _session


async def post_json(
    url: str,
    *,
    json: dict | None = None,
    headers: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    retries: int = 2,
) -> tuple[int, dict]:
    """POST JSON. Retry 429/503/timeout twice (0.5s, 1s). Do not retry 4xx.

    Returns (status, body_dict). Network failure after retries returns (0, {error}).
    """
    last_error = ""
    for attempt in range(retries + 1):
        try:
            sess = await _session_or_init()
            async with sess.post(
                url,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status in _RETRYABLE and attempt < retries:
                    delay = 0.5 * (2 ** attempt)
                    log.warning("http_retry", url=url, status=resp.status, attempt=attempt + 1, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                try:
                    body = await resp.json()
                    if not isinstance(body, dict):
                        body = {"data": body}
                except Exception:
                    body = {"error": (await resp.text())[:200]}
                return resp.status, body
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                delay = 0.5 * (2 ** attempt)
                log.warning("http_retry", url=url, error=last_error[:80], attempt=attempt + 1, delay=delay)
                await asyncio.sleep(delay)
                continue
            return 0, {"error": last_error}
    return 0, {"error": last_error or "request failed"}


async def probe(url: str, *, timeout: float = 2) -> bool:
    """True if the host answers with any non-5xx HTTP status. No retries; never raises.

    Network failures/timeouts -> False. Unexpected internal errors -> True (fail open;
    the real lookups still escalate on a genuine outage).
    """
    try:
        sess = await _session_or_init()
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return resp.status < 500
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        log.warning("api_probe_failed", url=url, error=str(exc)[:80])
        return False
    except Exception as exc:  # not a network verdict (e.g. stale session) — fail open
        log.warning("api_probe_error", url=url, error=str(exc)[:80])
        return True
