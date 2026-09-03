import time
import difflib
import aiohttp
import structlog
from config import settings

_log = structlog.get_logger()


async def party_search(
    phone: str = "",
    policy_number: str = "",
    date_of_birth: str = "",
    first_name: str = "",
    last_name: str = "",
    zipcode: str = "",
) -> dict:
    """
    PARTY_SEARCH API — find a party record by PII fields.
    Returns { success: bool, parties: [...], error: str }
    """
    payload = {}
    if phone:           payload["PhoneNumber"]   = phone
    if policy_number:   payload["PolicyNumber"]  = policy_number
    if date_of_birth:   payload["DOB"]           = date_of_birth
    if first_name:      payload["FirstName"]     = first_name
    if last_name:       payload["LastName"]       = last_name
    if zipcode:         payload["ZipCode"]        = zipcode

    url = f"{settings.cno_api_base_url}/party/search"
    headers = {
        "Authorization": f"Bearer {settings.cno_api_key}",
        "Content-Type":  "application/json",
    }

    t0 = time.time()
    search_by = "phone" if phone else "policy" if policy_number else "name" if first_name else "unknown"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status == 200:
                    parties = body.get("SearchParties", [])
                    _log.info("api_party_search", search_by=search_by, status=200,
                              party_count=len(parties), latency_ms=latency)
                    return {"success": True, "parties": parties, "error": ""}
                else:
                    error_block = body.get("ErrorBlock", [{}])
                    error_msg = error_block[0].get("ErrorMessage", "Unknown error") if error_block else str(body)
                    _log.warning("api_party_search_failed", search_by=search_by, status=resp.status,
                                 error=error_msg[:100], latency_ms=latency)
                    return {"success": False, "parties": [], "error": error_msg}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_party_search_error", search_by=search_by, error=str(e)[:100], latency_ms=latency)
        return {"success": False, "parties": [], "error": str(e)}


def validate_pii_match(party: dict, pii_collected: dict) -> list[str]:
    """
    Check which PII fields from pii_collected match the API party record.
    Returns list of matched field names.
    """
    matched = []

    if _phone_matches(party, pii_collected):
        matched.append("phoneNumber")
    if _policy_matches(party, pii_collected):
        matched.append("policyNumber")
    if _dob_matches(party, pii_collected):
        matched.append("dateOfBirth")
    if _name_fuzzy_matches(party, pii_collected, threshold=1.0):  # exact for legacy caller
        matched.append("insuredName")
    if pii_collected.get("zipcode") and pii_collected["zipcode"] == (
        (party.get("Addresses") or [{}])[0].get("Zip", "")
    ):
        matched.append("zipcode")

    return matched


def check_auth_success(party: dict, pii_collected: dict, fuzzy_threshold: float = 0.80) -> bool:
    """
    Returns True if any valid 2-factor auth pair is satisfied:
      phone + DOB  |  phone + name (fuzzy)  |  policy + DOB  |  policy + name (fuzzy)
    """
    phone_ok  = _phone_matches(party, pii_collected)
    policy_ok = _policy_matches(party, pii_collected)
    dob_ok    = _dob_matches(party, pii_collected)
    name_ok   = _name_fuzzy_matches(party, pii_collected, fuzzy_threshold)

    return (
        (phone_ok  and dob_ok)  or
        (phone_ok  and name_ok) or
        (policy_ok and dob_ok)  or
        (policy_ok and name_ok)
    )


# ── Match helpers ─────────────────────────────────────────────────────────────

def _phone_matches(party: dict, pii: dict) -> bool:
    phone = pii.get("phoneNumber", "")
    if not phone:
        return False
    api_phone = (party.get("PhoneNumbers") or [{}])[0].get("PhoneNumber", "")
    return phone == api_phone


def _policy_matches(party: dict, pii: dict) -> bool:
    policy = pii.get("policyNumber", "")
    if not policy:
        return False
    policy_nums = [p.get("PolicyNumber", "") for p in (party.get("Policies") or [])]
    return policy in policy_nums


def _dob_matches(party: dict, pii: dict) -> bool:
    dob = pii.get("dateOfBirth", "")
    if not dob:
        return False
    return dob == party.get("DOB", "")


def _name_fuzzy_matches(party: dict, pii: dict, threshold: float) -> bool:
    first = (pii.get("firstName") or "").lower().strip()
    last  = (pii.get("lastName")  or "").lower().strip()
    if not first or not last:
        return False
    api_first = (party.get("FirstName") or "").lower().strip()
    api_last  = (party.get("LastName")  or "").lower().strip()
    if not api_first or not api_last:
        return False
    fr = difflib.SequenceMatcher(None, first, api_first).ratio()
    lr = difflib.SequenceMatcher(None, last,  api_last).ratio()
    return fr >= threshold and lr >= threshold
