"""
Mock insuranceCompany Backend API server.
Run this alongside main.py so all API calls return realistic fake data.
Usage: python tests/mock_cno_api.py
Runs on port 8001.
"""
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI(title="Mock insuranceCompany Backend API")

# ── Fake data ─────────────────────────────────────────────────────────────────
# Party 1 — John Doe, reachable by phone 5551234567 or policy P300123456
PARTY001 = {
    "PartyCalrKeyCode": "PARTY001",
    "FirstName":        "John",
    "LastName":         "Doe",
    "DOB":              "1978-01-22",
    "CompanyCode":      "insuranceCompany",
    "FullName":         "John Doe",
    "PhoneNumbers":     [{"PhoneNumber": "5551234567"}],
    "Policies":         [{"PolicyNumber": "P300123456"}],
    "Addresses": [
        {
            "Address1": "123 MAIN ST",
            "Address2": "",
            "Address3": "",
            "Address4": "",
            "Zip":      "12345",
        }
    ],
}

# Party 2 — Sarah Johnson, reachable by policy P400567890 only (no phone in system)
# Used to test the phone-not-found → confirm → collect policy path
PARTY002 = {
    "PartyCalrKeyCode": "PARTY002",
    "FirstName":        "Sarah",
    "LastName":         "Johnson",
    "DOB":              "1985-07-15",
    "CompanyCode":      "insuranceCompany",
    "FullName":         "Sarah Johnson",
    "PhoneNumbers":     [],
    "Policies":         [{"PolicyNumber": "P400567890"}],
    "Addresses": [
        {
            "Address1": "456 OAK AVE",
            "Address2": "",
            "Address3": "",
            "Address4": "",
            "Zip":      "67890",
        }
    ],
}

FAKE_PARTY = {"SearchParties": [PARTY001]}

FAKE_HOLDING = {
    "PolicyNumber":  "P300123456",
    "PolicyStatus":  "Active",
    "PremiumAmount": "125.00",
    "PaidToDate":    "2025-06-01",
    "BillingMode":   "Monthly",
    "CoverageAmount": "50000",
}

FAKE_PAYMENT_HISTORY = {
    "Transactions": [
        {"Amount": "125.00", "Date": "2025-05-01", "Status": "Posted"},
        {"Amount": "125.00", "Date": "2025-04-01", "Status": "Posted"},
        {"Amount": "125.00", "Date": "2025-03-01", "Status": "Posted"},
    ]
}

FAKE_LOAN = {
    "LoanBalance":     "500.00",
    "AccruedInterest": "12.50",
    "PayoffAmount":    "512.50",
}

FAKE_BENEFICIARIES = {
    "Beneficiaries": [
        {"FirstName": "Jane", "LastName": "Doe", "Relationship": "Spouse", "Percentage": "100"},
    ]
}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/party/search")
async def party_search(request: Request):
    body = await request.json()
    phone  = body.get("PhoneNumber", "")
    policy = body.get("PolicyNumber", "")
    if phone == "5551234567" or policy == "P300123456":
        return {"SearchParties": [PARTY001]}
    if policy == "P400567890":
        return {"SearchParties": [PARTY002]}
    return {"SearchParties": []}

@app.post("/holding/inquiry")
async def holding_inquiry(request: Request):
    return FAKE_HOLDING

@app.post("/payment/history")
async def payment_history(request: Request):
    return FAKE_PAYMENT_HISTORY

@app.post("/payment/card")
async def card_payment(request: Request):
    return {"ConfirmationNumber": "CNF20250529001", "Status": "Approved"}

@app.post("/payment/ach")
async def ach_payment(request: Request):
    return {"ConfirmationNumber": "CNF20250529002", "Status": "Approved"}

@app.post("/loan/inquiry")
async def loan_inquiry(request: Request):
    return FAKE_LOAN

@app.post("/beneficiary/inquiry")
async def beneficiary_inquiry(request: Request):
    return FAKE_BENEFICIARIES

@app.post("/contact/update")
async def contact_update(request: Request):
    return {"Status": "Updated"}

@app.post("/document/request")
async def document_request(request: Request):
    return {"Status": "Queued", "EstimatedDays": "7-10"}

@app.post("/privacy/optout")
async def privacy_optout(request: Request):
    return {"Status": "Recorded"}

@app.get("/health")
def health():
    return {"status": "ok", "service": "mock-insuranceCompany-api"}


if __name__ == "__main__":
    print("Mock insuranceCompany API running on http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)
