import time
from core.graph.state import CNOState
from utils.call_logger import log_event


async def goodbye_node(state: CNOState) -> dict:
    t0 = time.time()
    call_sid = state.get("call_sid", "unknown")
    log_event(call_sid, "node_enter", node="goodbye")
    tts = "Thank you for calling. Have a great day. Goodbye."
    log_event(call_sid, "node_exit", node="goodbye",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return {
        "tts_text":     tts,
        "transfer_to":  "",
        "active_flow":  "",
        "current_node": "goodbye",
    }
