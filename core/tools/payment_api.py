import jwt
import time
import structlog
from config import settings
from core.tools.http import post_json

_log = structlog.get_logger()

ACH_AUTHORIZATION_SCRIPT = (
    "I'm now recording your authorization. "
    "By saying 'I authorize', you are authorizing US Insurance Company "
    "to initiate a one-time electronic funds transfer from the bank account you provided. "
    "This authorization will remain in effect until you revoke it. "
    "Do you authorize this transaction?"
)


def _generate_jwt(policy_number: str, amount: float) -> str:
    secret = settings.cno_jwt_secret
    if not secret:
        if settings.is_prod:
            raise RuntimeError("CNO_JWT_SECRET is required in production")
        _log.warning("jwt_secret_not_set", msg="CNO_JWT_SECRET is empty — using fallback dev secret")
        secret = "dev-fallback-secret-do-not-use-in-prod"
    payload = {
        "policyNumber": policy_number,
        "amount": amount,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,  # 5-minute expiry
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def process_card_payment(
    policy_number: str,
    access_token: str,
    amount: float,
    card_number: str,
    expiry: str,
    cvv: str,
    idempotency_key: str = "",
) -> dict:
    """
    DEBIT_CREDIT_CARD_PAYMENT — JWT-auth integration flow.
    Card data arrives via Twilio DTMF — never passed through LLM.
    """
    # BUG-018: Pre-flight validation before hitting the API
    from utils.payment_validator import validate_card_number, validate_expiry, validate_cvv
    ok, err = validate_card_number(card_number)
    if not ok:
        return {"success": False, "confirmation": "", "payment_id": "", "error": err}
    ok, err = validate_expiry(expiry)
    if not ok:
        return {"success": False, "confirmation": "", "payment_id": "", "error": err}
    ok, err = validate_cvv(cvv)
    if not ok:
        return {"success": False, "confirmation": "", "payment_id": "", "error": err}

    url = f"{settings.cno_api_base_url}/payment/card"
    jwt_token = _generate_jwt(policy_number, amount)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Payment-JWT": jwt_token,
        "Content-Type":  "application/json",
        "Idempotency-Key": idempotency_key or f"{policy_number}:{amount}:{card_number[-4:]}",
    }
    payload = {
        "PolicyNumber": policy_number,
        "Amount": amount,
        "CardNumber": card_number,
        "ExpiryDate": expiry,
        "CVV": cvv,
    }
    t0 = time.time()
    status, body = await post_json(url, json=payload, headers=headers, timeout=5)
    latency = int((time.time() - t0) * 1000)
    if status in (200, 201):
        payment_id = body.get("PaymentId", "")
        _log.info("api_card_payment", policy=policy_number[:3] + "****",
                  status=status, latency_ms=latency, payment_id=payment_id)
        return {"success": True, "confirmation": body.get("ConfirmationNumber", ""),
                "payment_id": payment_id, "error": ""}
    _log.warning("api_card_payment_failed", status=status,
                 error=str(body)[:100], latency_ms=latency)
    return {"success": False, "confirmation": "", "payment_id": "", "error": str(body)}


async def process_ach_payment(
    policy_number: str,
    access_token: str,
    amount: float,
    routing_number: str,
    account_number: str,
    account_type: str = "checking",
    idempotency_key: str = "",
) -> dict:
    """ACH / Bank payment — requires ACH authorization script read first."""
    # BUG-018: Pre-flight validation
    from utils.payment_validator import validate_routing_number, validate_account_number
    ok, err = validate_routing_number(routing_number)
    if not ok:
        return {"success": False, "confirmation": "", "payment_id": "", "error": err}
    ok, err = validate_account_number(account_number)
    if not ok:
        return {"success": False, "confirmation": "", "payment_id": "", "error": err}

    url = f"{settings.cno_api_base_url}/payment/ach"
    jwt_token = _generate_jwt(policy_number, amount)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Payment-JWT": jwt_token,
        "Content-Type":  "application/json",
        "Idempotency-Key": idempotency_key or f"{policy_number}:{amount}:{account_number[-4:]}",
    }
    payload = {
        "PolicyNumber":  policy_number,
        "Amount":        amount,
        "RoutingNumber": routing_number,
        "AccountNumber": account_number,
        "AccountType":   account_type,
    }
    t0 = time.time()
    status, body = await post_json(url, json=payload, headers=headers, timeout=5)
    latency = int((time.time() - t0) * 1000)
    if status in (200, 201):
        payment_id = body.get("PaymentId", "")
        _log.info("api_ach_payment", policy=policy_number[:3] + "****",
                  status=status, latency_ms=latency, payment_id=payment_id)
        return {"success": True, "confirmation": body.get("ConfirmationNumber", ""),
                "payment_id": payment_id, "error": ""}
    _log.warning("api_ach_payment_failed", status=status,
                 error=str(body)[:100], latency_ms=latency)
    return {"success": False, "confirmation": "", "payment_id": "", "error": str(body)}


def get_ach_script() -> str:
    """Returns verbatim ACH authorization script — never paraphrase."""
    return ACH_AUTHORIZATION_SCRIPT
