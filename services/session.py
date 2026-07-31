import json
import structlog
import redis.asyncio as aioredis
from config import settings

log = structlog.get_logger()
SESSION_TTL = 3600  # 1 hour — max IVR call duration


class SessionService:
    """Manages per-call session state in Redis (replaces transient variables)."""

    def __init__(self):
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            try:
                # socket_connect_timeout / socket_timeout ensure ping() fails fast
                # (within 2 s) when Docker just started and Redis isn't ready yet.
                # Without these, ping() blocks for OS-level timeout (~minutes on
                # Windows), causing Twilio's 15 s webhook deadline to expire → 31005.
                r = await aioredis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                await r.ping()
                self._redis = r
            except Exception:
                log.warning("redis_unavailable_using_fakeredis", url=settings.redis_url)
                import fakeredis.aioredis as fakeredis
                if not hasattr(SessionService, '_fake_redis_instance'):
                    SessionService._fake_redis_instance = fakeredis.FakeRedis(decode_responses=True)
                self._redis = SessionService._fake_redis_instance
        return self._redis

    def _key(self, call_sid: str) -> str:
        return f"cno:session:{call_sid}"

    async def init_session(self, call_sid: str) -> dict:
        """Create a blank session for a new call."""
        state = {
            "call_sid":      call_sid,
            "stream_sid":    "",
            "authenticated": False,
            "auth_step":     "collecting_phone",
            "auth_attempts": 0,
            "customer":      {},
            "access_token":  "",
            "finalized_party": {},
            "candidate_party": {},
            "pii_collected": {},
            "current_intent": "",
            "current_node":  "",
            "active_flow":   "",
            "slot_attempts": {},
            "tts_text":      "",
            "transfer_to":   "",
            "otp_step":      "",
            "otp_data":      {},
            "metric_data":   {
                "intentList":     [],
                "apiCallsList":   [],
                "piiSuccessList": [],
                "piiFailureList": [],
            },
            "messages": [],
        }
        r = await self._get_redis()
        await r.setex(self._key(call_sid), SESSION_TTL, json.dumps(state))
        return state

    async def get_state(self, call_sid: str) -> dict:
        r = await self._get_redis()
        raw = await r.get(self._key(call_sid))
        if raw:
            return json.loads(raw)
        return await self.init_session(call_sid)

    async def save_state(self, call_sid: str, state: dict) -> None:
        r = await self._get_redis()
        # messages are LangChain message objects — serialize to dicts.
        # Prefer model_dump() (LangChain v0.2+); fall back to dict() for older
        # installs; fall back to the raw value if it's already a plain dict.
        def _serialize_msg(m):
            if hasattr(m, "model_dump"):
                return m.model_dump()
            if hasattr(m, "dict"):
                return m.dict()
            return m

        serializable = {
            **state,
            "messages": [_serialize_msg(m) for m in state.get("messages", [])],
        }
        await r.setex(self._key(call_sid), SESSION_TTL, json.dumps(serializable))

    async def set_transfer(self, call_sid: str, phone_number: str) -> None:
        state = await self.get_state(call_sid)
        state["transfer_to"] = phone_number
        await self.save_state(call_sid, state)

    async def delete_session(self, call_sid: str) -> None:
        r = await self._get_redis()
        await r.delete(self._key(call_sid))

    async def get_slot_attempts(self, call_sid: str, slot: str) -> dict:
        state = await self.get_state(call_sid)
        return state.get("slot_attempts", {}).get(slot, {"invalid": 0, "blank": 0, "no": 0})

    async def increment_slot_attempt(self, call_sid: str, slot: str, attempt_type: str) -> int:
        state = await self.get_state(call_sid)
        if "slot_attempts" not in state:
            state["slot_attempts"] = {}
        if slot not in state["slot_attempts"]:
            state["slot_attempts"][slot] = {"invalid": 0, "blank": 0, "no": 0}
        state["slot_attempts"][slot][attempt_type] = (
            state["slot_attempts"][slot].get(attempt_type, 0) + 1
        )
        await self.save_state(call_sid, state)
        return state["slot_attempts"][slot][attempt_type]

    async def reset_slot_attempts(self, call_sid: str, slot: str) -> None:
        state = await self.get_state(call_sid)
        if "slot_attempts" in state:
            state["slot_attempts"].pop(slot, None)
        await self.save_state(call_sid, state)
