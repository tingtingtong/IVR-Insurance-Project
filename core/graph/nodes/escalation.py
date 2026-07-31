import time
from core.graph.state import CNOState
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.call_logger import log_event


async def escalation_node(state: CNOState) -> dict:
    """
    Transfer the caller to a live agent.
    Sets transfer_to so the WebSocket handler triggers Twilio <Dial>.
    """
    t0        = time.time()
    call_sid  = state.get("call_sid", "unknown")
    auth_step = state.get("auth_step", "")

    if auth_step == "failed":
        tts = PROMPTS["escalation"]["max_attempts"]
    else:
        tts = PROMPTS["escalation"]["caller_request"]

    tts += f" {PROMPTS['closing']['transfer_intro']}"

    log_event(call_sid, "node_exit", node="escalation",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return {
        "tts_text":    tts,
        "transfer_to": settings.twilio_agent_phone_number,
        "current_node": "escalation",
        "active_flow":  "",
    }
