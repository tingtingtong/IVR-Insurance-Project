import time
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.tools.holding_inquiry import holding_inquiry
from core.prompts.system_prompt import CNO_SYSTEM_PROMPT
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.date_utils import format_date_natural
from utils.call_logger import log_event

_llm = ChatGroq(
    model=settings.groq_model,
    temperature=0.3,
    api_key=settings.groq_api_key,
    max_tokens=200,
)


async def policy_node(state: CNOState) -> dict:
    """Policy information + premium amount (merged use case)."""
    ok, auth_state = await ensure_authenticated(state, "policy")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0           = time.time()
    customer     = state.get("customer", {})
    access_token = state.get("access_token", "")
    messages     = state.get("messages", [])
    call_sid     = state.get("call_sid", "unknown")

    policy_number = customer.get("policyNumber", "")
    if not policy_number:
        return merge_auth_state(auth_state, {
            "tts_text":    "I'm sorry, I wasn't able to find your policy number. Let me transfer you to a representative.",
            "transfer_to": "",
            "current_node": "policy", "active_flow": "",
        })

    result = await holding_inquiry(policy_number, access_token)

    if not result["success"]:
        return merge_auth_state(auth_state, {
            "tts_text":    PROMPTS["escalation"]["error"],
            "current_node": "policy", "active_flow": "",
        })

    data = result["data"]
    context = _format_policy_context(data, customer)

    # Use LLM to generate a natural voice response from the API data
    response = await _llm.ainvoke([
        SystemMessage(content=CNO_SYSTEM_PROMPT),
        *messages[-4:],  # last 2 turns for context
        HumanMessage(content=f"Policy data retrieved:\n{context}\n\nGenerate a concise voice response."),
    ])

    tts = response.content.strip()
    log_event(call_sid, "node_exit", node="policy",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return merge_auth_state(auth_state, {
        "tts_text":    tts,
        "current_node": "policy", "active_flow": "",
        "messages":    [AIMessage(content=tts)],
    })


def _format_policy_context(data: dict, customer: dict) -> str:
    lines = []
    status        = data.get("PolicyStatus", "")
    premium       = data.get("PremiumAmount", "")
    paid_to_date  = data.get("PaidToDate", "")
    billing_mode  = data.get("BillingMode", "")
    insured_name  = f"{customer.get('firstName', '')} {customer.get('lastName', '')}".strip()

    if insured_name: lines.append(f"Insured: {insured_name}")
    if status:       lines.append(f"Policy status: {status}")
    if premium:      lines.append(f"Premium amount: ${premium}")
    if paid_to_date:
        formatted = format_date_natural(paid_to_date)
        lines.append(f"Paid to date: {formatted}")
    if billing_mode: lines.append(f"Billing mode: {billing_mode}")
    return "\n".join(lines)
