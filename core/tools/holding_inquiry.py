import time
import aiohttp
import structlog
from config import settings

_log = structlog.get_logger()


async def holding_inquiry(policy_number: str, access_token: str) -> dict:
    """
    HOLDING_INQUIRY API — fetch policy details.
    Returns { success, data: { status, premium, paidToDate, coverage, ... }, error }
    """
    url = f"{settings.cno_api_base_url}/holding/inquiry"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    payload = {"PolicyNumber": policy_number}

    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status == 200 and body:
                    _log.info("api_holding_inquiry", policy=policy_number[:3] + "****",
                              status=200, latency_ms=latency)
                    return {"success": True, "data": body, "error": ""}
                error_block = body.get("ErrorBlock", [{}])
                error_msg = error_block[0].get("ErrorMessage", str(body)) if error_block else str(body)
                _log.warning("api_holding_inquiry_failed", status=resp.status,
                             error=error_msg[:100], latency_ms=latency)
                return {"success": False, "data": {}, "error": error_msg}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_holding_inquiry_error", error=str(e)[:100], latency_ms=latency)
        return {"success": False, "data": {}, "error": str(e)}


async def payment_history(policy_number: str, access_token: str) -> dict:
    """
    PAYMENT_INQUIRY API — fetch last 3 payment transactions.
    Returns { success, transactions: [...], error }
    """
    url = f"{settings.cno_api_base_url}/payment/history"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    payload = {"PolicyNumber": policy_number, "MaxRecords": 3}

    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status == 200:
                    txns = body.get("Transactions", [])
                    _log.info("api_payment_history", policy=policy_number[:3] + "****",
                              status=200, txn_count=len(txns), latency_ms=latency)
                    return {"success": True, "transactions": txns[:3], "error": ""}
                error_block = body.get("ErrorBlock", [{}])
                error_msg = error_block[0].get("ErrorMessage", str(body)) if error_block else str(body)
                _log.warning("api_payment_history_failed", status=resp.status,
                             error=error_msg[:100], latency_ms=latency)
                return {"success": False, "transactions": [], "error": error_msg}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_payment_history_error", error=str(e)[:100], latency_ms=latency)
        return {"success": False, "transactions": [], "error": str(e)}


async def loan_inquiry(policy_number: str, access_token: str) -> dict:
    """LOAN_INQUIRY API — loan balance, interest, payoff amount."""
    url = f"{settings.cno_api_base_url}/loan/inquiry"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"PolicyNumber": policy_number}

    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                latency = int((time.time() - t0) * 1000)
                if resp.status == 200:
                    _log.info("api_loan_inquiry", policy=policy_number[:3] + "****",
                              status=200, latency_ms=latency)
                    return {"success": True, "data": body, "error": ""}
                _log.warning("api_loan_inquiry_failed", status=resp.status,
                             error=str(body)[:100], latency_ms=latency)
                return {"success": False, "data": {}, "error": str(body)}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        _log.error("api_loan_inquiry_error", error=str(e)[:100], latency_ms=latency)
        return {"success": False, "data": {}, "error": str(e)}
