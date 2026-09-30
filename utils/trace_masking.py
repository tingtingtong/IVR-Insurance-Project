"""
PII masking for LangSmith traces.

LangGraph state is traced to LangSmith in full. This module installs a
LangSmith client whose hide_inputs / hide_outputs hooks drop sensitive state
keys and run utils.pii_redactor.redact() over every remaining string, so card
numbers, CVVs, tokens and caller identity never leave the process.
"""
from utils.pii_redactor import redact

REDACTED = "[REDACTED]"

# State / payload keys whose values are dropped entirely (matched case-insensitively).
SENSITIVE_KEYS = frozenset(k.lower() for k in (
    # auth + identity
    "access_token", "customer", "finalized_party", "candidate_party",
    "pii_collected", "caller_name", "authenticated_name",
    "dob", "date_of_birth", "ssn", "phone", "phonenumber", "phone_number",
    "firstname", "lastname", "first_name", "last_name", "address", "addresses",
    # payment
    "card_number", "card_groups", "cvv", "expiry", "cardholder_name",
    "account_number", "routing_number", "account_name",
    "new_phone", "new_address", "last_confirmation_tts",
))

_MAX_DEPTH = 40


def mask_pii(value, _depth: int = 0):
    """Return a copy of value with sensitive keys dropped and strings redacted."""
    if _depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
                else mask_pii(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(mask_pii(v, _depth + 1) for v in value)
    # LangChain messages and other pydantic models: copy with masked fields
    if hasattr(value, "model_dump") and hasattr(value, "model_copy"):
        try:
            fields = value.model_dump()
            masked = mask_pii(fields, _depth + 1)
            return value.model_copy(update={k: masked[k] for k in fields})
        except Exception:
            return REDACTED
    return value


def install_masked_langsmith_client() -> None:
    """Replace LangSmith's cached global client with one that masks PII.

    LangChain's tracer and @traceable both obtain their client from
    langsmith.run_trees.get_cached_client(), so this must run before the first
    traced call.
    """
    from langsmith import Client
    import langsmith.run_trees as run_trees

    run_trees._CLIENT = Client(hide_inputs=mask_pii, hide_outputs=mask_pii)
