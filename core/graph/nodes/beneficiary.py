import time
import aiohttp
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.prompts.system_prompt import CNO_SYSTEM_PROMPT
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.call_logger import log_event

_llm = ChatGroq(model=settings.groq_model, temperature=0.3,
                api_key=settings.groq_api_key, max_tokens=200)

# GROUP G2 — relationship restriction verbiage (read verbatim, never paraphrase)
RELATIONSHIP_RESTRICTION = (
    "Based on your relationship to the policy, I can confirm beneficiary information "
    "for the primary insured only. If you need to make changes, I'll need to transfer you."
)


async def beneficiary_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "beneficiary")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0            = time.time()
    customer      = state.get("customer", {})
    access_token  = state.get("access_token", "")
    messages      = state.get("messages", [])
    call_sid      = state.get("call_sid", "unknown")
    policy_number = customer.get("policyNumber", "")

    url = f"{settings.cno_api_base_url}/beneficiary/inquiry"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"PolicyNumber": policy_number},
                                    headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                if resp.status != 200:
                    return merge_auth_state(auth_state, {"tts_text": PROMPTS["escalation"]["error"], "current_node": "beneficiary", "active_flow": ""})
    except Exception:
        return merge_auth_state(auth_state, {"tts_text": PROMPTS["escalation"]["error"], "current_node": "beneficiary", "active_flow": ""})

    beneficiaries = body.get("Beneficiaries", [])
    context = _format_beneficiaries(beneficiaries)

    response = await _llm.ainvoke([
        SystemMessage(content=CNO_SYSTEM_PROMPT),
        *messages[-4:],
        HumanMessage(content=f"Beneficiary data:\n{context}\nGenerate a concise voice response. Append the relationship restriction notice."),
    ])
    tts = response.content.strip() + f" {RELATIONSHIP_RESTRICTION}"
    log_event(call_sid, "node_exit", node="beneficiary",
              latency_ms=int((time.time() - t0) * 1000), chars=len(tts))
    return merge_auth_state(auth_state, {"tts_text": tts, "current_node": "beneficiary", "active_flow": "", "messages": [AIMessage(content=tts)]})


def _format_beneficiaries(beneficiaries: list) -> str:
    if not beneficiaries:
        return "No beneficiaries on file."
    lines = []
    for b in beneficiaries:
        name  = f"{b.get('FirstName', '')} {b.get('LastName', '')}".strip()
        rel   = b.get("Relationship", "")
        pct   = b.get("Percentage", "")
        lines.append(f"{name} ({rel}) — {pct}%")
    return "\n".join(lines)
