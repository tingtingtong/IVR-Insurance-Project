"""
OpenAI Realtime API session for authentication PII collection.

Why Realtime for auth:
  - Model hears raw audio directly — no STT intermediate errors
  - Structured function calling extracts PII (phone, DOB, name) with high accuracy
  - Server-side VAD handles barge-in natively
  - Model handles clarifications, corrections, and hesitations in context

Audio format pipeline:
  Twilio  → mulaw 8kHz  → pcm16 24kHz → Realtime API
  Twilio  ← mulaw 8kHz  ← pcm16 24kHz ← Realtime API

Auth logic mirrors core/graph/nodes/auth.py:
  collect_phone → party_search
    found  → collect_dob  (phone + DOB pair)
    not found → confirm phone → collect_policy → collect_dob
  collect_dob → check_auth_success
    match → complete ✓
    no match → collect_name
  collect_name → check_auth_success
    match → complete ✓
    fail  → escalate
"""

import asyncio
import audioop
import base64
import json
import structlog
from typing import Callable, Awaitable

import websockets

from config import settings
from core.tools.party_search import party_search, check_auth_success
from core.prompts.retry_prompts import PROMPTS

log = structlog.get_logger()

REALTIME_URL  = "wss://api.openai.com/v1/realtime?model=gpt-realtime-1.5"
REALTIME_VOICE = "alloy"   # options: alloy, ash, ballad, coral, echo, sage, shimmer, verse


# ── Audio format conversion ───────────────────────────────────────────────────

def mulaw8k_to_pcm16_24k(data: bytes, state=None) -> tuple[bytes, object]:
    """Twilio mulaw 8kHz → OpenAI Realtime PCM16 24kHz."""
    linear = audioop.ulaw2lin(data, 2)
    upsampled, new_state = audioop.ratecv(linear, 2, 1, 8000, 24000, state)
    return upsampled, new_state


def pcm16_24k_to_mulaw8k(data: bytes, state=None) -> tuple[bytes, object]:
    """OpenAI Realtime PCM16 24kHz → Twilio mulaw 8kHz."""
    downsampled, new_state = audioop.ratecv(data, 2, 1, 24000, 8000, state)
    mulaw = audioop.lin2ulaw(downsampled, 2)
    return mulaw, new_state


# ── Session configuration ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a voice authentication IVR for insuranceCompany.
Verify the caller's identity using PII. Be brief and professional — this is a phone call.

RULES:
1. Start by asking for the caller's 10-digit phone number (include area code).
2. When the caller gives a phone number, immediately call collect_phone.
3. Follow the instruction returned from each function call.
4. When the caller gives a date of birth, call collect_dob.
5. When the caller gives a policy number, call collect_policy.
6. When the caller gives their name (first + last), call collect_name.
7. Do NOT call a function unless the caller has given you that piece of information.
8. Keep all responses under 2 sentences.
"""

_TOOLS = [
    {
        "type": "function",
        "name": "collect_phone",
        "description": "Caller provided a phone number. Extract 10 digits only.",
        "parameters": {
            "type": "object",
            "properties": {
                "digits": {
                    "type": "string",
                    "description": "Phone number digits only, no spaces or dashes. E.g. 5551234567",
                }
            },
            "required": ["digits"],
        },
    },
    {
        "type": "function",
        "name": "collect_dob",
        "description": "Caller provided a date of birth.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date of birth in YYYY-MM-DD format.",
                }
            },
            "required": ["date"],
        },
    },
    {
        "type": "function",
        "name": "collect_policy",
        "description": "Caller provided a policy number.",
        "parameters": {
            "type": "object",
            "properties": {
                "number": {
                    "type": "string",
                    "description": "Policy number as-spoken. E.g. P300123456",
                }
            },
            "required": ["number"],
        },
    },
    {
        "type": "function",
        "name": "collect_name",
        "description": "Caller provided their first and last name.",
        "parameters": {
            "type": "object",
            "properties": {
                "first": {"type": "string", "description": "First name"},
                "last":  {"type": "string", "description": "Last name"},
            },
            "required": ["first", "last"],
        },
    },
    {
        "type": "function",
        "name": "select_policy",
        "description": "Caller selected a policy by its last 4 digits.",
        "parameters": {
            "type": "object",
            "properties": {
                "last_four": {
                    "type": "string",
                    "description": "Last 4 digits of the selected policy number. E.g. 1234",
                }
            },
            "required": ["last_four"],
        },
    },
]


# ── Realtime auth session ─────────────────────────────────────────────────────

class RealtimeAuthSession:
    """
    Manages an OpenAI Realtime API WebSocket session for authentication.

    Lifecycle:
      start() → send_audio() per Twilio media chunk → on_auth_done called when done
      close()  → clean shutdown (called after on_auth_done or on call end)

    Callbacks:
      on_audio(mulaw_bytes)  — audio to stream back to Twilio
      on_auth_done(result)   — fired when auth succeeds or permanently fails
        result = {authenticated, auth_step, customer?, finalized_party?, pii_collected?}
    """

    def __init__(
        self,
        call_sid: str,
        on_audio:     Callable[[bytes], Awaitable[None]],
        on_auth_done: Callable[[dict],  Awaitable[None]],
    ):
        self.call_sid       = call_sid
        self._on_audio      = on_audio
        self._on_auth_done  = on_auth_done

        self._ws = None   # websockets.legacy.client.WebSocketClientProtocol
        self._recv_task: asyncio.Task | None = None
        self._closed = False

        # PII + auth state
        self._pii:             dict = {}
        self._candidate_party: dict = {}
        self._policy_numbers:  list = []
        self._attempts:        int  = 0
        self._pending_result:  dict | None = None  # set on auth complete, fires on response.done

        # Audio resampling state (continuous across chunks)
        self._up_state   = None   # mulaw→pcm16 ratecv state
        self._down_state = None   # pcm16→mulaw ratecv state

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to Realtime API, configure session, trigger initial greeting."""
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
        }
        self._ws = await websockets.connect(REALTIME_URL, additional_headers=headers)
        self._recv_task = asyncio.create_task(self._recv_loop())

        await self._send({
            "type": "session.update",
            "session": {
                "type":         "realtime",
                "instructions": _SYSTEM_PROMPT,
                "tools":        _TOOLS,
                "tool_choice":  "auto",
            },
        })

        # Trigger the first model response (greeting + phone ask)
        await self._send({"type": "response.create"})
        log.info("realtime_auth_started", call_sid=self.call_sid)

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        """Forward Twilio mulaw 8kHz audio to the Realtime API as PCM16 24kHz."""
        if self._closed or not self._ws:
            return
        pcm16, self._up_state = mulaw8k_to_pcm16_24k(mulaw_bytes, self._up_state)
        encoded = base64.b64encode(pcm16).decode("utf-8")
        await self._send({"type": "input_audio_buffer.append", "audio": encoded})

    async def close(self) -> None:
        """Shut down the session cleanly."""
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        log.info("realtime_auth_closed", call_sid=self.call_sid)

    # ── Receive loop ──────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                event = json.loads(raw)
                await self._dispatch(event)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as e:
            log.error("realtime_recv_error", call_sid=self.call_sid, error=str(e))

    async def _dispatch(self, event: dict) -> None:
        t = event.get("type", "")

        if t == "response.output_audio.delta":
            pcm16 = base64.b64decode(event.get("delta", ""))
            if pcm16:
                mulaw, self._down_state = pcm16_24k_to_mulaw8k(pcm16, self._down_state)
                await self._on_audio(mulaw)

        elif t == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                await self._handle_function_call(item)

        elif t == "response.done":
            # If auth just completed, fire the callback now that model finished speaking
            if self._pending_result:
                result = self._pending_result
                self._pending_result = None
                if not self._closed:
                    await self._on_auth_done(result)

        elif t == "error":
            log.error("realtime_api_error", call_sid=self.call_sid,
                      error=event.get("error", {}).get("message", str(event)))

    # ── Function call dispatch ────────────────────────────────────────────────

    async def _handle_function_call(self, item: dict) -> None:
        name    = item.get("name", "")
        call_id = item.get("call_id", "")
        try:
            args = json.loads(item.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        if name == "collect_phone":
            result = await self._do_collect_phone(args.get("digits", ""))
        elif name == "collect_dob":
            result = await self._do_collect_dob(args.get("date", ""))
        elif name == "collect_policy":
            result = await self._do_collect_policy(args.get("number", ""))
        elif name == "collect_name":
            result = await self._do_collect_name(args.get("first", ""), args.get("last", ""))
        elif name == "select_policy":
            result = self._do_select_policy(args.get("last_four", ""))
        else:
            result = {"error": f"unknown function: {name}"}

        # Return result to model and trigger next response
        await self._send({
            "type": "conversation.item.create",
            "item": {
                "type":    "function_call_output",
                "call_id": call_id,
                "output":  json.dumps(result),
            },
        })
        await self._send({"type": "response.create"})

    # ── PII handlers ──────────────────────────────────────────────────────────

    async def _do_collect_phone(self, raw: str) -> dict:
        from utils.pii_validator import normalize_phone
        digits = normalize_phone(raw)
        if not digits:
            return {
                "ok": False,
                "instruction": (
                    "That doesn't look like a valid 10-digit number. "
                    "Ask the caller to say all 10 digits including area code."
                ),
            }

        self._pii["phoneNumber"] = digits
        result = await party_search(phone=digits)

        if result["success"] and result["parties"]:
            self._candidate_party = result["parties"][0]
            self._policy_numbers = [
                p.get("PolicyNumber", "")
                for p in self._candidate_party.get("Policies", [])
            ]
            return {
                "ok":    True,
                "found": True,
                "instruction": "Phone matched. Ask for the insured's date of birth.",
            }

        formatted = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return {
            "ok":    True,
            "found": False,
            "instruction": (
                f"Phone {formatted} was not found. Read it back and ask the caller to confirm. "
                "If correct, ask for their policy number next."
            ),
        }

    async def _do_collect_dob(self, raw: str) -> dict:
        from utils.pii_validator import normalize_dob
        import re
        # Accept model-provided YYYY-MM-DD directly, or parse spoken format
        normalized = raw if re.match(r"^\d{4}-\d{2}-\d{2}$", raw) else normalize_dob(raw)

        if not normalized:
            return {
                "ok": False,
                "instruction": "Couldn't parse the date. Ask the caller to say month, day, and year.",
            }

        self._pii["dateOfBirth"] = normalized

        if check_auth_success(self._candidate_party, self._pii):
            if len(self._policy_numbers) > 1:
                return self._ask_policy_selection()
            return self._schedule_success()

        return {
            "ok":     True,
            "matched": False,
            "instruction": (
                "Date of birth didn't match. Tell the caller you couldn't verify it, "
                "then ask for the insured's first and last name."
            ),
        }

    async def _do_collect_policy(self, raw: str) -> dict:
        from utils.pii_validator import normalize_policy_number
        normalized = normalize_policy_number(raw)
        if not normalized:
            return {
                "ok": False,
                "instruction": "Couldn't parse the policy number. Ask the caller to say each character slowly.",
            }

        self._pii["policyNumber"] = normalized
        result = await party_search(policy_number=normalized)

        if result["success"] and result["parties"]:
            self._candidate_party = result["parties"][0]
            self._policy_numbers = [
                p.get("PolicyNumber", "")
                for p in self._candidate_party.get("Policies", [])
            ]
            return {
                "ok":    True,
                "found": True,
                "instruction": "Policy found. Now ask for the insured's date of birth.",
            }

        self._attempts += 1
        if self._attempts >= 3:
            self._pending_result = {"authenticated": False, "auth_step": "failed"}
            return {
                "ok": False,
                "instruction": (
                    "Tell the caller you weren't able to verify their information "
                    "and you're transferring them to a representative."
                ),
            }

        return {
            "ok":    False,
            "found": False,
            "instruction": "Policy not found. Ask the caller to try again or offer to transfer.",
        }

    async def _do_collect_name(self, first: str, last: str) -> dict:
        if not first or not last:
            return {
                "ok": False,
                "instruction": "Couldn't get a full name. Ask for first and last name again.",
            }

        self._pii["firstName"] = first
        self._pii["lastName"]  = last

        if check_auth_success(self._candidate_party, self._pii):
            if len(self._policy_numbers) > 1:
                return self._ask_policy_selection()
            return self._schedule_success()

        # Both DOB and name failed → give up
        self._pending_result = {"authenticated": False, "auth_step": "failed"}
        return {
            "ok":     False,
            "matched": False,
            "instruction": (
                "Verification failed. Tell the caller you're transferring them to a representative."
            ),
        }

    # ── Auth completion ───────────────────────────────────────────────────────

    def _ask_policy_selection(self) -> dict:
        """Multi-policy: ask caller to select by last 4 digits."""
        last_fours = ", ".join(p[-4:] for p in self._policy_numbers if len(p) >= 4)
        return {
            "ok":          True,
            "matched":     True,
            "multi_policy": True,
            "instruction": (
                f"The caller has multiple policies. Ask them to say the last 4 digits "
                f"of the policy they want to access. Their options are: {last_fours}. "
                "When they respond, call select_policy with those 4 digits."
            ),
        }

    def _do_select_policy(self, last_four: str) -> dict:
        digits = "".join(c for c in last_four if c.isdigit())[-4:]
        matched = next(
            (p for p in self._policy_numbers if len(digits) == 4 and p.endswith(digits)),
            None,
        )
        if not matched:
            last_fours = ", ".join(p[-4:] for p in self._policy_numbers if len(p) >= 4)
            return {
                "ok": False,
                "instruction": (
                    f"That didn't match any policy. Ask the caller to try again. "
                    f"Options are: {last_fours}."
                ),
            }
        return self._schedule_success(policy_number=matched)

    def _schedule_success(self, policy_number: str = "") -> dict:
        """Mark auth as pending completion. Fires on_auth_done after model speaks."""
        from core.graph.nodes.auth import _build_customer
        customer = _build_customer(self._candidate_party, policy_number)
        self._pending_result = {
            "authenticated":   True,
            "auth_step":       "complete",
            "active_flow":     "",
            "customer":        customer,
            "finalized_party": self._candidate_party,
            "candidate_party": {},
            "pii_collected":   self._pii,
        }
        return {
            "ok":     True,
            "matched": True,
            "instruction": (
                "Say exactly: 'I've verified your identity.' "
                "Do not say anything else after this."
            ),
        }

    # ── Send helper ───────────────────────────────────────────────────────────

    async def _send(self, event: dict) -> None:
        if self._ws and not self._closed:
            try:
                await self._ws.send(json.dumps(event))
            except websockets.ConnectionClosed:
                pass
