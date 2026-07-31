import time
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from core.graph.state import CNOState
from core.prompts.system_prompt import CNO_SYSTEM_PROMPT
from services.rag import search_knowledge
from config import settings  # feature flags: enable_rag, faq_fallback_to_escalate
from utils.call_logger import log_event

_llm = ChatGroq(
    model=settings.groq_model,
    temperature=0.4,
    api_key=settings.groq_api_key,
    max_tokens=200,
)


async def faq_node(state: CNOState) -> dict:
    """
    RAG-grounded FAQ node.
    Retrieves relevant knowledge chunks, generates a grounded voice response.
    """
    messages = state.get("messages", [])

    last_human = ""
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            last_human = msg.content.strip()
            break

    call_sid = state.get("call_sid", "unknown")

    if not last_human:
        return {"tts_text": "I'm sorry, I didn't catch that. What can I help you with?", "current_node": "faq", "active_flow": ""}

    # Retrieve relevant knowledge chunks (skip if RAG disabled via feature flag)
    t0 = time.time()
    context = await search_knowledge(last_human, k=3) if settings.enable_rag else ""
    log_event(call_sid, "rag_retrieved",
              chunks=len(context.split('\n\n')) if context else 0,
              latency_ms=int((time.time() - t0) * 1000))

    # Explicit guard: caller is authenticated — never ask for identity verification here.
    # CNO_SYSTEM_PROMPT contains "AUTHENTICATION — ALWAYS FIRST"; when RAG returns 0 chunks
    # the ungrounded LLM defaults to asking for credentials. The override below prevents that.
    AUTH_OVERRIDE = (
        "\n\nCRITICAL: The caller has ALREADY been authenticated. "
        "Do NOT ask for phone number, policy number, date of birth, name, or any other "
        "verification. Respond only to the caller's question."
    )

    if context:
        grounding = (
            "Use ONLY the following information to answer. Do not add anything not in the context."
            "\n\nContext:\n" + context
        )
    else:
        # No RAG match — canned response or escalate depending on feature flag.
        if settings.faq_fallback_to_escalate:
            tts = "I'm sorry, I don't have specific information on that. Let me connect you with a representative who can help."
        else:
            tts = "I'm sorry, I don't have specific information about that. Is there anything else I can help you with, or would you like me to connect you with a representative?"
        log_event(call_sid, "faq_rag_miss", node="faq", query=last_human[:80])
        log_event(call_sid, "llm_response", node="faq", latency_ms=0, chars=len(tts))
        return {
            "tts_text":     tts,
            "current_node": "faq", "active_flow": "",
            "messages":     [AIMessage(content=tts)],
        }

    t1 = time.time()
    response = await _llm.ainvoke([
        SystemMessage(content=CNO_SYSTEM_PROMPT + AUTH_OVERRIDE + "\n\n" + grounding),
        *messages[-4:],
        HumanMessage(content=f"Answer this caller question in 1-2 sentences for voice: {last_human}"),
    ])

    tts = response.content.strip()
    log_event(call_sid, "llm_response", node="faq",
              latency_ms=int((time.time() - t1) * 1000), chars=len(tts))
    return {
        "tts_text":    tts,
        "current_node": "faq", "active_flow": "",
        "messages":    [AIMessage(content=tts)],
    }
