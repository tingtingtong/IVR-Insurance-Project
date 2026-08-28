import time
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.tools.holding_inquiry import loan_inquiry
from core.prompts.system_prompt import CNO_SYSTEM_PROMPT
from core.prompts.retry_prompts import PROMPTS
from core.llm_factory import get_llm
from config import settings
from utils.call_logger import log_event

_llm = get_llm(temperature=0.3, max_tokens=150)


async def loan_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "loan")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0            = time.time()
    customer      = state.get("customer", {})
    access_token  = state.get("access_token", "")
    messages      = state.get("messages", [])
    call_sid      = state.get("call_sid", "unknown")
    policy_number = customer.get("policyNumber", "")

    result = await loan_inquiry(policy_number, access_token)
    if not result["success"]:
        return merge_auth_state(auth_state, {"tts_text": PROMPTS["escalation"]["error"], "current_node": "loan", "active_flow": ""})

    data = result["data"]
    balance  = data.get("LoanBalance", "0")
    interest = data.get("AccruedInterest", "0")
    payoff   = data.get("PayoffAmount", "0")
    context  = f"Loan balance: ${balance}. Accrued interest: ${interest}. Payoff amount: ${payoff}."

    response = await _llm.ainvoke([
        SystemMessage(content=CNO_SYSTEM_PROMPT),
        *messages[-4:],
        HumanMessage(content=f"Loan data: {context}\nGenerate a concise voice response."),
    ])
    tts = response.content.strip()
    log_event(call_sid, "node_exit", node="loan",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return merge_auth_state(auth_state, {"tts_text": tts, "current_node": "loan", "active_flow": "", "messages": [AIMessage(content=tts)]})
