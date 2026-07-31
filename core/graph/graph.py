from langgraph.graph import StateGraph, START, END  # noqa: F401 — END re-exported for edges
from langgraph.checkpoint.memory import MemorySaver

from core.graph.state import CNOState
from core.graph.nodes.router import router_node
from core.graph.nodes.policy import policy_node
from core.graph.nodes.payment import payment_node
from core.graph.nodes.otp import otp_node
from core.graph.nodes.loan import loan_node
from core.graph.nodes.beneficiary import beneficiary_node
from core.graph.nodes.contact import contact_node
from core.graph.nodes.document import document_node
from core.graph.nodes.privacy import privacy_node
from core.graph.nodes.faq import faq_node
from core.graph.nodes.escalation import escalation_node
from core.graph.nodes.goodbye import goodbye_node


def _route_after_router(state: CNOState) -> str:
    """Conditional routing based on current_intent set by router_node."""
    intent = state.get("current_intent", "faq")
    # Empty intent means router had no speech to classify (e.g. timeout or empty utterance
    # while already authenticated). Return END so twilio_voice.py re-prompts instead of
    # calling the FAQ node which would produce "How can I help you today?" as a greeting.
    if not intent:
        return END
    route_map = {
        "policy_info":    "policy",
        "payment":        "payment",
        "otp":            "otp",
        "loan":           "loan",
        "beneficiary":    "beneficiary",
        "goodbye":        "goodbye",
        "owner_change":   "escalation",   # escalate — not supported in IVR
        "document":       "document",
        "contact_change": "contact",
        "privacy":        "privacy",
        "faq":            "faq",
        "escalate":       "escalation",
    }
    return route_map.get(intent, "faq")


def build_graph() -> StateGraph:
    graph = StateGraph(CNOState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("router",      router_node)
    graph.add_node("policy",      policy_node)
    graph.add_node("payment",     payment_node)
    graph.add_node("otp",         otp_node)
    graph.add_node("loan",        loan_node)
    graph.add_node("beneficiary", beneficiary_node)
    graph.add_node("contact",     contact_node)
    graph.add_node("document",    document_node)
    graph.add_node("privacy",     privacy_node)
    graph.add_node("faq",         faq_node)
    graph.add_node("escalation",  escalation_node)
    graph.add_node("goodbye",     goodbye_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    graph.add_edge(START, "router")

    # ── Router → intent nodes ─────────────────────────────────────────────────
    graph.add_conditional_edges(
        "router",
        _route_after_router,
        {
            "policy":      "policy",
            "payment":     "payment",
            "otp":         "otp",
            "loan":        "loan",
            "beneficiary": "beneficiary",
            "contact":     "contact",
            "document":    "document",
            "privacy":     "privacy",
            "faq":         "faq",
            "escalation":  "escalation",
            "goodbye":     "goodbye",
            END:           END,  # empty-intent path — twilio_voice.py handles re-prompt
        },
    )

    # ── All service nodes → END (response returned to WebSocket handler) ──────
    for node in ("policy", "payment", "otp", "loan", "beneficiary", "goodbye",
                 "contact", "document", "privacy", "faq", "escalation"):
        graph.add_edge(node, END)

    return graph


# Module-level compiled graph — set to None until startup initializes it.
# Startup in main.py calls set_graph() after building with the chosen checkpointer.
cno_graph = None


def set_graph(compiled_graph) -> None:
    """Called from main.py startup to inject the compiled graph."""
    global cno_graph
    cno_graph = compiled_graph
