"""#61: SessionService must not reconnect to Redis on every voice turn."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.session as session_mod
from services.session import SessionService


def _reset():
    SessionService._clients.clear()
    SessionService._use_fake = False


def _client(ping_ok=True):
    c = MagicMock()
    c.ping = AsyncMock(return_value=True) if ping_ok else AsyncMock(side_effect=ConnectionError("refused"))
    return c


class SessionRedisTests(unittest.TestCase):
    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def test_client_is_shared_across_instances(self):
        from_url = MagicMock(return_value=_client())

        async def run():
            a = await SessionService()._get_redis()
            b = await SessionService()._get_redis()
            return a, b

        with patch.object(session_mod.aioredis, "from_url", from_url):
            a, b = asyncio.run(run())
        self.assertIs(a, b)
        self.assertEqual(from_url.call_count, 1, "reconnected on a later turn")

    def test_connect_timeout_is_set(self):
        from_url = MagicMock(return_value=_client())
        with patch.object(session_mod.aioredis, "from_url", from_url):
            asyncio.run(SessionService()._get_redis())
        self.assertIn("socket_connect_timeout", from_url.call_args.kwargs)

    def test_fakeredis_fallback_is_remembered_in_dev(self):
        from_url = MagicMock(return_value=_client(ping_ok=False))

        async def run():
            return [await SessionService()._get_redis() for _ in range(3)]

        with patch.object(session_mod.aioredis, "from_url", from_url), \
             patch.object(type(session_mod.settings), "is_prod", property(lambda self: False)):
            clients = asyncio.run(run())
        self.assertEqual(from_url.call_count, 1, "paid the Redis timeout again after fallback")
        self.assertTrue(all(c is clients[0] for c in clients))

    def test_prod_fails_closed_and_does_not_cache_fallback(self):
        from_url = MagicMock(return_value=_client(ping_ok=False))
        with patch.object(session_mod.aioredis, "from_url", from_url), \
             patch.object(type(session_mod.settings), "is_prod", property(lambda self: True)):
            for _ in range(2):
                with self.assertRaises(ConnectionError):
                    asyncio.run(SessionService()._get_redis())
        self.assertFalse(SessionService._use_fake)
        self.assertEqual(from_url.call_count, 2)

    def test_separate_event_loops_get_separate_clients(self):
        from_url = MagicMock(side_effect=lambda *a, **k: _client())
        with patch.object(session_mod.aioredis, "from_url", from_url):
            a = asyncio.run(SessionService()._get_redis())
            b = asyncio.run(SessionService()._get_redis())
        self.assertIsNot(a, b)

    def test_defaults_avoid_localhost(self):
        src = (ROOT / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('redis_url: str = "redis://127.0.0.1:6379/0"', src)
        self.assertNotIn("@localhost:5432", src)


if __name__ == "__main__":
    unittest.main()
