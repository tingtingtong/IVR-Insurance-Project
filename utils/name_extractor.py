"""Name extraction for IVR: labeled phrases, NATO/phonetic spelling, then LLM."""
import re
import difflib
from langchain_core.messages import SystemMessage, HumanMessage
from core.llm_factory import get_llm, ainvoke_limited

_llm = get_llm(temperature=0, max_tokens=60)

_NATO = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d",
    "echo": "e", "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i",
    "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l", "mike": "m",
    "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r",
    "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
    "whiskey": "w", "xray": "x", "x-ray": "x", "yankee": "y", "zulu": "z",
}

# "A for Alpha", "B as in Bravo", "C like Charlie"
_LETTER_FOR_WORD = re.compile(
    r"\b([a-z])\s+(?:for|as in|as|like)\s+([a-z][a-z\-]+)\b",
    re.IGNORECASE,
)
_WORD_FOR_LETTER = re.compile(
    r"\b([a-z][a-z\-]+)\s+(?:for|as in|as|like)\s+([a-z])\b",
    re.IGNORECASE,
)

_SYSTEM = (
    "Extract the caller's first and last name from a spoken IVR utterance.\n"
    "Reply with exactly: FIRSTNAME|LASTNAME\n"
    "If only one name is present, still return it and UNKNOWN for the other.\n"
    "If you cannot identify any name, reply: UNKNOWN|UNKNOWN\n"
    "Handle these patterns:\n"
    "  'John Smith' → John|Smith\n"
    "  'My name is John Smith' → John|Smith\n"
    "  'My first name is John, last name is Smith' → John|Smith\n"
    "  'Last name Smith, first name John' → John|Smith\n"
    "  'I am Jane Doe' → Jane|Doe\n"
    "  'J for Juliet O for Oscar H for Hotel N for November' → John|UNKNOWN\n"
    "  NATO/phonetic spelling of letters should be decoded into the name.\n"
    "Ignore filler words: uh, um, please, yeah, it's, this is.\n"
)


def decode_phonetics(utterance: str) -> str:
    """Turn NATO / 'A for Alpha' spelling into letters, keep other words."""
    if not utterance:
        return ""
    text = utterance

    def _letter_for(m: re.Match) -> str:
        letter, word = m.group(1).lower(), m.group(2).lower().replace("-", "")
        nato = _NATO.get(word)
        if nato and nato == letter:
            return nato.upper()
        if nato:
            return nato.upper()
        return letter.upper()

    def _word_for(m: re.Match) -> str:
        word, letter = m.group(1).lower().replace("-", ""), m.group(2).lower()
        nato = _NATO.get(word)
        if nato:
            return nato.upper()
        return letter.upper()

    text = _LETTER_FOR_WORD.sub(_letter_for, text)
    text = _WORD_FOR_LETTER.sub(_word_for, text)

    # Standalone NATO words → letters
    tokens = re.split(r"(\s+)", text)
    out = []
    for tok in tokens:
        key = tok.lower().replace("-", "")
        if key in _NATO:
            out.append(_NATO[key].upper())
        else:
            out.append(tok)
    text = "".join(out)

    # Collapse runs of single spelled letters into a word: "J O H N" → "JOHN"
    def _collapse(m: re.Match) -> str:
        letters = re.findall(r"[A-Za-z]", m.group(0))
        return "".join(letters)

    text = re.sub(r"\b(?:[A-Za-z](?:\s+|,)+){1,}[A-Za-z]\b", _collapse, text)
    return re.sub(r"\s+", " ", text).strip()


def parse_name_deterministic(utterance: str) -> tuple[str, str]:
    """Pull first/last from labeled phrases or a two-word name. No LLM."""
    if not utterance:
        return ("", "")
    text = decode_phonetics(utterance)
    u = text.strip()

    first = last = ""
    m = re.search(
        r"first\s+name(?:\s+is)?\s+([A-Za-z][A-Za-z'\-]*)",
        u, re.IGNORECASE,
    )
    if m:
        first = m.group(1)
    m = re.search(
        r"last\s+name(?:\s+is)?\s+([A-Za-z][A-Za-z'\-]*)",
        u, re.IGNORECASE,
    )
    if m:
        last = m.group(1)
    if first and last:
        return (first.title(), last.title())

    m = re.search(
        r"(?:my\s+name\s+is|i\s+am|i'm|this\s+is|it'?s|the\s+name\s+is|name\s+is)\s+"
        r"([A-Za-z][A-Za-z'\-]*)\s+([A-Za-z][A-Za-z'\-]*)",
        u, re.IGNORECASE,
    )
    if m:
        return (m.group(1).title(), m.group(2).title())

    # Bare two-token name after stripping leftover filler
    cleaned = re.sub(
        r"^(?:uh|um|yeah|yes|please|well)\s+",
        "", u, flags=re.IGNORECASE,
    ).strip(" .,")
    parts = [p for p in re.split(r"\s+", cleaned) if re.match(r"^[A-Za-z][A-Za-z'\-]*$", p)]
    if len(parts) >= 2:
        return (parts[0].title(), parts[-1].title())
    if len(parts) == 1 and len(parts[0]) >= 2:
        return (parts[0].title(), "")
    return (first.title() if first else "", last.title() if last else "")


async def extract_name(utterance: str) -> tuple[str, str]:
    """
    Extract first and last name. Deterministic parse first (labeled phrases,
    NATO spelling), then LLM if needed.
    Returns (first, last); either may be "" if unknown.
    """
    if not utterance or not utterance.strip():
        return ("", "")

    first, last = parse_name_deterministic(utterance)
    if first and last:
        return (first, last)

    decoded = decode_phonetics(utterance)
    try:
        response = await ainvoke_limited(_llm, [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=decoded or utterance),
        ])
        raw = (response.content or "").strip()
        parts = raw.split("|")
        if len(parts) >= 2:
            f, l = parts[0].strip(), parts[1].strip()
            if f.upper() not in ("", "UNKNOWN"):
                first = first or f.title()
            if l.upper() not in ("", "UNKNOWN"):
                last = last or l.title()
    except Exception:
        pass
    return (first, last)


def format_extracted_name(first: str, last: str) -> str:
    return " ".join(p for p in (first, last) if p).strip()


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
