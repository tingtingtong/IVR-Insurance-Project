"""
WebSocket /stream — bidirectional Twilio Media Stream handler.

Two auth modes (set AUTH_MODE in .env):

  standard (default):
    Twilio ──audio──► Deepgram STT ──transcript──► LangGraph ──tts_text──► ElevenLabs ──audio──► Twilio
    Barge-in: local VADService detects speech during TTS playback

  realtime:
    During auth: Twilio ──audio──► OpenAI Realtime API ──audio──► Twilio
      Realtime handles STT + NLU + TTS + barge-in natively via server VAD
      PII extracted via function calls → party_search → auth complete/fail
    Post-auth: switches to standard Deepgram + ElevenLabs + LangGraph pipeline
"""
import asyncio
import base64
import json
import secrets
import structlog

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, AIMessage

from config import settings
import core.graph.graph as _graph_module
from core.prompts.retry_prompts import PROMPTS
from services.session import SessionService
from services.stt import STTService
from services.tts import TTSService
from services.vad import VADService
from services.conversation_store import update_call_metadata, start_call as _cs_start_call

log = structlog.get_logger()


# ── DTMF collection ───────────────────────────────────────────────────────────

class DTMFCollector:
    """
    Accumulates DTMF keypad digits across multiple sub-steps for payment collection.
    Callers terminate each field with #. * is treated as backspace.

    Card  flow: card_number (16 digits) → expiry (4 digits MMYY) → cvv (3-4 digits) → amount
    Bank  flow: routing_number (9 digits) → account_number → amount

    The first prompt for each flow is spoken by otp_node before DTMF mode activates.
    DTMFCollector only needs to supply the prompts for subsequent fields.
    """

    _CARD_STEPS: list[tuple[str, str | None]] = [
        # (state_key,         prompt_spoken_after_this_field_is_captured)
        ("card_number",   "Please enter your card expiry as four digits, month then year, followed by the pound sign."),
        ("expiry",        "Please enter your three or four digit security code, followed by the pound sign."),
        ("cvv",           "Please enter the payment amount in whole dollars, followed by the pound sign."),
        ("amount",        None),   # last field — triggers completion
    ]

    _BANK_STEPS: list[tuple[str, str | None]] = [
        ("routing_number",  "Please enter your account number, followed by the pound sign."),
        ("account_number",  "Please enter the payment amount in whole dollars, followed by the pound sign."),
        ("amount",          None),
    ]

    def __init__(self, payment_type: str) -> None:
        self._steps   = self._CARD_STEPS if payment_type == "card" else self._BANK_STEPS
        self._idx     = 0
        self._buffer  = ""
        self._data: dict[str, object] = {}

    def push_digit(self, digit: str) -> dict | None:
        """
        Feed one DTMF digit. Returns:
          None                                     — still collecting current field
          {"type": "prompt", "text": "..."}        — field captured, prompt for next field
          {"type": "complete", "data": {...}}       — all fields captured
        """
        if digit == "#":
            field, next_prompt = self._steps[self._idx]
            self._data[field] = self._buffer
            self._buffer = ""
            self._idx += 1

            if self._idx >= len(self._steps):
                return {"type": "complete", "data": self._formatted_data()}

            return {"type": "prompt", "text": next_prompt}

        if digit == "*":
            self._buffer = self._buffer[:-1]
        else:
            self._buffer += digit

        return None

    def _formatted_data(self) -> dict:
        out = dict(self._data)
        # "0628" → "06/28"
        if "expiry" in out and len(str(out["expiry"])) == 4:
            e = str(out["expiry"])
            out["expiry"] = f"{e[:2]}/{e[2:]}"
        # dollars entered as whole number → float
        if "amount" in out:
            try:
                out["amount"] = float(str(out["amount"]))
            except ValueError:
                out["amount"] = 0.0
        return out


router = APIRouter()


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    # Validate shared secret when WS_AUTH_TOKEN is configured in .env.
    # In TwiML, append ?token=<WS_AUTH_TOKEN> to the <Stream url>.
    await websocket.accept()

    if settings.ws_auth_token:
        token = websocket.query_params.get("token", "")
        if not token or not secrets.compare_digest(
            token.encode("utf-8"), settings.ws_auth_token.encode("utf-8")
        ):
            log.warning("stream_auth_rejected", reason="invalid_token")
            await websocket.close(code=4001)
            return
    handler = CallHandler(websocket)
    await handler.run()


class CallHandler:
    def __init__(self, ws: WebSocket):
        self.ws          = ws
        self.call_sid    = ""
        self.stream_sid  = ""
        self.session     = SessionService()
        self.tts         = TTSService()
        self.vad         = VADService()
        self.stt: STTService | None = None

        # Barge-in control
        self._tts_task: asyncio.Task | None = None
        self._tts_cancelled = asyncio.Event()
        self._speaking = False

        # Prevent concurrent LangGraph calls
        self._processing = asyncio.Lock()

        # Realtime auth (only used when AUTH_MODE=realtime)
        self._realtime_auth = None   # RealtimeAuthSession | None
        self._auth_active   = False  # True while Realtime is handling auth

        # DTMF payment collection
        self._dtmf_mode      = False               # suppresses STT processing
        self._dtmf_collector: DTMFCollector | None = None

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        try:
            async for raw in self.ws.iter_text():
                msg = json.loads(raw)
                event = msg.get("event")

                if event == "connected":
                    continue

                elif event == "start":
                    await self._on_start(msg)

                elif event == "media":
                    await self._on_media(msg)

                elif event == "dtmf":
                    await self._on_dtmf(msg)

                elif event == "stop":
                    break

        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.error("stream_error", call_sid=self.call_sid, error=str(e))
        finally:
            await self._cleanup()

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_start(self, msg: dict):
        self.stream_sid = msg.get("streamSid", "")
        start_data = msg.get("start", {})
        self.call_sid = start_data.get("callSid", "")

        log.info("call_started", call_sid=self.call_sid, stream_sid=self.stream_sid,
                 auth_mode=settings.auth_mode)

        # Ensure call record exists and tag it as websocket channel
        from services import conversation_store as _cs
        if self.call_sid not in _cs._calls:
            _cs_start_call(self.call_sid, start_data.get("customParameters", {}).get("from", "stream"), channel="websocket")
        else:
            _cs._calls[self.call_sid]["channel"] = "websocket"

        # Init session state in Redis
        await self.session.init_session(self.call_sid)

        if settings.auth_mode == "realtime":
            # Realtime mode: OpenAI handles auth conversation (STT + NLU + TTS + barge-in)
            from services.realtime_auth import RealtimeAuthSession
            self._auth_active   = True
            self._realtime_auth = RealtimeAuthSession(
                call_sid      = self.call_sid,
                on_audio      = self._relay_audio_to_twilio,
                on_auth_done  = self._on_realtime_auth_done,
            )
            await self._realtime_auth.start()
            # Model speaks the greeting itself — don't call _speak()
        else:
            # Standard mode: Deepgram STT → LangGraph → ElevenLabs TTS
            self.stt = STTService(on_transcript=self._on_transcript)
            await self.stt.start()
            await self._speak(PROMPTS["greeting"]["welcome"])

    async def _on_media(self, msg: dict):
        payload = msg.get("media", {}).get("payload", "")
        if not payload:
            return

        audio_bytes = base64.b64decode(payload)

        # Realtime auth mode: route audio to Realtime API (server VAD handles barge-in)
        if self._auth_active and self._realtime_auth:
            await self._realtime_auth.send_audio(audio_bytes)
            return

        # Standard mode: Deepgram STT + local VAD barge-in
        if self.stt:
            await self.stt.send_audio(audio_bytes)

        if self._speaking:
            should_interrupt = self.vad.process(audio_bytes)
            if should_interrupt:
                await self._cancel_tts()

    # ── Transcript handler (called by Deepgram) ───────────────────────────────

    async def _on_transcript(self, text: str, is_final: bool):
        if not is_final or not text.strip():
            return

        # Suppress STT while collecting DTMF — keypad digits aren't speech turns
        if self._dtmf_mode:
            return

        log.info("transcript", call_sid=self.call_sid, text=text)

        # Don't process if we're already in the middle of a graph call
        if self._processing.locked():
            return

        async with self._processing:
            await self._process_turn(text)

    async def _process_turn(self, utterance: str):
        """Append utterance to history, then invoke the graph."""
        state = await self.session.get_state(self.call_sid)
        state.setdefault("messages", [])
        state["messages"] = state["messages"] + [HumanMessage(content=utterance)]
        await self._invoke_graph_and_respond(state)

    async def _invoke_graph_and_respond(self, state: dict):
        """Run LangGraph with the given state → speak response → handle DTMF/transfer."""
        try:
            result = await _graph_module.cno_graph.ainvoke(
                state,
                config={"configurable": {"thread_id": self.call_sid}},
            )
        except Exception as e:
            log.error("graph_error", call_sid=self.call_sid, error=str(e))
            await self._speak(PROMPTS["escalation"]["error"])
            return

        await self.session.save_state(self.call_sid, result)
        update_call_metadata(self.call_sid, result)

        tts_text    = result.get("tts_text", "")
        transfer_to = result.get("transfer_to", "")
        otp_step    = result.get("otp_step", "")

        if tts_text:
            await self._speak(tts_text)

        # Activate DTMF mode when the OTP node is waiting for keypad input.
        # The node has already spoken the first collection prompt via tts_text above.
        if otp_step in ("collecting_card_dtmf", "collecting_bank_dtmf"):
            payment_type = result.get("otp_data", {}).get("payment_type", "card")
            self._dtmf_mode      = True
            self._dtmf_collector = DTMFCollector(payment_type)
            log.info("dtmf_mode_activated", call_sid=self.call_sid, payment_type=payment_type)

        if transfer_to:
            await self._transfer_call(transfer_to)

    # ── DTMF handlers ─────────────────────────────────────────────────────────

    async def _on_dtmf(self, msg: dict):
        """
        Handle a single DTMF digit event from Twilio.
        Twilio sends: {"event": "dtmf", "streamSid": "...", "dtmf": {"digit": "5", "track": "inbound"}}
        """
        digit = msg.get("dtmf", {}).get("digit", "")
        if not digit or not self._dtmf_mode or not self._dtmf_collector:
            return

        log.info("dtmf_digit", call_sid=self.call_sid, digit="*" if digit.isdigit() else digit)

        result = self._dtmf_collector.push_digit(digit)
        if result is None:
            return  # still accumulating current field

        if result["type"] == "prompt":
            # Field captured — speak the prompt for the next field
            await self._speak(result["text"])

        elif result["type"] == "complete":
            # All fields collected — hand off to the graph
            self._dtmf_mode      = False
            self._dtmf_collector = None
            if self._processing.locked():
                return
            async with self._processing:
                await self._on_dtmf_complete(result["data"])

    async def _on_dtmf_complete(self, collected: dict):
        """
        Merge collected DTMF data into session state and invoke the graph
        with otp_step='dtmf_complete' so otp_node moves to confirmation.
        """
        log.info("dtmf_collection_complete", call_sid=self.call_sid,
                 fields=list(collected.keys()))
        state = await self.session.get_state(self.call_sid)
        state["otp_data"] = {**state.get("otp_data", {}), **collected}
        state["otp_step"] = "dtmf_complete"
        await self._invoke_graph_and_respond(state)

    # ── Realtime auth callbacks ───────────────────────────────────────────────

    async def _relay_audio_to_twilio(self, mulaw_bytes: bytes) -> None:
        """Stream Realtime API audio output back to the caller via Twilio."""
        payload = base64.b64encode(mulaw_bytes).decode("utf-8")
        await self.ws.send_text(json.dumps({
            "event":     "media",
            "streamSid": self.stream_sid,
            "media":     {"payload": payload},
        }))

    async def _on_realtime_auth_done(self, auth_result: dict) -> None:
        """
        Called when Realtime auth session completes (success or failure).
        1. Closes Realtime session
        2. Updates Redis session state with auth result
        3. Starts Deepgram for post-auth conversation
        4. If failed, speaks escalation prompt and transfers
        """
        log.info("realtime_auth_done", call_sid=self.call_sid,
                 authenticated=auth_result.get("authenticated"))

        # Stop routing audio to Realtime
        self._auth_active = False

        # Close Realtime session
        if self._realtime_auth:
            await self._realtime_auth.close()
            self._realtime_auth = None

        # Persist auth result into Redis session state
        state = await self.session.get_state(self.call_sid)
        state.update(auth_result)

        if auth_result.get("authenticated"):
            # Realtime auth bypasses auth_node, so we acquire the access token here.
            # This mirrors the token acquisition in auth_node for the standard path.
            from core.tools.auth_token import acquire_access_token
            customer = state.get("customer", {})
            token = await acquire_access_token(
                party_key=customer.get("partyKey", ""),
                company_code=customer.get("companyCode", ""),
            )
            if not token:
                # Token failed — demote to auth failure so we don't proceed with
                # empty credentials on every downstream API call
                state["authenticated"] = False
                state["auth_step"]     = "failed"
                log.warning("realtime_auth_token_failed", call_sid=self.call_sid)
            else:
                state["access_token"] = token
                # Add authenticated greeting to message history so LangGraph
                # has context on the next turn
                state.setdefault("messages", [])
                state["messages"] = state["messages"] + [
                    {"type": "ai", "content": PROMPTS["greeting"]["authenticated"]}
                ]

        await self.session.save_state(self.call_sid, state)

        # Start Deepgram for post-auth intent handling
        self.stt = STTService(on_transcript=self._on_transcript)
        await self.stt.start()

        if not state.get("authenticated"):
            await self._speak(PROMPTS["escalation"]["max_attempts"])
            await self._transfer_call(settings.twilio_agent_phone_number)

    # ── TTS streaming ─────────────────────────────────────────────────────────

    async def _speak(self, text: str):
        """Stream TTS audio to Twilio. Supports cancellation via barge-in."""
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()

        self._tts_cancelled.clear()
        self._tts_task = asyncio.create_task(self._stream_tts(text))
        await self._tts_task

    async def _stream_tts(self, text: str):
        self._speaking = True
        self.vad.reset()
        chunk_count = 0

        try:
            log.info("tts_start", call_sid=self.call_sid, stream_sid=self.stream_sid, text_len=len(text))
            async for mulaw_chunk in self.tts.stream(text):
                if self._tts_cancelled.is_set():
                    break
                chunk_count += 1
                payload = base64.b64encode(mulaw_chunk).decode("utf-8")
                await self.ws.send_text(json.dumps({
                    "event":     "media",
                    "streamSid": self.stream_sid,
                    "media":     {"payload": payload},
                }))
            log.info("tts_done", call_sid=self.call_sid, chunks_sent=chunk_count)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("tts_stream_error", call_sid=self.call_sid, error=str(e), chunks_sent=chunk_count)
        finally:
            self._speaking = False
            self.vad.reset()

    async def _cancel_tts(self):
        """Cancel in-progress TTS and clear Twilio's audio buffer."""
        self._tts_cancelled.set()
        if self._tts_task:
            self._tts_task.cancel()

        # Clear buffered audio on Twilio's side
        await self.ws.send_text(json.dumps({
            "event":     "clear",
            "streamSid": self.stream_sid,
        }))

        log.info("barge_in_fired", call_sid=self.call_sid)

    # ── Call transfer ─────────────────────────────────────────────────────────

    async def _transfer_call(self, phone_number: str):
        """
        Trigger a call transfer by updating the call via Twilio REST API.
        Redirects to a TwiML that dials the agent.
        """
        from twilio.rest import Client
        from config import settings

        try:
            client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
            client.calls(self.call_sid).update(
                twiml=f'<Response><Dial>{phone_number}</Dial></Response>'
            )
            log.info("call_transferred", call_sid=self.call_sid, to=phone_number)
        except Exception as e:
            log.error("transfer_failed", call_sid=self.call_sid, error=str(e))

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def _cleanup(self):
        if self._realtime_auth:
            await self._realtime_auth.close()
        if self.stt:
            await self.stt.finish()
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        # Send a proper WebSocket close frame so Twilio's gateway gets a clean
        # close handshake instead of a TCP reset.  Without this, any exception
        # in _on_start (e.g. STT service unreachable right after restart) leaves
        # the socket half-open and Twilio sends error 31005 to the caller.
        try:
            await self.ws.close()
        except Exception:
            pass
        log.info("call_ended", call_sid=self.call_sid)
