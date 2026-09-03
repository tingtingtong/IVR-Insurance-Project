import time
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.tools.holding_inquiry import payment_history
from core.prompts.system_prompt import CNO_SYSTEM_PROMPT
from core.prompts.retry_prompts import PROMPTS
from core.llm_factory import get_llm
from config import settings
from utils.date_utils import format_date_natural
from utils.call_logger import log_event

_llm = get_llm(temperature=0.3, max_tokens=200)


async def payment_node(state: CNOState) -> dict:
    """Payment history / payment status check (merged use case)."""
    ok, auth_state = await ensure_authenticated(state, "payment")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0           = time.time()
    customer     = state.get("customer", {})
    access_token = state.get("access_token", "")
    messages     = state.get("messages", [])
    call_sid     = state.get("call_sid", "unknown")

    log_event(call_sid, "node_enter", node="payment")

    policy_number = customer.get("policyNumber", "")
    if not policy_number:
        return merge_auth_state(auth_state, {
            "tts_text":    PROMPTS["escalation"]["error"],
            "current_node": "payment", "active_flow": "",
        })

    result = await payment_history(policy_number, access_token)
    log_event(call_sid, "api_call", node="payment", api="payment_history",
              success=result["success"], txn_count=len(result.get("transactions", [])))

    if not result["success"]:
        return merge_auth_state(auth_state, {
            "tts_text":    PROMPTS["escalation"]["error"],
            "current_node": "payment", "active_flow": "",
        })

    transactions = result["transactions"]
    context = _format_transactions(transactions)

    t_llm = time.time()
    response = await _llm.ainvoke([
        SystemMessage(content=CNO_SYSTEM_PROMPT),
        *messages[-4:],
        HumanMessage(content=f"Payment history:\n{context}\n\nGenerate a concise voice response. Include the payment disclosure at the end."),
    ])

    tts = response.content.strip()
    log_event(call_sid, "llm_response", node="payment",
              latency_ms=int((time.time() - t_llm) * 1000), chars=len(tts))
    # Append mandatory payment posting disclosure (GROUP G4)
    tts += f" {PROMPTS['payment_disclosure']}"

    log_event(call_sid, "node_exit", node="payment",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return merge_auth_state(auth_state, {
        "tts_text":    tts,
        "current_node": "payment", "active_flow": "",
        "messages":    [AIMessage(content=tts)],
    })


def _format_transactions(transactions: list) -> str:
    if not transactions:
        return "No recent transactions found."
    lines = []
    for i, txn in enumerate(transactions[:3], 1):
        amount = txn.get("Amount", "")
        date   = format_date_natural(txn.get("Date", ""))
        status = txn.get("Status", "")
        lines.append(f"Transaction {i}: ${amount} on {date} — {status}")
    return "\n".join(lines)
