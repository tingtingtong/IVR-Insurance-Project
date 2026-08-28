"""LLM-based name extraction and fuzzy matching for IVR auth."""
import difflib
from langchain_core.messages import SystemMessage, HumanMessage
from core.llm_factory import get_llm

_llm = get_llm(temperature=0, max_tokens=50)

_SYSTEM = (
    "Extract the person's first and last name from the caller's utterance.\n"
    "Reply with exactly: FIRSTNAME|LASTNAME\n"
    "If you cannot identify both a first AND last name, reply: UNKNOWN|UNKNOWN\n"
    "Examples:\n"
    "  'My name is John Doe'           → John|Doe\n"
    "  'jane smith'                    → Jane|Smith\n"
    "  'the insured is Mary Jo Williams' → Mary|Williams\n"
    "  'hello'                         → UNKNOWN|UNKNOWN"
)


async def extract_name(utterance: str) -> tuple[str, str]:
    """
    LLM-extract first and last name from a spoken utterance.
    Returns (first, last) or ("", "") if extraction fails.
    """
    if not utterance.strip():
        return ("", "")
    response = await _llm.ainvoke([
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=utterance),
    ])
    raw = response.content.strip()
    parts = raw.split("|")
    if len(parts) == 2:
        first, last = parts[0].strip(), parts[1].strip()
        if first.upper() not in ("", "UNKNOWN") and last.upper() not in ("", "UNKNOWN"):
            return (first, last)
    return ("", "")


def name_matches_party(first: str, last: str, party: dict, threshold: float = 0.80) -> bool:
    """
    Fuzzy match first+last against party FirstName/LastName.
    Both names must independently meet the similarity threshold.
    """
    if not first or not last:
        return False
    api_first = (party.get("FirstName") or "").lower().strip()
    api_last  = (party.get("LastName")  or "").lower().strip()
    if not api_first or not api_last:
        return False
    first_ratio = difflib.SequenceMatcher(None, first.lower(), api_first).ratio()
    last_ratio  = difflib.SequenceMatcher(None, last.lower(),  api_last).ratio()
    return first_ratio >= threshold and last_ratio >= threshold
