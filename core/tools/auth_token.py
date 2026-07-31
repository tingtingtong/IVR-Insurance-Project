import aiohttp
import structlog
from config import settings

log = structlog.get_logger()


async def acquire_access_token(party_key: str, company_code: str) -> str:
    """
    POST /auth/token — exchange a verified party key + company code for a session
    access token used as the Bearer credential on all subsequent policy/payment/loan
    API calls.

    Returns the token string on success, or "" on failure.
    Callers must treat an empty return as an escalation condition — do not proceed
    with empty tokens since every downstream API call will reject the request.
    """
    if not party_key or not company_code:
        log.warning("access_token_missing_params",
                    has_party_key=bool(party_key), has_company_code=bool(company_code))
        return ""

    url = f"{settings.cno_api_base_url}/auth/token"
    headers = {
        "Authorization": f"Bearer {settings.cno_api_key}",
        "Content-Type":  "application/json",
    }
    payload = {
        "PartyCalrKeyCode": party_key,
        "CompanyCode":      company_code,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.json()
                if resp.status == 200:
                    token = body.get("AccessToken", "")
                    if token:
                        log.info("access_token_acquired", party_key=party_key)
                    else:
                        log.warning("access_token_empty_response")
                    return token
                log.warning("access_token_request_failed",
                            status=resp.status, body=str(body)[:200])
                return ""
    except Exception as e:
        log.error("access_token_exception", error=str(e))
        return ""
