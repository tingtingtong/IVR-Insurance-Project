"""
Automated Twilio test caller.

Uses Twilio REST API to place scripted calls to the IVR,
validates outcomes via the dashboard API.

Usage:
  cd cno_ivr
  venv/Scripts/python tests/eval_e2e_twilio.py

Requires:
  - IVR server running (locally or via ngrok)
  - Valid Twilio credentials in .env
  - TWILIO_PHONE_NUMBER and TWILIO_BASE_URL configured
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK   = "\033[92m"
FAIL = "\033[91m"
BOLD = "\033[1m"
DIM  = "\033[90m"
CYAN = "\033[96m"
RESET = "\033[0m"

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")


SCENARIOS = [
    {
        "name": "happy_path_policy",
        "description": "Full auth + policy inquiry + goodbye",
        "twiml": """<Response>
            <Pause length="4"/>
            <Say voice="Polly.Matthew">5 5 5 1 2 3 4 5 6 7</Say>
            <Pause length="6"/>
            <Say voice="Polly.Matthew">July 15th 1965</Say>
            <Pause length="6"/>
            <Say voice="Polly.Matthew">John Smith</Say>
            <Pause length="6"/>
            <Say voice="Polly.Matthew">I want to check my policy status</Say>
            <Pause length="8"/>
            <Say voice="Polly.Matthew">goodbye</Say>
            <Pause length="3"/>
        </Response>""",
        "expected": {
            "authenticated": True,
            "final_intents": ["policy_info", "goodbye"],
        },
    },
    {
        "name": "faq_then_escalate",
        "description": "Ask FAQ then request agent transfer",
        "twiml": """<Response>
            <Pause length="4"/>
            <Say voice="Polly.Matthew">What is whole life insurance?</Say>
            <Pause length="8"/>
            <Say voice="Polly.Matthew">I want to speak to a representative</Say>
            <Pause length="5"/>
        </Response>""",
        "expected": {
            "authenticated": False,
            "final_intents": ["escalate"],
        },
    },
    {
        "name": "goodbye_only",
        "description": "Caller immediately says goodbye",
        "twiml": """<Response>
            <Pause length="4"/>
            <Say voice="Polly.Matthew">goodbye</Say>
            <Pause length="3"/>
        </Response>""",
        "expected": {
            "final_intents": ["goodbye"],
        },
    },
]


def place_call(scenario: dict) -> str | None:
    """Place a Twilio call with scripted TwiML. Returns call SID."""
    from twilio.rest import Client
    from config import settings

    if not settings.twilio_phone_number or not settings.twilio_base_url:
        print(f"  {FAIL}TWILIO_PHONE_NUMBER or TWILIO_BASE_URL not configured{RESET}")
        return None

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    # Use a Twilio-hosted TwiML endpoint (via twiml parameter)
    try:
        call = client.calls.create(
            to=settings.twilio_phone_number,
            from_=settings.twilio_phone_number,
            twiml=scenario["twiml"],
            record=True,
        )
        return call.sid
    except Exception as e:
        print(f"  {FAIL}Failed to place call: {e}{RESET}")
        return None


def wait_for_call_completion(call_sid: str, timeout: int = 90) -> dict | None:
    """Poll Twilio until the call completes."""
    from twilio.rest import Client
    from config import settings

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    start = time.time()

    while time.time() - start < timeout:
        call = client.calls(call_sid).fetch()
        if call.status in ("completed", "failed", "busy", "no-answer", "canceled"):
            return {
                "status": call.status,
                "duration": call.duration,
                "direction": call.direction,
            }
        time.sleep(5)

    return None


def validate_call(call_sid: str, expected: dict) -> tuple[bool, list[str]]:
    """Fetch call data from dashboard API and validate against expectations."""
    import httpx
    from config import settings

    base_url = f"http://localhost:{settings.app_port}"
    issues = []

    try:
        resp = httpx.get(f"{base_url}/dashboard/calls/{call_sid}", timeout=5)
        if resp.status_code != 200:
            return False, [f"Dashboard returned {resp.status_code}"]
        call_data = resp.json()
    except Exception as e:
        return False, [f"Could not reach dashboard: {e}"]

    # Check auth
    if "authenticated" in expected:
        if call_data.get("authenticated") != expected["authenticated"]:
            issues.append(f"Auth: expected {expected['authenticated']}, got {call_data.get('authenticated')}")

    # Check intents
    if "final_intents" in expected:
        intent_history = call_data.get("intent_history", [])
        for intent in expected["final_intents"]:
            if intent not in intent_history:
                issues.append(f"Missing intent: {intent} (history: {intent_history})")

    return len(issues) == 0, issues


def run_eval():
    print(f"\n{BOLD}Automated Twilio E2E Test Calls{RESET}")
    print(f"{DIM}{'=' * 70}{RESET}")
    print(f"Scenarios: {len(SCENARIOS)}\n")

    results = []

    for scenario in SCENARIOS:
        print(f"\n{CYAN}Scenario: {scenario['name']}{RESET} — {scenario['description']}")

        # Place call
        call_sid = place_call(scenario)
        if not call_sid:
            results.append({"name": scenario["name"], "passed": False, "reason": "Failed to place call"})
            continue

        print(f"  Call SID: {call_sid}")
        print(f"  Waiting for completion...")

        # Wait for completion
        completion = wait_for_call_completion(call_sid)
        if not completion:
            print(f"  {FAIL}Call timed out{RESET}")
            results.append({"name": scenario["name"], "call_sid": call_sid, "passed": False, "reason": "Timeout"})
            continue

        print(f"  Status: {completion['status']} | Duration: {completion['duration']}s")

        # Allow processing time
        time.sleep(3)

        # Validate
        passed, issues = validate_call(call_sid, scenario["expected"])
        if passed:
            print(f"  {OK}PASS{RESET}")
        else:
            print(f"  {FAIL}FAIL{RESET}")
            for issue in issues:
                print(f"    - {issue}")

        results.append({
            "name": scenario["name"],
            "call_sid": call_sid,
            "passed": passed,
            "issues": issues,
            "completion": completion,
        })

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}Results: {passed}/{total} passed{RESET}")

    for r in results:
        icon = f"{OK}PASS{RESET}" if r.get("passed") else f"{FAIL}FAIL{RESET}"
        print(f"  [{icon}] {r['name']}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results_path = os.path.join(RESULTS_DIR, "twilio_e2e_eval.json")
    with open(results_path, "w") as f:
        json.dump({"passed": passed, "total": total, "scenarios": results}, f, indent=2, default=str)
    print(f"\n{DIM}Results saved to {results_path}{RESET}")

    return passed == total


if __name__ == "__main__":
    success = run_eval()
    sys.exit(0 if success else 1)
