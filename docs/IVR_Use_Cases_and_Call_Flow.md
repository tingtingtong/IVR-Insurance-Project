# US Insurance Company (UIC) IVR — Use Cases & Call Flow Document

---

## 1. Use Cases

| # | Use Case | Description |
|---|----------|-------------|
| 1 | **Policy Information** | Retrieves and reads back policy status, coverage summary, billing mode, and paid-to date for the authenticated caller. |
| 2 | **Premium Account Info** | Provides the caller's premium amount, billing frequency (monthly/quarterly/annual), and paid-to date. |
| 3 | **Payment Information** | Fetches and reads back the last 3 payment transactions including amount, date, and posting status, followed by a mandatory payment posting disclosure. |
| 4 | **Payment Information Update (One-Time Payment)** | Processes a one-time payment via credit/debit card or bank account (ACH); caller speaks the numbers (with DTMF keypad as fallback), and an ACH authorization script is read verbatim for bank payments. |
| 5 | **Loan Inquiry** | Retrieves and presents the policy loan balance, accrued interest, and payoff amount. |
| 6 | **Beneficiary Information** | Reads back the beneficiary designations (name, relationship, percentage) on the policy, with a mandatory relationship restriction disclosure. |
| 7 | **Contact Information Change** | Guides the caller through updating their mailing address and/or phone number via a multi-step collect, confirm, and submit flow. |
| 8 | **Document Request** | Allows the caller to request policy documents (policy copy, billing statement, tax form 1099, claim form, beneficiary form, amendment) delivered by mail or fax to the address on file — email is not permitted per compliance policy. |
| 9 | **Privacy / Opt-Out (GLBA)** | Reads the GLBA privacy disclosure and processes the caller's opt-out preference for information sharing with affiliated companies, non-affiliated companies, or both. |
| 10 | **FAQ (General Questions)** | Answers general insurance questions using RAG retrieval against a knowledge base; falls back to a canned response or agent transfer when no match is found. |
| 11 | **Live Agent Escalation** | Transfers the caller to a human representative when explicitly requested, when authentication fails, or when the request is out of scope. |

- **Call Termination:** The caller can say goodbye at any point to end the call. This always bypasses authentication so callers are never trapped in the system.

---

## 2. Authentication Flow (Required Before Use Cases 1–9)

### Authentication Combinations (2 PII Required)

The system verifies the caller's identity by matching **2 PII fields** against the backend party search API. Any one of these combinations will authenticate the caller:

| Combination | Fields |
|-------------|--------|
| **Primary** | Phone Number + Date of Birth |
| **Fallback** | Phone Number + Insured Name (first & last) |
| **Alternate** | Policy Number + Date of Birth |
| **Alternate Fallback** | Policy Number + Insured Name (first & last) |

### Step-by-Step Authentication

**Step 0 — ANI Match Check (Automatic)**

Before asking the caller for any information, the system checks the incoming caller's phone number (ANI — Automatic Number Identification) against the party search API. If a match is found, the bot skips the phone collection step and instead confirms: *"Is [phone number] the phone number associated with your policy?"*

| Outcome | Next Step |
|---------|-----------|
| ANI match found, caller confirms Yes | Step 3 — Collect Date of Birth (1st PII already captured) |
| ANI match found, caller says No | Step 1 — Collect Phone Number (manually) |
| No ANI match | Step 1 — Collect Phone Number |

**Step 1 — Collect Phone Number**
Bot: *"What is the 10-digit phone number associated with your policy?"*

The caller speaks their phone number. The system parses and validates 10 digits, then searches the party API by phone.

| Outcome | Next Step |
|---------|-----------|
| Match found in API | Step 3 — Collect Date of Birth (candidate party record held) |
| No match found | Step 2 — Confirm Phone (bot reads back the number for verification) |
| API error / timeout | Skip to Step 2b — Collect Policy Number |
| Caller says "I don't know" | Skip to Step 2b — Collect Policy Number |
| Invalid / blank input | Retry up to 2 times, then escalate to agent |

**Step 2 — Confirm Phone Number**
Bot: *"I heard [5 5 5 - 1 2 3 - 4 5 6 7]. Is that correct?"*

| Outcome | Next Step |
|---------|-----------|
| Yes | Step 2b — Collect Policy Number (phone confirmed but not in system) |
| No | Back to Step 1 — re-collect phone number |
| "I don't know" | Step 2b — Collect Policy Number |
| Unclear response | Retry up to 2 times, then escalate |

**Step 2b — Collect Policy Number**
Bot: *"What is your policy number? It's on your policy documents."*

| Outcome | Next Step |
|---------|-----------|
| Match found in API | Step 3 — Collect Date of Birth |
| No match (neither phone nor policy found) | Escalate to agent |
| Caller says "I don't know" | Escalate to agent |
| Invalid / blank input | Retry up to 2 times, then escalate |

**Step 3 — Collect Date of Birth**
Bot: *"What is the insured's date of birth?"*

| Outcome | Next Step |
|---------|-----------|
| DOB parsed and matches API record | Step 5 — Identity Verified |
| DOB parsed but does not match | Step 4 — Collect Insured Name (fallback) |
| Caller says "I don't know" | Step 4 — Collect Insured Name |
| Invalid / blank input | Retry up to 2 times, then escalate |

**Step 4 — Collect Insured Name (fallback verification)**
Bot: *"What is the first and last name of the insured?"*

| Outcome | Next Step |
|---------|-----------|
| Name matches API record | Step 5 — Identity Verified |
| Name does not match | Escalate to agent (both DOB and name failed) |
| Caller says "I don't know" | Escalate to agent |
| Invalid / blank input | Retry up to 2 times, then escalate |

**Step 5 — Policy Selection (if multiple policies exist)**
Bot: *"We found multiple policies on your account. Please say the last 4 digits of the policy you'd like to access. Your options are: [last 4 digits of each]."*

| Outcome | Next Step |
|---------|-----------|
| Digits match a policy | Step 6 — Caller Persona Identification |
| No match | Retry up to 2 times, then escalate |

**Step 6 — Caller Persona Identification**
Bot: *"I've verified your identity. May I ask your name please?"*

The caller's name is matched against personas on the policy (insured, owner, payor).

| Outcome | Access Level |
|---------|-------------|
| Match found (insured / owner / payor) | Full access to all use cases. Bot: *"Thank you, [Name]."* |
| No match | Caller marked as "other". Bot informs them that detailed policy information can only be shared with the policyholder, owner, or payor, and offers to transfer. If an "other" caller later attempts any restricted use case, they are automatically routed to escalation. |

### Retry Limits

| Field | Max Retries | On Failure |
|-------|-------------|------------|
| Phone Number | 2 retries (3 total attempts) | Escalate to agent |
| Phone Confirmation | 2 retries | Escalate to agent |
| Policy Number | 2 retries | Escalate to agent |
| Date of Birth | 2 retries | Escalate to agent |
| Insured Name | 2 retries | Escalate to agent |
| Policy Selection | 2 retries | Escalate to agent |

**Global maximum:** 3 authentication failures total triggers immediate transfer — *"I'm sorry, I wasn't able to verify your information. Let me transfer you to a representative who can help."*

### Initial Escalation Engagement

If a caller's first stated intent is "I want to speak to an agent" (before any use case begins), the bot first attempts to engage the caller by asking what they need help with. This gives the IVR an opportunity to handle the request without a live agent transfer. If the caller insists on speaking to a person, the transfer proceeds immediately.

---

## 3. Detailed Use Case — Policy Information

**Trigger intent:** `policy_info` — caller says: "What's my policy status?", "Tell me about my policy", "Is my policy active?"

**Pre-requisite:** Caller must be authenticated (Section 2). If not yet authenticated, the auth flow runs first with `active_flow = "policy"` so the caller returns here after auth completes.

### Flow

```
1. Auth Guard Check
   └─ Authenticated?
       ├─ No  → Run authentication (Section 2), then return here
       └─ Yes → Continue

2. Retrieve Policy Number
   └─ Policy number found in customer record?
       ├─ No  → "I'm sorry, I wasn't able to find your policy number.
       │         Let me transfer you to a representative." → Escalate
       └─ Yes → Continue

3. Call Holding Inquiry API
   POST /holding/inquiry  (policy number + access token, 10s timeout)
   └─ API success?
       ├─ No  → "I'm sorry, we're experiencing a technical issue.
       │         Let me transfer you to a representative." → Escalate
       └─ Yes → Continue

4. Format Policy Context
   Extract from API response:
     • Insured name (first + last)
     • Policy status (Active, Lapsed, Paid Up, etc.)
     • Premium amount (e.g., $45.00)
     • Paid-to date (e.g., "March 15th, 2026")
     • Billing mode (Monthly, Quarterly, Annual)

5. Generate Voice Response
   LLM produces a concise 1–3 sentence natural-language summary
   from the formatted data, suitable for voice delivery.

6. Deliver Response via TTS
   Example: "Your policy is currently active. Your premium amount
   is $45 per month, and your policy is paid through March 15th, 2026."

7. After Completion
   Bot: "Is there anything else I can help you with today?"
```

### Context Switch Policy: Escalate-Only

While the policy information flow is active, the caller cannot pivot to a different intent (e.g., asking about payments mid-response). Only agent transfer requests ("I want to speak to someone") are honoured. This prevents accidental context switches from misclassified speech.

---

## 4. Detailed Use Case — One-Time Payment (OTP)

**Trigger intent:** `otp` — caller says: "I want to make a payment", "Pay my bill", "One-time payment"

**Pre-requisite:** Caller must be authenticated (Section 2).

### Input Method for All Numeric Fields

All numeric fields (card number, expiry, CVV, routing number, account number) follow the same input pattern:

| Attempt | Voice | DTMF (Keypad) | Prompt Mentions DTMF? |
|---------|-------|---------------|----------------------|
| **1st attempt** | Active — caller speaks the numbers | Active — system accepts keypad input silently | **No** — prompt only asks caller to speak |
| **2nd attempt (retry)** | Active | Active | **Yes** — prompt explicitly says "or type it on your keypad" |

- DTMF is always on from the first attempt, but is only explicitly mentioned as a fallback option starting from the second attempt.
- **No `#` key required** — the system auto-detects input completion.

### Flow

```
Step 1: Card/Bank Account Ownership Check
   Bot: "Is the card or bank account under your name?"
   ├─ Yes → Continue
   ├─ No  → Escalate ("I'm sorry, I can't help you with your request.")
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 2: Due Amount Check
   System retrieves the policy's due amount from the API.
   ├─ Due available:
   │    Bot: "Your total due amount is $[amount]. May we proceed with the payment?"
   │    ├─ Yes → Step 4
   │    ├─ No / wants different amount → Step 3
   │    └─ Invalid/Blank → Retry (max 2 retries, then escalate)
   └─ No due available:
        Bot: "There is currently no premium due. However, you can pay for the
              premium amount of $[amount]. Would you like to make that payment now?"
        ├─ Yes → Step 4
        ├─ No  → Exit flow
        └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 3: Custom Amount (if caller wants to pay a different amount)
   Bot: "How much would you like to pay?"
   Caller states amount → Bot confirms: "You'd like to pay $[amount]. Is that correct?"
   ├─ Yes → Step 4
   ├─ No  → Re-ask amount
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 4: Payment Method Selection
   Bot: "Which payment method would you like to use: a card or bank account?"
   ├─ Card  → Step 5a (Card flow)
   ├─ Bank  → Step 5b (Bank/ACH flow)
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 5a: Ready Check (Card)
   Bot: "When you have your card details with you, please say Ready."
   ├─ Ready → Step 6a
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 5b: Ready Check (Bank)
   Bot: "When you have your bank account details with you, please say Ready."
   ├─ Ready → Step 6b
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)
```

---

#### Card Payment Flow

```
Step 6a: Confirm Name on Card
   Bot: "Is the name [authenticated name] the same as it appears on the card?
         If it's a prepaid card, say 'card does not have a name.'"
   ├─ Yes → Step 7a
   ├─ "Card does not have a name" → Decline with prepaid card restriction:
   │    "I'm sorry, we're unable to process payments from prepaid cards
   │     that do not have a name on them." → Escalate
   ├─ No  → Caller provides the name on the card → Continue
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 7a: Collect Card Number
   1st attempt: "Please tell me your 16-digit card number."
   2nd attempt: "Please provide your 16-digit card number, or type it on your keypad."
   (DTMF is silently active on both attempts; no # key required)
   ├─ 16 digits captured → Step 8a
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 8a: Confirm Card Number
   Bot: "Just confirming, the card number is [number], right?"
   ├─ Yes → Step 9a
   ├─ No  → Back to Step 7a (re-collect)
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 9a: Collect Expiry Date
   1st attempt: "Please tell me the card expiry as month and year."
   2nd attempt: "Please provide the expiry, or type it on your keypad."
   Format: MM/YY (e.g., "05/28" or "May twenty-eight")
   ├─ Valid expiry → Step 10a
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 10a: Collect CVV
   1st attempt: "Please tell me your 3 or 4 digit security code."
   2nd attempt: "Please provide your security code, or type it on your keypad."
   ├─ Valid CVV → Step 11 (Final Confirmation)
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)
```

---

#### Bank (ACH) Payment Flow

```
Step 6b: Confirm Name on Account
   Bot: "Please confirm if your name as it appears on the account is [name]."
   ├─ Yes → Step 7b
   ├─ No  → Caller provides the name on the account → Continue
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 7b: Collect Routing Number
   1st attempt: "Please tell me your 9-digit routing number."
   2nd attempt: "Please provide your routing number, or type it on your keypad."
   (DTMF silently active; no # key required)
   ├─ 9 digits captured → Step 8b
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 8b: Collect Account Number
   1st attempt: "Please tell me your account number."
   2nd attempt: "Please provide your account number, or type it on your keypad."
   ├─ Account number captured → Step 9b
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)

Step 9b: ACH Authorization Script (verbatim, recorded)
   Bot reads the legally required script:
   "I will now begin recording your authorization. I am speaking with
   [bank account owner name]. You have requested that US Insurance Company
   debit a one-time ACH payment of $[amount] today, [date] to pay your
   premium. The account we will be using is your [checking/savings] account
   ending in [last 4 digits]. This amount will be deducted from your
   [checking/savings] account on or after [date].
   Do I have your authorization to initiate this payment?"

   ├─ Caller authorizes → Step 11 (Final Confirmation)
   ├─ Caller does not authorize → Exit flow gracefully
   └─ Invalid/Blank → Retry (max 2 retries, then escalate)
```

---

#### Final Steps (Both Card and Bank)

```
Step 11: Final Confirmation
   Bot: "I have a [card/bank] payment of $[amount] for policy [number].
         Is that correct?"
   ├─ Yes → Step 12 (Process Payment)
   ├─ No  → Restart from Step 1
   └─ Unclear → "Please say yes to confirm or no to cancel."

Step 12: Process Payment
   Bot: "Please hold while I process your payment."
   System calls the Payment API (POST /payment/card or /payment/ach,
   JWT-signed payload, 15s timeout).
   ├─ Success:
   │    "Your payment has been processed. Confirmation number: [number].
   │     Please allow 24 to 48 hours for your payment to post if made
   │     online, or 7 to 10 business days if mailed."
   └─ Failure:
        "I'm sorry, the payment could not be processed. [error].
         Please try again or call back."

Step 13: After Completion
   Bot: "Is there anything else I can help you with today?"
```

### Retry Pattern (All Steps)

Every step in the OTP flow follows a consistent retry pattern:

| Attempt | Response Type | Action |
|---------|--------------|--------|
| 1st | Ask | Initial prompt |
| 2nd | InvalidFirst / BlankFirst | Rephrased prompt (DTMF explicitly mentioned for numeric fields) |
| 3rd | InvalidSecond / BlankSecond | Simplified prompt |
| 4th | InvalidThird / BlankThird | Escalate: *"I'm sorry, I can't help you with your request."* |

### PCI Compliance

- During DTMF entry, speech-to-text is suppressed — keypad digits are not transcribed or logged
- Payment data entered via DTMF is never processed by the LLM — it flows directly to the payment API
- The OTP flow uses a **locked context-switch policy** — no intent changes are allowed during payment entry; only explicit escalation is honoured as a safety exit
- Card payments are secured with a JWT token (HS256, 5-minute expiry)
- Prepaid cards without a name are declined with a compliance disclosure

### ACH Compliance

- The ACH authorization script is read **verbatim** and recorded — never paraphrased
- The script includes the caller's name, payment amount, date, account type, and last 4 digits of the account
- The caller must explicitly authorize; without authorization the flow exits gracefully

---

## 5. Post-Authentication Routing & Context Switching

Once authenticated, the caller's original intent is fulfilled. After each use case completes, the bot asks: *"Is there anything else I can help you with today?"*

The caller can switch between use cases. Context-switch policies per flow:

| Policy | Behavior | Flows |
|--------|----------|-------|
| **Open** | Caller can pivot to any intent at any time | FAQ, Escalation |
| **Escalate-only** | Only agent transfer is allowed mid-flow; other pivots are suppressed | Policy, Payment, Loan, Beneficiary, Contact, Document, Privacy |
| **Locked** | No context switch at all; only escalation is honoured as a safety exit | OTP (payment entry) |

---

## 6. Escalation Triggers

Transfer to a live agent occurs when:

- The caller explicitly requests it ("I want to speak to someone")
- Authentication fails after maximum retries (3 total failures)
- A technical error prevents the bot from completing the request (API failure/timeout)
- The caller is identified as "other" persona and attempts a restricted use case
- The request is out of scope (coverage changes, cancellations, claims, legal/medical advice)
- Any use case step exhausts its retry limit (3 failed attempts on a single step)

Escalation message: *"Let me connect you with a representative right away. Please hold while I transfer you."*
