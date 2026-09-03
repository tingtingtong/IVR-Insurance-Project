import aiohttp
import jwt
import time
import structlog
from config import settings

_log = structlog.get_logger()

ACH_AUTHORIZATION_SCRIPT = (
    "I'm now recording your authorization. "
    "By saying 'I authorize', you are authorizing insuranceCompany "
    "to initiate a one-time electronic funds transfer from the bank account you provided. "
    "This authorization will remain in effect until you revoke it. "
    "Do you authorize this transaction?"
)


def _generate_jwt(policy_number: str, amount: float) -> str:
    payload = {
        "policyNumber": policy_number,
        "amount": amount,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,  # 5-minute expiry
    }
    return jwt.encode(payload, settings.cno_jwt_secret, algorithm="HS256")


async def process_card_payment(
    policy_number: str,
    access_token: str,
    amount: float,
    card_number: str,
    expiry: str,
    cvv: str,
) -> dict:
    """
    DEBIT_CREDIT_CARD_PAYMENT — JWT-auth integration flow.
    Card data arrives via Twilio DTMF — never passed through LLM.
    """
    url = f"{settings.cno_api_base_url}/payment/card"
    jwt_token = _generate_jwt(policy_number, amount)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Payment-JWT": jwt_token,
        "Content-Type":  "application/json",
    }
    payload = {
        "PolicyNumber": policy_number,
        "Amount": amount,
        "CardNumber": card_number,
        "ExpiryDate": expiry,
        "CVV": cvv,
    }
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status in (200, 201):
                    _log.info("api_card_payment", policy=policy_number[:3] + "****",
                              status=resp.status, latency_ms=latency)
                    return {"success": True, "confirmation": body.get("ConfirmationNumber", ""), "error": ""}
                _log.warning("api_card_payment_failed", status=resp.status,
                             error=str(body)[:100], latency_ms=latency)
                return {"success": False, "confirmation": "", "error": str(body)}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_card_payment_error", error=str(e)[:100], latency_ms=latency)
        return {"success": False, "confirmation": "", "error": str(e)}


async def process_ach_payment(
    policy_number: str,
    access_token: str,
    amount: float,
    routing_number: str,
    account_number: str,
    account_type: str = "checking",
) -> dict:
    """ACH / Bank payment — requires ACH authorization script read first."""
    url = f"{settings.cno_api_base_url}/payment/ach"
    jwt_token = _generate_jwt(policy_number, amount)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Payment-JWT": jwt_token,
        "Content-Type":  "application/json",
    }
    payload = {
        "PolicyNumber":  policy_number,
        "Amount":        amount,
        "RoutingNumber": routing_number,
        "AccountNumber": account_number,
        "AccountType":   account_type,
    }
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status in (200, 201):
                    _log.info("api_ach_payment", policy=policy_number[:3] + "****",
                              status=resp.status, latency_ms=latency)
                    return {"success": True, "confirmation": body.get("ConfirmationNumber", ""), "error": ""}
                _log.warning("api_ach_payment_failed", status=resp.status,
                             error=str(body)[:100], latency_ms=latency)
                return {"success": False, "confirmation": "", "error": str(body)}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_ach_payment_error", error=str(e)[:100], latency_ms=latency)
        return {"success": False, "confirmation": "", "error": str(e)}


def get_ach_script() -> str:
    """Returns verbatim ACH authorization script — never paraphrase."""
    return ACH_AUTHORIZATION_SCRIPT
