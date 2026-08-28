"""
WebChat API — text-based interface to the same LangGraph IVR bot.
No Twilio involved; browser talks directly to the graph.

Endpoints:
  POST /chat/message          — send a message, get bot response
  GET  /chat/history/{sid}    — get full turn history for a session
  GET  /chat/sessions         — list all chat sessions
  POST /chat/reset/{sid}      — clear a session (start fresh)
"""
import uuid
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

import core.graph.graph as _graph_module
from services.conversation_store import add_chat_turn, get_chat, get_chats, ensure_chat

log = structlog.get_logger()
router = APIRouter(prefix="/chat", tags=["webchat"])

_GREETING = (
    "Hi! I'm your virtual insurance assistant. "
    "I can help you with your policy status, payment history, beneficiaries, loans, and more. "
    "Could you please provide your 10-digit phone number to get started?"
)


class ChatMessage(BaseModel):
    session_id: str = ""
    text: str


@router.post("/message")
async def chat_message(body: ChatMessage):
    """Send a message to the bot and receive a text response."""
    session_id = body.session_id.strip() or str(uuid.uuid4())
    text = body.text.strip()

    if not text:
        return JSONResponse({"error": "empty message"}, status_code=400)

    ensure_chat(session_id)

    # First turn — inject greeting so the graph has context
    chat = get_chat(session_id)
    is_first = len(chat.get("turns", [])) == 0

    if is_first:
        # Record the greeting as a bot turn so conversation history looks complete
        add_chat_turn(session_id, "bot", _GREETING, node="greeting")

    # Record human turn
    add_chat_turn(session_id, "human", text)

    try:
        result = await _graph_module.cno_graph.ainvoke(
            {"messages": [HumanMessage(content=text)]},
            config={"configurable": {"thread_id": f"chat_{session_id}"}},
        )

        bot_text    = result.get("tts_text") or "I'm sorry, I didn't understand that. Could you please try again?"
        intent      = result.get("current_intent", "")
        node        = result.get("current_node", "")
        is_goodbye  = node == "goodbye"
        transfer_to = result.get("transfer_to", "")

        add_chat_turn(session_id, "bot", bot_text, intent=intent, node=node)

        log.info("chat_turn", session_id=session_id, intent=intent, node=node)

        return JSONResponse({
            "session_id":  session_id,
            "response":    bot_text,
            "intent":      intent,
            "node":        node,
            "is_goodbye":  is_goodbye,
            "transfer_to": transfer_to,
            "greeting":    _GREETING if is_first else None,
        })

    except Exception as e:
        import traceback
        log.error("chat_error", session_id=session_id, error=str(e), traceback=traceback.format_exc())
        err = "I'm having trouble right now. Please try again in a moment."
        add_chat_turn(session_id, "bot", err, node="error")
        return JSONResponse({"session_id": session_id, "response": err, "error": str(e)}, status_code=200)


@router.get("/history/{session_id}")
async def chat_history(session_id: str):
    chat = get_chat(session_id)
    if not chat:
        return JSONResponse({"turns": [], "greeting": _GREETING})
    return JSONResponse(chat)


@router.get("/sessions")
async def list_sessions():
    sessions = get_chats()
    summary = [
        {
            "session_id": s["session_id"],
            "started_at": s["started_at"],
            "turns":      len(s["turns"]),
            "preview":    next((t["text"][:60] for t in s["turns"] if t["role"] == "human"), ""),
        }
        for s in sessions
    ]
    return JSONResponse({"sessions": summary})


@router.post("/reset/{session_id}")
async def reset_session(session_id: str):
    """Clear chat history and reset graph state for a session."""
    from services.conversation_store import _chats
    _chats.pop(session_id, None)
    # Clear LangGraph checkpoint for this thread
    try:
        from core.graph.graph import _checkpointer
        config = {"configurable": {"thread_id": f"chat_{session_id}"}}
        # MemorySaver doesn't expose a delete API; just remove from storage dict
        thread_key = f"chat_{session_id}"
        if hasattr(_checkpointer, "storage"):
            _checkpointer.storage.pop(thread_key, None)
    except Exception:
        pass
    return JSONResponse({"status": "reset", "session_id": session_id})
