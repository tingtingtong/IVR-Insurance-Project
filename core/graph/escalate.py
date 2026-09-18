"""Single shape for live-agent transfer so Twilio always hangs up (never Gather)."""
from config import settings


def transfer_now(tts: str, **extra) -> dict:
    """Return graph state that ends the call: speak TTS, Dial if we have a number, Hangup."""
    result = {
        "tts_text":       tts,
        "transfer_to":    settings.twilio_agent_phone_number or "",
        "current_node":   "escalation",
        "current_intent": "escalate",
        "active_flow":    "",
    }
    result.update(extra)
    return result


def is_terminal(result: dict | None) -> bool:
    """True when the graph wants the PSTN/browser call to end."""
    if not result:
        return False
    if result.get("current_node") in ("escalation", "goodbye"):
        return True
    if result.get("current_intent") == "escalate":
        return True
    if result.get("transfer_to"):
        return True
    return False
