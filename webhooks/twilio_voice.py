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
        speechTimeout="auto",
        language="en-US",
        timeout=8,
        profanityFilter=False,
        hints="yes, no, correct, incorrect, right, wrong, confirm, cancel, repeat, policy, beneficiary, payment, loan, status, help, agent, transfer",
    )
    gather.say(say_text, voice="Polly.Joanna")
    response.append(gather)
    # Fallback if caller says nothing
    response.redirect("/webhook/gather", method="POST")
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

    # Start recording in a background thread — the Twilio SDK is synchronous and
    # its HTTP call blocks the event loop for 4–12 s (DNS + TLS + API roundtrip).
    # Running it in a thread keeps the webhook response fast and within Twilio's
    # 15-second deadline.
    import asyncio
    async def _start_recording():
        try:
            await asyncio.to_thread(
                _twilio.calls(call_sid).recordings.create,
                recording_status_callback="/webhook/recording-status",
                recording_status_callback_method="POST",
                recording_status_callback_event=["completed"],
            )
            log.info("recording_started", call_sid=call_sid)
        except Exception as e:
            log.warning("recording_start_failed", call_sid=call_sid, error=str(e))
    asyncio.create_task(_start_recording())

    return Response(content=_gather_response(_GREETING), media_type="application/xml")


@router.post("/webhook/gather", dependencies=[Depends(validate_twilio_webhook)])
async def gather_speech(request: Request):
    """Receive Twilio speech transcript, run LangGraph, speak response."""
    form        = await request.form()
    call_sid    = form.get("CallSid", "")
    transcript  = form.get("SpeechResult", "").strip()
    confidence  = form.get("Confidence", "0")

    log.info("speech_received", call_sid=call_sid,
             transcript=transcript, confidence=confidence)

    if not transcript:
        return Response(
            content=_gather_response(_TIMEOUT_MSG),
            media_type="application/xml",
        )

    add_call_turn(call_sid, "human", transcript)

    try:
        # Only pass the new message — LangGraph checkpointer owns auth_step,
        # pii_collected, candidate_party, etc. across turns.
        result = await _graph_module.cno_graph.ainvoke(
            {"messages": [HumanMessage(content=transcript)], "call_sid": call_sid},
            config={"configurable": {"thread_id": call_sid}},
        )

        tts_text     = result.get("tts_text", "")
        transfer_to  = result.get("transfer_to", "")
        current_node = result.get("current_node", "")
        intent       = result.get("current_intent", "")

        update_call_metadata(call_sid, result)
        add_call_turn(call_sid, "bot", tts_text, intent=intent, node=current_node)
        log_event(call_sid, "tts_sent", node=current_node, intent=intent, chars=len(tts_text))

        if transfer_to:
            end_call(call_sid)
            log_event(call_sid, "call_transfer", to=transfer_to)
            response = VoiceResponse()
            if tts_text:
                response.say(tts_text, voice="Polly.Joanna")
            response.dial(transfer_to)
            return Response(content=str(response), media_type="application/xml")

        if current_node == "goodbye":
            end_call(call_sid)
            log_event(call_sid, "call_end", reason="goodbye")
            response = VoiceResponse()
            response.say(tts_text or "Thank you for calling. Goodbye.", voice="Polly.Joanna")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

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


@router.post("/webhook/status", dependencies=[Depends(validate_twilio_webhook)])
async def call_status(request: Request):
    form = await request.form()
    log.info(
        "call_status",
        call_sid=form.get("CallSid"),
        status=form.get("CallStatus"),
        duration=form.get("CallDuration"),
    )
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
