"""
Webhook-based IVR — uses Twilio <Gather> for STT and <Say>/<Play> for TTS.
No Media Streams needed. Works reliably with both browser SDK and PSTN calls.

Flow:
  POST /webhook/voice  → greet caller, start listening
  POST /webhook/gather → receive transcript, run LangGraph, respond
"""
import asyncio
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient

from config import settings
import core.graph.graph as _graph_module
from core.prompts.retry_prompts import PROMPTS
from services.session import SessionService
from services.conversation_store import start_call, add_call_turn, end_call, set_recording, update_call_metadata, get_call
from langchain_core.messages import HumanMessage
from utils.call_logger import log_event
from utils.pii_redactor import redact_for_log, remember_identity
from utils.tts_normalizer import normalize_tts_text
from webhooks.security import validate_twilio_webhook
from core.graph.escalate import is_terminal as _is_terminal

_twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

# Twilio form fields safe to log as-is (#67) — no caller speech, digits or phone numbers
_SAFE_FORM_KEYS = frozenset((
    "CallSid", "AccountSid", "ApplicationSid", "CallStatus", "Direction",
    "ApiVersion", "Confidence", "Language", "msg",
))


def _mask_number(number: str) -> str:
    """Mask a caller phone number for logs; browser identities (client:...) are kept."""
    if not number or number.startswith("client:"):
        return number
    digits = sum(c.isdigit() for c in number)
    return number[:3] + "*" * max(0, len(number) - 5) + number[-2:] if digits >= 7 else number


_RECORDING_ATTEMPTS = 5
_RECORDING_RETRY_S = 1.0
_public_base_url: str | None = None  # resolved once — the ngrok lookup is a blocking HTTP call


async def _start_recording(call_sid: str) -> None:
    """Start the call recording once Twilio has answered the call (#63).

    Runs as a background task after the greeting TwiML is sent. The call is only
    answered once Twilio has the TwiML, so asking earlier fails with "Requested
    resource is not eligible for recording". Retries briefly to cover that gap.
    """
    global _public_base_url
    if _public_base_url is None:
        from services.twilio_webhook_sync import resolve_base_url
        _public_base_url = await asyncio.to_thread(resolve_base_url, settings) or ""

    kwargs = {}
    if _public_base_url:
        # REST API needs an absolute URL — a relative path never reaches the app
        kwargs = {
            "recording_status_callback": f"{_public_base_url}/webhook/recording-status",
            "recording_status_callback_method": "POST",
            "recording_status_callback_event": ["completed"],
        }
    else:
        log.warning("recording_callback_url_unknown", call_sid=call_sid)

    for attempt in range(1, _RECORDING_ATTEMPTS + 1):
        try:
            await asyncio.to_thread(_twilio.calls(call_sid).recordings.create, **kwargs)
            log.info("recording_started", call_sid=call_sid, attempt=attempt)
            return
        except Exception as e:
            if attempt == _RECORDING_ATTEMPTS or "not eligible" not in str(e):
                log.warning("recording_start_failed", call_sid=call_sid, attempt=attempt, error=str(e))
                return
            await asyncio.sleep(_RECORDING_RETRY_S)

log = structlog.get_logger()
router = APIRouter()

_GREETING = PROMPTS["greeting"]["welcome"]   # same text on the stream path (#98)
_TIMEOUT_MSG = "I didn't catch that. Please say your request after the tone."
_CONFIRM_TIMEOUT_MSG = "I didn't catch that. Please say yes or no."
_ERROR_MSG   = "I'm sorry, I'm having trouble right now. Please hold while I connect you to an agent."


def _last_bot_text(call_sid: str) -> str:
    try:
        call = get_call(call_sid) or {}
        for turn in reversed(call.get("turns") or []):
            if turn.get("role") == "bot":
                return turn.get("text") or ""
    except Exception:
        pass
    return ""


def _timeout_prompt(call_sid: str) -> str:
    """Use a yes/no retry when the last prompt was a confirmation — not a generic IDK."""
    last = _last_bot_text(call_sid).lower()
    if any(p in last for p in (
        "is that correct",
        "say yes",
        "yes or no",
        "please confirm",
        "repeat those numbers",
        "anything else i can help",
    )):
        return _CONFIRM_TIMEOUT_MSG
    return _TIMEOUT_MSG


def _hangup_response(say_text: str, transfer_to: str = "") -> Response:
    """Speak, optionally Dial an agent, then hang up. Never Gather after this."""
    response = VoiceResponse()
    if say_text:
        response.say(normalize_tts_text(say_text), voice="Polly.Joanna")
    if transfer_to:
        response.dial(transfer_to)
    response.hangup()
    return Response(content=str(response), media_type="application/xml")


def _gather_response(say_text: str, action: str = "/webhook/gather") -> str:
    """Build TwiML that speaks text and waits for speech input."""
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=action,
        method="POST",
        speechTimeout="3",
        language="en-US",
        timeout=10,
        profanityFilter=False,
        actionOnEmptyResult=True,
        hints="yes, no, correct, incorrect, right, wrong, confirm, cancel, repeat, policy, beneficiary, payment, loan, status, help, agent, transfer, january, february, march, april, may, june, july, august, september, october, november, december, nineteen, twenty, sixty, seventy, eighty, ninety",
    )
    gather.say(normalize_tts_text(say_text), voice="Polly.Joanna")
    response.append(gather)
    # Fallback if caller says nothing (actionOnEmptyResult sends to action URL first,
    # but this redirect is a safety net in case Gather doesn't fire at all)
    response.redirect("/webhook/gather", method="POST")
    return str(response)


def _gather_dtmf_or_speech(say_text: str) -> str:
    """BUG-016: Build TwiML that accepts DTMF digits OR speech for card/account numbers.
    DTMF terminates with #. Speech uses normal timeout."""
    response = VoiceResponse()
    gather = Gather(
        input="dtmf speech",
        action="/webhook/gather-payment",
        method="POST",
        speechTimeout="3",
        timeout=15,
        finishOnKey="#",
        numDigits=20,  # generous max to handle card (16) + trailing
        language="en-US",
        profanityFilter=False,
        actionOnEmptyResult=True,
    )
    gather.say(normalize_tts_text(say_text), voice="Polly.Joanna")
    response.append(gather)
    response.redirect("/webhook/gather-payment", method="POST")
    return str(response)


@router.post("/webhook/voice", dependencies=[Depends(validate_twilio_webhook)])
async def incoming_call(request: Request, background_tasks: BackgroundTasks):
    """Initial inbound call — initialize session and play greeting."""
    form = await request.form()
    call_sid  = form.get("CallSid", "")
    from_num  = form.get("From", "")

    log.info("call_started", call_sid=call_sid, from_number=_mask_number(from_num))
    start_call(call_sid, from_num)
    add_call_turn(call_sid, "bot", _GREETING, node="greeting")
    log_event(call_sid, "call_start", from_number=from_num, channel="webhook")

    session = SessionService()
    await session.init_session(call_sid)

    # ANI pre-check: look up the caller's phone number before they speak.
    # If found, store in session so the first graph invocation includes it.
    if from_num:
        try:
            from core.tools.party_search import party_search
            digits = "".join(c for c in from_num if c.isdigit())
            if len(digits) > 10:
                digits = digits[-10:]  # strip country code
            if len(digits) == 10:
                result = await party_search(phone=digits)
                if result["success"] and result["parties"]:
                    remember_identity(result["parties"][0])  # mask this caller's name in logs/traces
                    state = await session.get_state(call_sid)
                    state["pii_collected"] = {"phoneNumber": digits}
                    state["candidate_party"] = result["parties"][0]
                    state["auth_step"] = "collecting_dob"
                    await session.save_state(call_sid, state)
                    log_event(call_sid, "ani_match", phone=digits[:3] + "***" + digits[7:])
                else:
                    log_event(call_sid, "ani_no_match", phone=digits[:3] + "***" + digits[7:])
        except Exception as e:
            log.warning("ani_check_failed", call_sid=call_sid, error=str(e))

    # Start recording after this response is sent — the call isn't answered until
    # Twilio has the TwiML (#63). Status callback fires when the recording is ready.
    background_tasks.add_task(_start_recording, call_sid)

    return Response(content=_gather_response(_GREETING), media_type="application/xml")


@router.post("/webhook/gather", dependencies=[Depends(validate_twilio_webhook)])
async def gather_speech(request: Request):
    """Receive Twilio speech transcript, run LangGraph, speak response."""
    form        = await request.form()
    call_sid    = form.get("CallSid", "")
    transcript  = form.get("SpeechResult", "").strip()
    confidence  = form.get("Confidence", "0")

    # ── Diagnostic: log Twilio form params, never the caller's words or digits ──
    # SpeechResult / Digits carry CVV, expiry, DOB; From / Caller / To are phone
    # numbers on PSTN calls (#67). Only sizes of those are logged.
    form_log = {k: v for k, v in form.items() if k in _SAFE_FORM_KEYS}
    form_log["speech_len"] = len(form.get("SpeechResult", ""))
    form_log["digits_len"] = len(form.get("Digits", ""))
    log.info("gather_raw", call_sid=call_sid, form_params=form_log)

    safe_transcript = redact_for_log(transcript)
    log.info("speech_received", call_sid=call_sid,
             transcript=safe_transcript, confidence=confidence)
    log_event(call_sid, "stt_result", transcript=safe_transcript[:80],
              confidence=float(confidence) if confidence else 0.0,
              chars=len(transcript))

    if not transcript:
        log_event(call_sid, "stt_empty", reason="no_speech_result",
                  form_keys=list(form.keys()))
        timeout_msg = _timeout_prompt(call_sid)
        add_call_turn(call_sid, "human", "[no speech detected]")
        add_call_turn(call_sid, "bot", timeout_msg, node="gather_timeout")
        return Response(
            content=_gather_response(timeout_msg),
            media_type="application/xml",
        )

    add_call_turn(call_sid, "human", transcript)

    try:
        import time as _time
        t_graph = _time.time()

        # Build graph input — always include the new message.
        graph_input = {"messages": [HumanMessage(content=transcript)], "call_sid": call_sid}

        # On the first turn, inject ANI pre-check data from the session
        # so the graph checkpoint starts with phone + candidate_party populated.
        session = SessionService()
        sess_state = await session.get_state(call_sid)
        if sess_state.get("auth_step") == "collecting_dob" and sess_state.get("candidate_party"):
            # ANI match was found — seed the graph state
            graph_input["pii_collected"] = sess_state["pii_collected"]
            graph_input["candidate_party"] = sess_state["candidate_party"]
            graph_input["auth_step"] = "collecting_dob"
            # Clear session flag so we don't re-inject on subsequent turns
            sess_state["auth_step"] = ""
            await session.save_state(call_sid, sess_state)

        # LangGraph checkpointer owns state across turns after first injection.
        result = await _graph_module.cno_graph.ainvoke(
            graph_input,
            config={"configurable": {"thread_id": call_sid}},
        )

        graph_latency = int((_time.time() - t_graph) * 1000)
        remember_identity(result)  # names from auth → masked in later logs/traces
        tts_text     = result.get("tts_text", "")
        # BUG-015: Hint about pending intents so caller knows to confirm
        from utils.pending_intent_hint import append_pending_hint
        tts_text = append_pending_hint(tts_text, result)
        transfer_to  = result.get("transfer_to", "")
        current_node = result.get("current_node", "")
        intent       = result.get("current_intent", "")
        auth_step    = result.get("auth_step", "")

        log_event(call_sid, "graph_result", node=current_node, intent=intent,
                  auth_step=auth_step, authenticated=result.get("authenticated", False),
                  caller_persona=result.get("caller_persona", ""),
                  active_flow=result.get("active_flow", ""),
                  graph_latency_ms=graph_latency,
                  tts_preview=tts_text[:80] if tts_text else "")

        update_call_metadata(call_sid, result)
        add_call_turn(call_sid, "bot", tts_text, intent=intent, node=current_node)
        log_event(call_sid, "tts_sent", node=current_node, intent=intent, chars=len(tts_text))

        if _is_terminal(result):
            end_call(call_sid)
            reason = "goodbye" if current_node == "goodbye" else "escalate"
            log_event(call_sid, "call_end", reason=reason, to=transfer_to or "")
            return _hangup_response(tts_text, transfer_to)

        # BUG-016: When OTP is collecting sensitive numbers, accept both DTMF and speech
        otp_step = result.get("otp_step", "")
        if otp_step in ("collecting_card_dtmf", "collecting_bank_dtmf"):
            return Response(
                content=_gather_dtmf_or_speech(tts_text or "Please enter or say your number."),
                media_type="application/xml",
            )

        speak = tts_text or (
            "Is there anything else I can help you with today?"
        )
        return Response(
            content=_gather_response(speak),
            media_type="application/xml",
        )

    except Exception as e:
        log.error("graph_error", call_sid=call_sid, error=str(e))
        log_event(call_sid, "error", error=str(e), node="graph")
        end_call(call_sid)
        response = VoiceResponse()
        response.say(_ERROR_MSG, voice="Polly.Joanna")
        response.dial(settings.twilio_agent_phone_number)
        return Response(content=str(response), media_type="application/xml")


@router.post("/webhook/gather-payment", dependencies=[Depends(validate_twilio_webhook)])
async def gather_payment(request: Request):
    """BUG-016: Handle DTMF or speech input for card/account number collection.
    Merges the collected number into graph state and continues the OTP flow.
    BUG-024: Uses robust card extractor for all STT variations."""
    import re
    from utils.card_extractor import extract_card_digits
    form = await request.form()
    call_sid   = form.get("CallSid", "")
    digits     = form.get("Digits", "").strip()
    speech     = form.get("SpeechResult", "").strip()

    log.info(
        "gather_payment_raw",
        call_sid=call_sid,
        digits=("[REDACTED]" if digits else ""),
        speech=("[REDACTED]" if speech else ""),
        input_kind=("dtmf" if digits else ("speech" if speech else "empty")),
    )

    # Prefer DTMF digits over speech if both present
    collected_number = ""
    if digits:
        collected_number = re.sub(r"\D", "", digits)
    elif speech:
        # BUG-024: Robust extraction handles word numbers, commas, dots, homophones
        collected_number = extract_card_digits(speech)

    if not collected_number:
        # Nothing received — re-prompt
        return Response(
            content=_gather_dtmf_or_speech("I didn't receive any input. Please enter or say your number."),
            media_type="application/xml",
        )

    # Determine which field based on current otp_step in graph state
    try:
        result = await _graph_module.cno_graph.ainvoke(
            {"messages": [HumanMessage(content=collected_number)], "call_sid": call_sid},
            config={"configurable": {"thread_id": call_sid}},
        )

        # Check current state to set the right field name
        otp_data = dict(result.get("otp_data", {}))
        payment_type = otp_data.get("payment_type", "card")

        if payment_type == "card":
            otp_data["card_number"] = collected_number
        else:
            otp_data["account_number"] = collected_number

        # Re-invoke graph with dtmf_complete to trigger validation + next step
        result2 = await _graph_module.cno_graph.ainvoke(
            {"otp_step": "dtmf_complete", "otp_data": otp_data, "call_sid": call_sid},
            config={"configurable": {"thread_id": call_sid}},
        )

        tts_text     = result2.get("tts_text", "")
        current_node = result2.get("current_node", "")
        otp_step     = result2.get("otp_step", "")

        update_call_metadata(call_sid, result2)
        add_call_turn(call_sid, "bot", tts_text, node=current_node)

        if _is_terminal(result2):
            end_call(call_sid)
            log_event(call_sid, "call_end", reason="escalate",
                      to=result2.get("transfer_to") or "")
            return _hangup_response(tts_text, result2.get("transfer_to") or "")

        # If still collecting card (retry), use DTMF+speech gather again
        if otp_step in ("collecting_card_dtmf", "collecting_bank_dtmf"):
            return Response(
                content=_gather_dtmf_or_speech(tts_text),
                media_type="application/xml",
            )

        # Otherwise continue with normal speech gather
        return Response(
            content=_gather_response(tts_text or "How can I help you?"),
            media_type="application/xml",
        )

    except Exception as e:
        log.error("gather_payment_error", call_sid=call_sid, error=str(e))
        return Response(
            content=_gather_response("I'm sorry, there was an error. Let me try again. How can I help you?"),
            media_type="application/xml",
        )


@router.post("/webhook/status", dependencies=[Depends(validate_twilio_webhook)])
async def call_status(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    duration = form.get("CallDuration", "")
    log.info("call_status", call_sid=call_sid, status=status, duration=duration)
    if status in ("completed", "canceled", "failed", "busy", "no-answer"):
        end_call(call_sid)
        log_event(call_sid, "call_end", reason=f"status_callback:{status}", duration=duration)
    return Response(content="", status_code=204)


@router.post("/webhook/recording-status", dependencies=[Depends(validate_twilio_webhook)])
async def recording_status(request: Request):
    """
    Twilio fires this when a call recording is ready.
    Stores the recording SID + URL against the call in conversation_store.
    The URL points to the .mp3 on Twilio's servers (requires auth to download).
    """
    form          = await request.form()
    call_sid      = form.get("CallSid", "")
    recording_sid = form.get("RecordingSid", "")
    recording_url = form.get("RecordingUrl", "")
    status        = form.get("RecordingStatus", "")
    duration      = form.get("RecordingDuration", "")

    log.info(
        "recording_ready",
        call_sid=call_sid,
        recording_sid=recording_sid,
        status=status,
        duration_s=duration,
    )

    if status == "completed" and recording_sid:
        # Append .mp3 for direct playback link
        mp3_url = recording_url + ".mp3" if recording_url and not recording_url.endswith(".mp3") else recording_url
        set_recording(call_sid, recording_sid, mp3_url)

    return Response(content="", status_code=204)
