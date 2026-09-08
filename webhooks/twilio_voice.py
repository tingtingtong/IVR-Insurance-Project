"""
Webhook-based IVR — uses Twilio <Gather> for STT and <Say>/<Play> for TTS.
No Media Streams needed. Works reliably with both browser SDK and PSTN calls.

Flow:
  POST /webhook/voice  → greet caller, start listening
  POST /webhook/gather → receive transcript, run LangGraph, respond
"""
import structlog
from fastapi import APIRouter, Depends, Request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient

from config import settings
import core.graph.graph as _graph_module
from services.session import SessionService
from services.conversation_store import start_call, add_call_turn, end_call, set_recording, update_call_metadata
from langchain_core.messages import HumanMessage
from utils.call_logger import log_event
from utils.tts_normalizer import normalize_tts_text
from webhooks.security import validate_twilio_webhook

_twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

log = structlog.get_logger()
router = APIRouter()

_GREETING = (
    "Thank you for calling. "
    "I'm your virtual assistant and I'm here to help you with your life insurance policy. "
    "How can I help you today?"
)
_TIMEOUT_MSG = "I didn't catch that. Please say your request after the tone."
_ERROR_MSG   = "I'm sorry, I'm having trouble right now. Please hold while I connect you to an agent."


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
async def incoming_call(request: Request):
    """Initial inbound call — initialize session and play greeting."""
    form = await request.form()
    call_sid  = form.get("CallSid", "")
    from_num  = form.get("From", "")

    log.info("call_started", call_sid=call_sid, from_number=from_num)
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

    # Start recording — status callback fires when recording is available
    try:
        _twilio.calls(call_sid).recordings.create(
            recording_status_callback="/webhook/recording-status",
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed"],
        )
        log.info("recording_started", call_sid=call_sid)
    except Exception as e:
        log.warning("recording_start_failed", call_sid=call_sid, error=str(e))

    return Response(content=_gather_response(_GREETING), media_type="application/xml")


@router.post("/webhook/gather", dependencies=[Depends(validate_twilio_webhook)])
async def gather_speech(request: Request):
    """Receive Twilio speech transcript, run LangGraph, speak response."""
    form        = await request.form()
    call_sid    = form.get("CallSid", "")
    transcript  = form.get("SpeechResult", "").strip()
    confidence  = form.get("Confidence", "0")

    # ── Diagnostic: log ALL Twilio form params to detect missing/unexpected data ──
    form_keys = {k: (v if k not in ("SpeechResult",) else v[:60]) for k, v in form.items()}
    log.info("gather_raw", call_sid=call_sid, form_params=form_keys)

    log.info("speech_received", call_sid=call_sid,
             transcript=transcript, confidence=confidence)
    log_event(call_sid, "stt_result", transcript=transcript[:80],
              confidence=float(confidence) if confidence else 0.0,
              chars=len(transcript))

    if not transcript:
        log_event(call_sid, "stt_empty", reason="no_speech_result",
                  form_keys=list(form.keys()))
        return Response(
            content=_gather_response(_TIMEOUT_MSG),
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

        if transfer_to:
            end_call(call_sid)
            log_event(call_sid, "call_transfer", to=transfer_to)
            response = VoiceResponse()
            if tts_text:
                response.say(normalize_tts_text(tts_text), voice="Polly.Joanna")
            response.dial(transfer_to)
            # Hangup after dial completes/fails — prevents call from continuing
            response.say("Thank you for calling. Goodbye.", voice="Polly.Joanna")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

        if current_node == "goodbye":
            end_call(call_sid)
            log_event(call_sid, "call_end", reason="goodbye")
            response = VoiceResponse()
            response.say(normalize_tts_text(tts_text or "Thank you for calling. Goodbye."), voice="Polly.Joanna")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

        # BUG-016: When OTP is collecting sensitive numbers, accept both DTMF and speech
        otp_step = result.get("otp_step", "")
        if otp_step in ("collecting_card_dtmf", "collecting_bank_dtmf"):
            return Response(
                content=_gather_dtmf_or_speech(tts_text or "Please enter or say your number."),
                media_type="application/xml",
            )

        speak = tts_text or "I'm sorry, I didn't understand. Could you please repeat that?"
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

    log.info("gather_payment_raw", call_sid=call_sid, digits=digits, speech=speech[:60] if speech else "")

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

        if result2.get("transfer_to"):
            end_call(call_sid)
            response = VoiceResponse()
            if tts_text:
                response.say(normalize_tts_text(tts_text), voice="Polly.Joanna")
            response.dial(result2["transfer_to"])
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

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
