"""
Mock CNO Backend API — dev/test only.
Runs on port 8001 (matches CNO_API_BASE_URL=http://localhost:8001 in .env).

Endpoints implemented (all POST):
  /party/search        — find party by phone / policy / DOB / name / zip
  /auth/token          — exchange partyKey + companyCode → access token
  /holding/inquiry     — policy status, premium, paid-to-date, billing mode
  /payment/history     — last 3 transactions
  /loan/inquiry        — loan balance, interest, payoff
  /beneficiary/inquiry — beneficiary list
  /contact/update      — address / phone update (always succeeds)
  /document/request    — document mail/fax request (always succeeds)
  /payment/card        — card payment processing
  /payment/ach         — ACH payment processing

Run:
  venv/Scripts/python mock_cno_api.py
or:
  venv/Scripts/uvicorn mock_cno_api:app --port 8001 --reload
"""

import random
import string
import time
from datetime import date

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

log = structlog.get_logger()

app = FastAPI(title="CNO Mock API", version="1.0.0")

# ── Test caller data ──────────────────────────────────────────────────────────
# Each party record mirrors the exact field names read by party_search.py
# and auth.py (_build_customer).

PARTIES = [
    {
        "PartyCalrKeyCode": "PKY100001",
        "CompanyCode": "CNO",
        "FirstName": "John",
        "LastName": "Smith",
        "DOB": "1965-07-15",
        "PhoneNumbers": [{"PhoneNumber": "5551234567", "PhoneType": "Home"}],
        "Policies": [{"PolicyNumber": "P300123456", "ProductType": "Whole Life"}],
        "Addresses": [{"Street": "123 Maple Ave", "City": "Indianapolis", "State": "IN", "Zip": "46204"}],
        # Persona list — used by caller-name identification step post-auth
        # Jane Smith is beneficiary only (see BENEFICIARY_DATA), not a policy persona
        "Personas": [
            {"name": "John Smith",  "role": "insured"},
            {"name": "Smith Corp",  "role": "payor"},
        ],
    },
    {
        "PartyCalrKeyCode": "PKY100002",
        "CompanyCode": "CNO",
        "FirstName": "Mary",
        "LastName": "Johnson",
        "DOB": "1950-03-22",
        "PhoneNumbers": [{"PhoneNumber": "5559876543", "PhoneType": "Home"}],
        "Policies": [{"PolicyNumber": "P300654321", "ProductType": "Term Life"}],
        "Addresses": [{"Street": "456 Oak Street", "City": "Columbus", "State": "OH", "Zip": "43215"}],
        # Mary Johnson is both insured and owner (dual-role) — payor is her husband
        "Personas": [
            {"name": "Mary Johnson",   "role": "insured"},
            {"name": "Mary Johnson",   "role": "owner"},
            {"name": "Robert Johnson", "role": "payor"},
        ],
    },
    {
        # Multi-policy caller — triggers policy-selection flow
        "PartyCalrKeyCode": "PKY100003",
        "CompanyCode": "CNO",
        "FirstName": "Robert",
        "LastName": "Williams",
        "DOB": "1945-11-08",
        "PhoneNumbers": [{"PhoneNumber": "5553334444", "PhoneType": "Home"}],
        "Policies": [
            {"PolicyNumber": "P300111222", "ProductType": "Whole Life"},
            {"PolicyNumber": "P300333444", "ProductType": "Medicare Supplement"},
        ],
        "Addresses": [{"Street": "789 Pine Road", "City": "Carmel", "State": "IN", "Zip": "46032"}],
        # Robert is both insured and payor; Linda is the owner
        "Personas": [
            {"name": "Robert Williams", "role": "insured"},
            {"name": "Linda Williams",  "role": "owner"},
            {"name": "Robert Williams", "role": "payor"},
        ],
    },
    {
        # Easy test caller — short DOB (2000-01-01) for quick manual testing
        # No payor entry — tests missing-role edge case
        "PartyCalrKeyCode": "PKY000001",
        "CompanyCode": "CNO",
        "FirstName": "Test",
        "LastName": "User",
        "DOB": "2000-01-01",
        "PhoneNumbers": [{"PhoneNumber": "5550000000", "PhoneType": "Mobile"}],
        "Policies": [{"PolicyNumber": "P300000001", "ProductType": "Whole Life"}],
        "Addresses": [{"Street": "1 Test Lane", "City": "Anywhere", "State": "IN", "Zip": "46000"}],
        "Personas": [
            {"name": "Test User",  "role": "insured"},
            {"name": "Test Owner", "role": "owner"},
        ],
    },
]

# Policy-level detail data keyed by PolicyNumber
POLICY_DETAIL = {
    "P300123456": {
        "PolicyStatus": "Active",
        "PremiumAmount": "125.50",
        "PaidToDate": "2026-07-01",
        "BillingMode": "Monthly",
        "CoverageAmount": "50000",
    },
    "P300654321": {
        "PolicyStatus": "Active",
        "PremiumAmount": "45.00",
        "PaidToDate": "2026-06-01",
        "BillingMode": "Monthly",
        "CoverageAmount": "100000",
    },
    "P300111222": {
        "PolicyStatus": "Active",
        "PremiumAmount": "210.00",
        "PaidToDate": "2026-08-01",
        "BillingMode": "Quarterly",
        "CoverageAmount": "25000",
    },
    "P300333444": {
        "PolicyStatus": "Active",
        "PremiumAmount": "189.00",
        "PaidToDate": "2026-06-15",
        "BillingMode": "Monthly",
        "CoverageAmount": "0",
    },
    "P300000001": {
        "PolicyStatus": "Active",
        "PremiumAmount": "75.00",
        "PaidToDate": "2026-06-01",
        "BillingMode": "Monthly",
        "CoverageAmount": "25000",
    },
}

PAYMENT_HISTORY = {
    "P300123456": [
        {"Amount": "125.50", "Date": "2026-05-01", "Status": "Posted"},
        {"Amount": "125.50", "Date": "2026-04-01", "Status": "Posted"},
        {"Amount": "125.50", "Date": "2026-03-01", "Status": "Posted"},
    ],
    "P300654321": [
        {"Amount": "45.00", "Date": "2026-05-15", "Status": "Posted"},
        {"Amount": "45.00", "Date": "2026-04-15", "Status": "Posted"},
        {"Amount": "45.00", "Date": "2026-03-15", "Status": "Posted"},
    ],
    "P300111222": [
        {"Amount": "210.00", "Date": "2026-05-01", "Status": "Posted"},
        {"Amount": "210.00", "Date": "2026-02-01", "Status": "Posted"},
        {"Amount": "210.00", "Date": "2025-11-01", "Status": "Posted"},
    ],
    "P300333444": [
        {"Amount": "189.00", "Date": "2026-05-15", "Status": "Posted"},
        {"Amount": "189.00", "Date": "2026-04-15", "Status": "Posted"},
    ],
    "P300000001": [
        {"Amount": "75.00", "Date": "2026-05-01", "Status": "Posted"},
        {"Amount": "75.00", "Date": "2026-04-01", "Status": "Posted"},
        {"Amount": "75.00", "Date": "2026-03-01", "Status": "Posted"},
    ],
}

LOAN_DATA = {
    "P300123456": {
        "LoanBalance": "5000.00",
        "AccruedInterest": "187.50",
        "PayoffAmount": "5187.50",
        "LoanOriginationDate": "2023-01-15",
    },
    "P300111222": {
        "LoanBalance": "2500.00",
        "AccruedInterest": "93.75",
        "PayoffAmount": "2593.75",
        "LoanOriginationDate": "2024-06-01",
    },
    "P300000001": {
        "LoanBalance": "1000.00",
        "AccruedInterest": "37.50",
        "PayoffAmount": "1037.50",
        "LoanOriginationDate": "2025-01-01",
    },
}

BENEFICIARY_DATA = {
    "P300123456": [
        {"FirstName": "Jane", "LastName": "Smith", "Relationship": "Spouse", "Percentage": 100},
    ],
    "P300654321": [
        {"FirstName": "Michael", "LastName": "Johnson", "Relationship": "Son", "Percentage": 50},
        {"FirstName": "Lisa",    "LastName": "Johnson", "Relationship": "Daughter", "Percentage": 50},
    ],
    "P300111222": [
        {"FirstName": "Patricia", "LastName": "Williams", "Relationship": "Spouse", "Percentage": 100},
    ],
    "P300333444": [
        {"FirstName": "Patricia", "LastName": "Williams", "Relationship": "Spouse", "Percentage": 100},
    ],
    "P300000001": [
        {"FirstName": "Demo", "LastName": "Beneficiary", "Relationship": "Child", "Percentage": 100},
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _confirmation_number(prefix: str = "CNF") -> str:
    return prefix + "".join(random.choices(string.digits, k=8))


def _error(msg: str, status: int = 404) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ErrorBlock": [{"ErrorCode": str(status), "ErrorMessage": msg}]},
    )


def _find_party_by_phone(phone: str):
    for p in PARTIES:
        for ph in p.get("PhoneNumbers", []):
            if ph.get("PhoneNumber") == phone:
                return p
    return None


def _find_party_by_policy(policy: str):
    for p in PARTIES:
        for pol in p.get("Policies", []):
            if pol.get("PolicyNumber") == policy:
                return p
    return None


def _get_policy_number(party: dict, requested: str = "") -> str:
    """Return the requested policy if provided, else the first policy on the party."""
    policies = [pol.get("PolicyNumber", "") for pol in party.get("Policies", [])]
    if requested and requested in policies:
        return requested
    return policies[0] if policies else ""


def _log_request(endpoint: str, body: dict):
    log.info(f"mock_api_{endpoint.replace('/', '_')}", **{
        k: ("***" if k.lower() in ("cardnumber", "cvv", "routingnumber", "accountnumber") else v)
        for k, v in body.items()
    })


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/party/search")
async def party_search(request: Request):
    body = await request.json()
    _log_request("party/search", body)

    phone      = body.get("PhoneNumber", "").strip().replace("-", "").replace(" ", "")
    policy_num = body.get("PolicyNumber", "").strip().upper()
    dob        = body.get("DOB", "").strip()
    first      = body.get("FirstName", "").strip().lower()
    last       = body.get("LastName", "").strip().lower()
    zipcode    = body.get("ZipCode", "").strip()

    party = None

    if phone:
        party = _find_party_by_phone(phone)
    if not party and policy_num:
        party = _find_party_by_policy(policy_num)
    if not party and dob:
        for p in PARTIES:
            if p.get("DOB") == dob:
                party = p
                break
    if not party and (first or last):
        for p in PARTIES:
            pf = p.get("FirstName", "").lower()
            pl = p.get("LastName", "").lower()
            if (first and first == pf) or (last and last == pl):
                party = p
                break
    if not party and zipcode:
        for p in PARTIES:
            addrs = p.get("Addresses") or [{}]
            if addrs[0].get("Zip") == zipcode:
                party = p
                break

    if not party:
        return JSONResponse(
            status_code=200,
            content={"SearchParties": [], "TotalCount": 0},
        )

    return JSONResponse(
        status_code=200,
        content={"SearchParties": [party], "TotalCount": 1},
    )


@app.post("/auth/token")
async def auth_token(request: Request):
    body = await request.json()
    _log_request("auth/token", body)

    party_key    = body.get("PartyCalrKeyCode", "")
    company_code = body.get("CompanyCode", "")

    # Validate partyKey exists
    known_keys = {p["PartyCalrKeyCode"] for p in PARTIES}
    if party_key not in known_keys:
        return _error("Invalid party key or company code", status=401)

    token = f"mock-access-token-{party_key}-{int(time.time())}"
    return JSONResponse(status_code=200, content={"AccessToken": token, "ExpiresIn": 3600})


@app.post("/holding/inquiry")
async def holding_inquiry(request: Request):
    body = await request.json()
    _log_request("holding/inquiry", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    detail = POLICY_DETAIL.get(policy_num)
    if not detail:
        return _error(f"Policy {policy_num} not found")

    return JSONResponse(status_code=200, content=detail)


@app.post("/payment/history")
async def payment_history(request: Request):
    body = await request.json()
    _log_request("payment/history", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    max_rec    = int(body.get("MaxRecords", 3))

    txns = PAYMENT_HISTORY.get(policy_num, [])
    return JSONResponse(
        status_code=200,
        content={"PolicyNumber": policy_num, "Transactions": txns[:max_rec]},
    )


@app.post("/loan/inquiry")
async def loan_inquiry(request: Request):
    body = await request.json()
    _log_request("loan/inquiry", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    data = LOAN_DATA.get(policy_num)

    if not data:
        # Policy exists but has no loan — return zeros
        if policy_num in POLICY_DETAIL:
            return JSONResponse(
                status_code=200,
                content={
                    "PolicyNumber":      policy_num,
                    "LoanBalance":       "0.00",
                    "AccruedInterest":   "0.00",
                    "PayoffAmount":      "0.00",
                    "LoanOriginationDate": "",
                },
            )
        return _error(f"Policy {policy_num} not found")

    return JSONResponse(status_code=200, content={"PolicyNumber": policy_num, **data})


@app.post("/beneficiary/inquiry")
async def beneficiary_inquiry(request: Request):
    body = await request.json()
    _log_request("beneficiary/inquiry", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    benes = BENEFICIARY_DATA.get(policy_num)

    if benes is None:
        if policy_num in POLICY_DETAIL:
            return JSONResponse(
                status_code=200,
                content={"PolicyNumber": policy_num, "Beneficiaries": []},
            )
        return _error(f"Policy {policy_num} not found")

    return JSONResponse(
        status_code=200,
        content={"PolicyNumber": policy_num, "Beneficiaries": benes},
    )


@app.post("/contact/update")
async def contact_update(request: Request):
    body = await request.json()
    _log_request("contact/update", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    if policy_num not in POLICY_DETAIL:
        return _error(f"Policy {policy_num} not found")

    log.info("mock_contact_updated",
             policy=policy_num,
             new_address=body.get("NewAddress", ""),
             new_phone=body.get("NewPhone", ""))

    return JSONResponse(
        status_code=200,
        content={"PolicyNumber": policy_num, "Status": "Updated", "ConfirmationNumber": _confirmation_number("UPD")},
    )


@app.post("/document/request")
async def document_request(request: Request):
    body = await request.json()
    _log_request("document/request", body)

    policy_num = body.get("PolicyNumber", "").strip().upper()
    if policy_num not in POLICY_DETAIL:
        return _error(f"Policy {policy_num} not found")

    log.info("mock_document_requested",
             policy=policy_num,
             doc_type=body.get("DocumentType", ""),
             delivery=body.get("DeliveryMethod", "mail"))

    return JSONResponse(
        status_code=201,
        content={
            "PolicyNumber":      policy_num,
            "DocumentType":      body.get("DocumentType", ""),
            "DeliveryMethod":    body.get("DeliveryMethod", "mail"),
            "EstimatedDelivery": "7-10 business days",
            "ConfirmationNumber": _confirmation_number("DOC"),
        },
    )


@app.post("/payment/card")
async def payment_card(request: Request):
    body = await request.json()
    _log_request("payment/card", body)

    policy_num  = body.get("PolicyNumber", "").strip().upper()
    amount      = float(body.get("Amount", 0))
    card_number = body.get("CardNumber", "")

    if policy_num not in POLICY_DETAIL:
        return _error(f"Policy {policy_num} not found", status=400)

    # Simulate decline for test card ending in 0000
    if card_number.endswith("0000"):
        return JSONResponse(
            status_code=402,
            content={"ErrorBlock": [{"ErrorCode": "DECLINE", "ErrorMessage": "Card declined by issuer"}]},
        )

    conf = _confirmation_number("CNF")
    log.info("mock_card_payment_processed",
             policy=policy_num, amount=amount, confirmation=conf)

    return JSONResponse(
        status_code=200,
        content={
            "PolicyNumber":      policy_num,
            "Amount":            str(amount),
            "ConfirmationNumber": conf,
            "Status":            "Approved",
            "PostingDate":       str(date.today()),
        },
    )


@app.post("/payment/ach")
async def payment_ach(request: Request):
    body = await request.json()
    _log_request("payment/ach", body)

    policy_num     = body.get("PolicyNumber", "").strip().upper()
    amount         = float(body.get("Amount", 0))
    routing_number = body.get("RoutingNumber", "")

    if policy_num not in POLICY_DETAIL:
        return _error(f"Policy {policy_num} not found", status=400)

    # Simulate invalid routing number
    if routing_number == "000000000":
        return JSONResponse(
            status_code=422,
            content={"ErrorBlock": [{"ErrorCode": "INVALID_ROUTING", "ErrorMessage": "Invalid routing number"}]},
        )

    conf = _confirmation_number("ACH")
    log.info("mock_ach_payment_processed",
             policy=policy_num, amount=amount, confirmation=conf)

    return JSONResponse(
        status_code=200,
        content={
            "PolicyNumber":       policy_num,
            "Amount":             str(amount),
            "ConfirmationNumber":  conf,
            "Status":             "Accepted",
            "EstimatedPosting":   "1-2 business days",
        },
    )


# ── Health + test data viewer ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "mock": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    rows = ""
    for p in PARTIES:
        phone    = (p["PhoneNumbers"] or [{}])[0].get("PhoneNumber", "—")
        policies = ", ".join(pol["PolicyNumber"] for pol in p["Policies"])
        has_loan = "Yes" if p["Policies"][0]["PolicyNumber"] in LOAN_DATA else "No"
        rows += (
            f"<tr>"
            f"<td>{p['FirstName']} {p['LastName']}</td>"
            f"<td>{phone}</td>"
            f"<td>{p['DOB']}</td>"
            f"<td>{policies}</td>"
            f"<td>{has_loan}</td>"
            f"</tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CNO Mock API</title>
<style>
  body {{ font-family: monospace; background: #0f172a; color: #e2e8f0; padding: 40px; }}
  h1 {{ color: #38bdf8; margin-bottom: 4px; }}
  .badge {{ background: #22c55e; color: #000; padding: 2px 10px; border-radius: 20px;
            font-size: 12px; font-weight: bold; margin-left: 10px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 24px; }}
  th {{ background: #1e293b; padding: 10px 16px; text-align: left; color: #94a3b8; font-size: 12px; }}
  td {{ padding: 10px 16px; border-bottom: 1px solid #1e293b; }}
  tr:hover td {{ background: #1e293b55; }}
  .endpoints {{ margin-top: 32px; }}
  .ep {{ background: #1e293b; border-radius: 8px; padding: 8px 16px; margin: 6px 0;
          font-size: 13px; display: flex; gap: 16px; align-items: center; }}
  .method {{ background: #6366f1; color: #fff; padding: 2px 8px; border-radius: 4px;
             font-size: 11px; font-weight: bold; }}
</style>
</head>
<body>
<h1>CNO Mock API <span class="badge">RUNNING</span></h1>
<p style="color:#64748b">Dev mock — port 8001 | All endpoints return realistic fixture data</p>

<table>
  <thead><tr>
    <th>Name</th><th>Phone</th><th>DOB</th><th>Policy(s)</th><th>Has Loan</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>

<div class="endpoints">
  <p style="color:#94a3b8; margin-bottom:8px; font-size:13px;">ENDPOINTS</p>
  {"".join(
      f'<div class="ep"><span class="method">POST</span><span>{ep}</span></div>'
      for ep in [
          "/party/search", "/auth/token", "/holding/inquiry",
          "/payment/history", "/loan/inquiry", "/beneficiary/inquiry",
          "/contact/update", "/document/request", "/payment/card", "/payment/ach",
      ]
  )}
</div>

<p style="margin-top:32px; color:#475569; font-size:12px;">
  Decline test: card ending in <code>0000</code> &nbsp;|&nbsp;
  Invalid routing: <code>000000000</code>
</p>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import logging
    import structlog

    logging.basicConfig(level=logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )
    uvicorn.run("mock_cno_api:app", host="0.0.0.0", port=8001, reload=True)
