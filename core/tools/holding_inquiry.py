import aiohttp
from config import settings


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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                if resp.status == 200 and body:
                    return {"success": True, "data": body, "error": ""}
                error_block = body.get("ErrorBlock", [{}])
                error_msg = error_block[0].get("ErrorMessage", str(body)) if error_block else str(body)
                return {"success": False, "data": {}, "error": error_msg}
    except Exception as e:
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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                if resp.status == 200:
                    txns = body.get("Transactions", [])
                    return {"success": True, "transactions": txns[:3], "error": ""}
                error_block = body.get("ErrorBlock", [{}])
                error_msg = error_block[0].get("ErrorMessage", str(body)) if error_block else str(body)
                return {"success": False, "transactions": [], "error": error_msg}
    except Exception as e:
        return {"success": False, "transactions": [], "error": str(e)}


async def loan_inquiry(policy_number: str, access_token: str) -> dict:
    """LOAN_INQUIRY API — loan balance, interest, payoff amount."""
    url = f"{settings.cno_api_base_url}/loan/inquiry"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"PolicyNumber": policy_number}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                if resp.status == 200:
                    return {"success": True, "data": body, "error": ""}
                return {"success": False, "data": {}, "error": str(body)}
    except Exception as e:
        return {"success": False, "data": {}, "error": str(e)}
