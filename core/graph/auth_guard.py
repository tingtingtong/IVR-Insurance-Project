"""
Authentication guard for service nodes.

Each restricted service node calls ensure_authenticated() at its entry point.
Auth is enforced as a guard at the boundary of each service node instead of
as a separate routing node in the graph.

Return semantics of ensure_authenticated():
    (True,  None)        → fully authenticated; service node may proceed normally.
    (True,  auth_state)  → auth completed THIS turn; proceed AND merge auth_state
                           into the service node's return via merge_auth_state().
    (False, auth_state)  → auth still in progress; return auth_state directly as
                           the node's response for this turn.
"""
from core.graph.state import CNOState
from core.graph.nodes.auth import auth_node
from utils.call_logger import log_event


async def ensure_authenticated(
    state: CNOState,
    node_name: str,
) -> tuple[bool, dict | None]:
    """
    Guard called at the entry of each restricted service node.

    Args:
        state:     current LangGraph state
        node_name: the calling service node's graph name (e.g. "policy", "loan")
                   — stored as active_flow so the router routes back here while
                   auth is in progress.
    """
    authenticated  = state.get("authenticated", False)
    caller_persona = state.get("caller_persona", "")
    call_sid       = state.get("call_sid", "unknown")

    # Fully authenticated with persona — green light for the service node
    if authenticated and caller_persona:
        log_event(call_sid, "auth_guard", node=node_name, result="already_authenticated",
                  persona=caller_persona)
        return (True, None)

    log_event(call_sid, "auth_guard", node=node_name, result="auth_needed",
              auth_step=state.get("auth_step", "collecting_phone"))

    # Auth needed — run auth node to advance the state machine one step
    auth_result = await auth_node(state)

    # Did persona identification complete this turn?
    just_completed = bool(auth_result.get("caller_persona"))

    if just_completed:
        # "other" callers cannot access restricted policy data — the auth node
        # already generated the appropriate "I can connect you with a rep" TTS.
        if auth_result.get("caller_persona") == "other":
            auth_result["active_flow"] = ""
            log_event(call_sid, "auth_guard", node=node_name, result="persona_other_blocked")
            return (False, auth_result)
        # Normal completion — service node may proceed immediately (same turn)
        log_event(call_sid, "auth_guard", node=node_name, result="auth_just_completed",
                  persona=auth_result.get("caller_persona", ""))
        return (True, auth_result)

    # Auth still in progress — keep active_flow pointing here so the router
    # routes the next PII utterance back to this service node.
    auth_result["active_flow"] = node_name
    log_event(call_sid, "auth_guard", node=node_name, result="auth_in_progress",
              auth_step=auth_result.get("auth_step", ""))
    return (False, auth_result)


def apply_auth_state(state: dict, auth_state: dict | None) -> dict:
    """
    Merge auth completion state into a local working copy of state.

    Called when ensure_authenticated() returns (True, auth_state): the auth_node
    ran inside this turn and produced state updates (customer, access_token, etc.)
    that haven't been applied to the LangGraph state yet.  Service nodes read
    customer / access_token from this merged dict rather than from `state` directly.
    """
    return state if not auth_state else {**state, **auth_state}


def merge_auth_state(auth_state: dict | None, service_result: dict) -> dict:
    """
    Merge auth completion state with the service node's result dict.

    When auth just completed (True, auth_state) the caller should hear the
    auth "Thank you, {name}." followed immediately by the service response.
    auth_state fields that aren't overridden by service_result are preserved
    (e.g. authenticated, caller_persona, customer).
    """
    if not auth_state:
        return service_result

    auth_tts    = auth_state.get("tts_text", "")
    service_tts = service_result.get("tts_text", "")
    combined    = f"{auth_tts} {service_tts}".strip() if auth_tts else service_tts

    return {
        **{k: v for k, v in auth_state.items() if k not in service_result},
        **service_result,
        "tts_text": combined,
    }
