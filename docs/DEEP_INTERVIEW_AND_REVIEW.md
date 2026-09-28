# Insurance IVR — Deep Interview Guide + Code Review

This document is grounded in the current codebase, not generic IVR theory. Use it to interview someone who built or owns this system, or to prepare as a candidate.

There are two parts:

1. **Deep interview questions** — grouped by area, with what a strong answer looks like *for this repo*
2. **Issues and improvements** — real findings from the code, ranked by severity

---

## How this system actually works (read this first)

Do not recite the README blindly. The README describes one architecture; the code implements two.

**Path A — TwiML Gather (what `/webhook/voice` actually does today)**

```
Caller → Twilio PSTN/browser → POST /webhook/voice
  → Twilio <Gather input="speech"> (Twilio STT)
  → POST /webhook/gather
  → LangGraph (thread_id = CallSid)
  → Twilio <Say voice="Polly.Joanna">
```

**Path B — Media Streams (what `/stream` and the README describe)**

```
Caller → Twilio Media Stream WebSocket /stream
  → Deepgram Nova-2 STT
  → LangGraph
  → ElevenLabs TTS (μ-law 8 kHz)
  → Twilio audio frames
  → local WebRTC VAD for barge-in
```

Auth can also be swapped to **OpenAI Realtime** (`AUTH_MODE=realtime`) for the identity portion only, then it hands off to Path B.

LangGraph is a star graph: `START → router → {policy, payment, otp, loan, beneficiary, contact, document, privacy, faq, escalation, goodbye} → END`. There is no auth node in the graph. Restricted nodes call `ensure_authenticated()` as a guard. Checkpointer is `MemorySaver` even when Postgres is up. Redis holds a parallel session blob. Conversation history is an in-memory dict write-through to Postgres.

If a candidate cannot explain why auth is a guard instead of a graph node, or why there are two voice paths, they do not know this system.

---

# Part 1 — Deep Interview Questions

For each question: ask it, then use the follow-ups. Strong answers cite files and trade-offs, not slogans.

---

## A. Architecture and call lifecycle

### A1. Walk me through one inbound call, turn by turn, from the first SIP invite to hangup.

**Strong answer should cover:**

- Twilio hits `POST /webhook/voice` (`webhooks/twilio_voice.py`).
- Session is created in Redis; `start_call()` writes the dashboard record.
- ANI pre-check: last 10 digits of `From` go to `party_search`. On hit, Redis is seeded with `pii_collected.phoneNumber`, `candidate_party`, `auth_step=collecting_dob` so the caller is not asked for their phone.
- Recording is started via Twilio REST (`calls(sid).recordings.create`).
- Greeting is spoken with `<Gather>`. Subsequent turns hit `/webhook/gather`.
- First graph invoke may inject ANI state; after that LangGraph’s checkpointer (thread_id = CallSid) owns conversation state.
- Transfer: TwiML `<Dial>` then hangup. Goodbye: `<Say>` then hangup. Errors: speak error, dial agent.

**Follow-ups:**

- Where does ANI data live after the first turn? (Redis flag is cleared; graph checkpoint should keep it.)
- What happens if `party_search` throws during ANI? (Logged, call continues as unauthenticated.)
- Why start recording on every call, including payments?

### A2. Why are there two complete voice pipelines? Which one is production?

**Strong answer:** Path A (TwiML Gather + Polly) is the webhook path used by the TwiML App / ALB voice URL. Path B (Deepgram + ElevenLabs + VAD) is the Media Stream handler. They do not share STT, TTS, barge-in, or DTMF collection. A candidate who only recites the README diagram has not read `twilio_voice.py`.

**Follow-up:** What breaks if Twilio is pointed at `/twilio/voice` vs `/webhook/voice` vs `/stream`? (README post-deploy checklist lists both `/twilio/voice` and `/webhook/voice` — those are different routers.)

### A3. Why is authentication a guard inside each service node instead of a graph node?

**Expected design argument (`core/graph/auth_guard.py`):**

- Intent is known *before* auth finishes. The caller said “check my policy”; `active_flow` is set to `policy` so after PII collection the same node continues.
- A dedicated auth node would need extra edges: auth → every service node, plus “auth still in progress → END”.
- `ensure_authenticated` return values:
  - `(True, None)` — already authed + persona set
  - `(True, auth_state)` — just completed this turn; merge TTS (“Thank you, Jane.” + policy answer)
  - `(False, auth_state)` — still collecting PII; return immediately
- Persona `"other"` is blocked at the guard, not after the API call.

**Trap:** “Because LangGraph can’t do auth” is wrong. This is a product choice.

### A4. Explain `CNOState`. What is `active_flow` vs `current_intent` vs `current_node`?

| Field | Meaning |
|---|---|
| `current_intent` | Router’s classification this turn (`policy_info`, `otp`, …) |
| `current_node` | Last node that ran |
| `active_flow` | Which multi-turn machine owns the conversation (`otp`, `beneficiary`, `auth`, …) |
| `pending_intents` | Extra intents from “check my policy and make a payment” |
| `auth_step` | Auth sub-state machine |
| `otp_step` / `otp_data` | Payment machine — **also reused by contact, document, privacy** |
| `caller_persona` | `payor` / `insured` / `owner` / `other` |
| `messages` | LangChain messages with `add_messages` reducer |

**Follow-up:** What happens if `active_flow` is empty but `authenticated` is false? Router will LLM-classify. FAQ has no guard. Policy/payment/otp will start auth and set `active_flow`.

### A5. You compile with a checkpointer. Why is it `MemorySaver` even when Postgres is reachable?

**The code in `main.py` lifespan:**

1. Port-check Postgres (2s TCP).
2. Sync `PostgresSaver(conn).setup()` to create checkpoint tables.
3. Close that connection.
4. Compile with `MemorySaver()`.

Comment claims sync `PostgresSaver` cannot `aget_tuple` for `ainvoke`. `langgraph-checkpoint-postgres` *does* ship `AsyncPostgresSaver`. This is a known production defect: in-flight call state dies on deploy, crash, or a second ECS task.

**Follow-up:** If you scale ECS to 2 tasks, what happens to a caller mid-auth? ALB can send the next `/webhook/gather` to the other task; that task has an empty MemorySaver → auth restarts or state looks blank.

### A6. Redis session vs LangGraph checkpoint vs `conversation_store`. Why three stores?

| Store | Role | Persistence |
|---|---|---|
| LangGraph checkpointer | Conversation + slot state for `ainvoke` | **In-process MemorySaver** |
| Redis `cno:session:{CallSid}` | ANI seed, WS path full state, 1h TTL | Redis (or fakeredis fallback) |
| `conversation_store` | Dashboard/transcripts/events | In-memory dict (max 100) + Postgres write-through |

**Webhook path:** after first ANI inject, graph checkpoint is source of truth; Redis is mostly unused per turn.

**WebSocket path:** Redis is loaded, mutated, saved every turn *and* passed into `ainvoke` — dual write, easy to diverge.

**Follow-up:** `SessionService.get_state` re-inits a blank session if the key is missing. What happens after a 1-hour call, or a Redis flush?

### A7. The graph is `START → router → one node → END` every turn. Why not keep the caller inside a subgraph until the flow completes?

**Strong answer:** Telephony is turn-based. Twilio Gather (or Deepgram endpointing) delivers one utterance, you must TwiML-respond within Twilio’s timeout. Returning to END lets the HTTP/WS handler speak TTS and wait. Multi-turn is encoded in `active_flow` + `otp_step` / `beneficiary_step` / `auth_step`, not in graph topology.

**Follow-up:** What is the cost of re-running the router every turn? (LLM latency, misroutes, keyword overrides, context-switch config.)

---

## B. Router, context switch, and NLU

### B1. The router is an LLM classifier with temperature 0 and max_tokens 20. Why also have keyword lists?

Because the LLM is wrong in predictable ways. Code has already patched:

| Patch | File | Why |
|---|---|---|
| Goodbye keywords before auth wall | `router.py` ISSUE-2-002 | “Bye” was sending people into phone collection |
| Escalation keywords always win | `_ESCALATION_KEYWORDS` | Safety exit |
| Privacy phrases before escalate | `_PRIVACY_PHRASES` | “how do you use my data” classified as escalate |
| Confirmation-no in `escalate_only` | ISSUE-2-001 | “no that is wrong” leaked to agent |
| `collecting_caller_name` owns the turn | names like “Person” / “Agent” |
| Groq 503/429 retry | ISSUE-3-001 | otherwise immediate agent transfer |

**Follow-up:** Is `"help"` in `_ESCALATION_KEYWORDS` a good idea? (“I need help with my premium” becomes escalate.) Same for `"person"`.

### B2. Explain `CONTEXT_SWITCH_CONFIG`.

```
otp         → locked          # PCI; only escalate escapes (checked before lock)
payment     → escalate_only
policy      → escalate_only
...most     → escalate_only
faq         → open
escalation  → open
```

- `locked`: no LLM call; force `_FLOW_TO_INTENT[active_flow]`.
- `escalate_only`: LLM still runs; non-escalate intents overwritten back to the flow.
- `open`: full classification.

**Follow-up:** OTP is locked, but cancel is handled *inside* `otp_node` (`_wants_cancel`). How does “cancel the payment” reach the node if the router is locked? Because locked still routes to `otp`, and the node inspects the utterance. Escalation never reaches the node — router short-circuits to `escalate`.

### B3. How do multi-intent utterances work? (“I want my policy status and to make a payment”)

1. Router prompt asks for comma-separated labels.
2. First intent is `current_intent`; rest go to `pending_intents`.
3. **Pre-auth special case (BUG-015):** if `active_flow` is set, auth in progress, `auth_step == collecting_phone`, no pending yet, and utterance > 15 chars, router still classifies once to queue extras.
4. After a flow clears `active_flow`, a confirmation word (`yes`, `sure`, `next`…) dequeues the next intent.
5. `append_pending_hint` adds a spoken hint so the caller knows to confirm.

**Follow-up:** What if the caller never says “yes”? Queue sits until they say something that classifies as a new intent — then pending is replaced, not merged (`pending = classified[1:]`).

### B4. `max_tokens=20` and “comma-separated if multiple”. Are those compatible?

A borderline design. `policy_info,payment,beneficiary` fits; a chatty model that wraps with explanation gets truncated and falls through to `faq` (empty parse → `classified = ["faq"]`). On exception, fallback is `escalate` if no `active_flow`.

### B5. Why is the system prompt still talking about `[verify_caller]` and `[escalate_to_agent]` functions?

`CNO_SYSTEM_PROMPT` describes a tool-calling agent. This codebase is a **deterministic graph with Python nodes**, not an agent with tools. Service nodes (policy, payment, loan, FAQ) still send that prompt to the LLM *only to phrase TTS*. The LLM cannot call those functions. This is prompt drift / copy-paste from an earlier agent design.

**Great candidate:** “I would split a short voice-phrasing prompt from the auth/compliance spec, and never tell the model it has tools it doesn’t have.”

---

## C. Authentication, PII, and personas

### C1. Draw the auth state machine. What is 2-factor here?

Steps in `auth.py`:

```
collecting_phone
  ├ found → collecting_dob
  ├ API error → collecting_policy (skip confirm)
  ├ not found → confirming_phone
  └ IDK → collecting_policy
confirming_phone → yes: collecting_policy / no: collecting_phone
collecting_policy
  ├ found → collecting_dob
  └ miss or IDK → escalate
collecting_dob
  ├ match → (multi-policy ? collecting_policy_selection : collecting_caller_name)
  ├ first mismatch → confirming_dob
  └ second mismatch or IDK → collecting_name
collecting_name → match or escalate
collecting_caller_name → persona
```

`check_auth_success` (`party_search.py`): any of

- phone + DOB
- phone + name (fuzzy 0.80)
- policy + DOB
- policy + name (fuzzy 0.80)

That is 2-of-N, not 2-of-2 of a fixed set. Name is fuzzy via `difflib.SequenceMatcher`.

### C2. ANI matched. Why still ask for DOB and then the caller’s name?

ANI is only a lookup key, not proof of identity (shared household phones, spoofing, wrong party on `parties[0]`). DOB/name is the second factor. `collecting_caller_name` is **post-auth persona**, not identity proof — it decides whether this speaker is payor/insured/owner/other.

### C3. `result["parties"][0]` — what is wrong with that?

Phone numbers can map to multiple parties (husband/wife, parent/child). The code takes the first record, then later matches DOB/name against *that* record. A second party on the same phone never gets a chance. Multi-policy *on one party* is handled (`collecting_policy_selection` by last 4). Multi-party is not.

### C4. Persona matching uses exact word, startswith, and 0.75 fuzzy. Attack it.

`_match_persona`:

- “John” matches “John Smith” (word in parts, len > 1).
- “Johnson” matches “John” (startswith).
- “Jon” vs “John” at 0.75.

False positives: common first names, STT “Ann” / “Anne”, a friend named similarly. False negatives: nicknames (“Bob” vs “Robert”) unless fuzzy lucks out.

`"other"` is then offered a transfer. If matching is too loose, a non-owner hears policy data. If too tight, real owners get blocked.

### C5. When is the access token acquired, and what if it fails?

On transition to `collecting_caller_name` with `authenticated=True` (and a safety path on `complete`). `acquire_access_token(partyKey, companyCode)`. Empty token → auth `failed` + escalate. Realtime path does the same in `_on_realtime_auth_done`. Token is stored in graph/Redis state for the rest of the call.

**Follow-up:** Token in Redis with 1h TTL, no rotation, no encryption at rest. ElastiCache in this Terraform has no AUTH token.

### C6. Slot retries vs `max_auth_attempts`. Which one actually fires?

Per-slot: `blank`/`asked` counters; after 2 blanks → `_escalate`. Global `auth_attempts >= MAX_AUTH_ATTEMPTS` (default 3) at node entry. `_escalate` sets `auth_attempts` to max so re-entry cannot restart. These two counters are easy to get out of sync in an interview — make the candidate walk a “mumbled phone twice, then wrong DOB” path.

---

## D. Voice, latency, and barge-in

### D1. Deepgram settings: `endpointing=300`, `utterance_end_ms=1000`, `no_delay=true`. What do they mean on an 8 kHz phone line?

- `endpointing` 300 ms silence → finalize.
- `utterance_end_ms` 1000 → finalize if no new words for 1s (helps trailing speech).
- `no_delay` + `interim_results` → partials stream; handler **ignores non-final** (`if not is_final: return`).
- Keywords boost: policy number, beneficiary, premium, paid-to-date, autopay.

**Trade-off:** 300 ms is aggressive; elderly/slow speakers get cut off. 1000 ms feels laggy. This is a product knob, not a constant of nature.

### D2. How does barge-in work, and why can it false-trigger?

`VADService`: μ-law → PCM, WebRTC VAD aggressiveness 3, 20 ms frames, 3 consecutive speech frames = 60 ms → fire. Then `clear` event to Twilio to flush the TTS buffer.

False triggers: hold-music bleed, “uh-huh”, line noise, echo of TTS if echo cancellation is weak. Mitigation in code is thin: only run VAD while `_speaking`, reset on TTS start/end, ignore after first fire until reset.

**Webhook path has no barge-in.** Twilio Gather does not play-and-listen simultaneously the way Media Streams do.

### D3. `_on_transcript` drops the utterance if `_processing` is locked. Is that correct?

It prevents overlapping `ainvoke` (good). It **silently drops** a final transcript (bad). After barge-in, the interrupting utterance might be the one that gets dropped if TTS cancel + graph of the previous turn still holds the lock. A strong candidate proposes a one-slot pending utterance queue.

### D4. Why is there 15–20 s of silence on policy lookup?

Documented in `docs/production_issues_and_solutions.md` and still true:

1. `holding_inquiry` / `payment_history` with 10s aiohttp timeout.
2. Then LLM phrasing (`temperature=0.3`, last 4 messages).
3. No filler TTS (“Let me look that up”).
4. New `aiohttp.ClientSession()` per call — no connection pool, TLS handshake every time.

Industry hang-up curve: many callers abandon after ~10 s of dead air.

### D5. TwiML Gather `speechTimeout=3`, `timeout=10`, `actionOnEmptyResult=True`, plus a `<Redirect>` to `/webhook/gather`. What happens on silence?

Twilio posts empty `SpeechResult` → handler returns timeout prompt. The extra `<Redirect>` is a safety net if Gather never fires; it can also **double-hit** gather with empty speech. `actionOnEmptyResult=True` already covers empty results.

---

## E. Payments and PCI

### E1. “Card numbers never go through the LLM.” Is that fully true?

**Partially.**

- Intent: DTMF collector on Media Streams; TwiML `input="dtmf speech"` on webhook (`BUG-016`).
- `otp` flow is `locked` so the router does not classify PAN as an intent.
- Redis masks `card_number`/`account_number` last-4 and CVV (`BUG-029`) on save.
- Luhn validation before API (`BUG-017` / `BUG-018`).

**But:**

- Spoken PAN is allowed (“or you can read it out loud”).
- Progressive capture **reads groups back** (“I received the first four: one two three four”).
- **CVV is collected via STT and spoken back in full** (`confirming_cvv`: `"Security code {_spell_digits(cvv)}"`).
- **Every call is recorded**, including payment turns.
- Webhook `gather_payment` first `ainvoke`s with `HumanMessage(content=collected_number)` — the PAN is a graph message on the first invoke, *then* a second invoke sets `otp_step=dtmf_complete`.

PCI-DSS: SAD (CVV) must not be stored or even logged; PAN storage has strict requirements; call recording of CVV/PAN is a finding. Reading CVV aloud is a finding.

### E2. Walk the OTP state machine. Where can the caller cancel?

`start → choosing_method → (card DTMF | ACH script) → confirmations → processing → complete`. Cancel (`_wants_cancel`) is accepted on any step except `start`/`complete`, clears `otp_data`, releases `active_flow`. ACH requires the word `"authorize"` in the utterance or the flow aborts to “anything else?”.

### E3. Why a payment JWT (`X-Payment-JWT`) in addition to the access token?

`payment_api.py` signs `{policyNumber, amount, iat, exp}` HS256, 5-minute TTL. Intent: bind amount+policy so a stolen access token cannot charge an arbitrary amount without the JWT secret. If `CNO_JWT_SECRET` is empty, it uses `"dev-fallback-secret-do-not-use-in-prod"` and logs a warning.

### E4. Contact, document, and privacy store their step in `otp_data`. Why is that dangerous?

`otp_data` is documented as payment PCI state. Contact uses `contact_step`, document `doc_step`, privacy `privacy_step` inside the same dict. A caller who starts a payment, somehow lands in contact (or vice versa), or whose `otp_data` is only partially cleared, gets cross-flow corruption. Redis masking only strips card/account/cvv keys — leftover `contact_step` survives.

---

## F. RAG, LLM, and compliance speech

### F1. Does FAQ require authentication?

No. `faq_node` has no `ensure_authenticated`. That is correct for general knowledge. When RAG hits, the prompt appends `AUTH_OVERRIDE`: “The caller has ALREADY been authenticated. Do NOT ask for phone/policy/DOB.” That override is **unconditional** even for unauthenticated callers. Combined with `CNO_SYSTEM_PROMPT` (“AUTHENTICATION — ALWAYS FIRST”), the model is given contradictory instructions. If the router misfiles “what’s my premium” as `faq`, the model is told not to verify identity.

On RAG miss: canned “I don’t have specific information…” or escalate if `faq_fallback_to_escalate`.

### F2. RAG implementation — what would you change?

`services/rag.py`: langchain `PGVector`, OpenAI `text-embedding-3-small`, `similarity_search(k=3)`, lazy singleton. Failures return `""`.

Issues: lazy init cold-start on first FAQ after deploy; no score threshold (any 3 neighbors, even irrelevant); no hybrid/BM25; no citation; embeddings and chat models are different vendors (OpenAI + Groq); `enable_rag` flag.

### F3. Policy/payment/loan nodes use the LLM only to speak API JSON. Why not a template?

Pros of LLM: natural variation, can use last 4 messages for “and the premium?” follow-ups.

Cons: latency, hallucination of amounts, prompt can leak `CNO_SYSTEM_PROMPT` auth behavior, cost, non-determinism for compliance phrases.

Payment node already appends a **mandatory disclosure** after the LLM (`PROMPTS['payment_disclosure']`). Privacy/ACH/document delivery use verbatim scripts. Inconsistent philosophy: some legal text is code, some is hoped-for LLM behavior.

### F4. How would you stop the model from reading back a full policy number?

Prompt says “last 4 only”, but policy node feeds `holding_inquiry` fields into the LLM with no output filter. TTS normalizer will happily speak whatever came back. A strong answer: structured output / template for numbers, plus a regex guard on `tts_text` before speak.

---

## G. Reliability, scale, and ops

### G1. `/health` returns `{"status":"ok"}`. What does the ALB think when Redis is down?

ALB target group probes `/health` every 15s, matcher 200. Redis down: `SessionService` falls back to **process-local fakeredis** and the app still 200s. Postgres down: MemorySaver anyway. Deepgram/Groq down: health still 200; failures happen mid-call. You can have a “healthy” task that cannot persist sessions or talk to the LLM.

### G2. ECS desired_count is 1, but autoscaling policies exist (CPU, memory, ALBRequestCountPerTarget=10). Is that coherent?

Autoscaling can raise `desired_count` above 1. With `MemorySaver`, the second task does not share graph state. Sticky sessions are not configured on the target group. Horizontal scale without `AsyncPostgresSaver` (or Redis checkpointer) **breaks in-flight calls**. Scaling on CPU is also a weak proxy for concurrent voice calls.

### G3. Connection pooling — what happens at 100 concurrent calls?

Every tool (`party_search`, `holding_inquiry`, `payment_api`, contact, document, beneficiary) does `async with aiohttp.ClientSession()`. Each is a new TCP+TLS client. FD exhaustion (`Too many open files`) is a documented risk. LLM clients are module-level singletons (`_llm = get_llm()`), which is better, but they are created at **import time** — before settings/env in some test paths.

### G4. What is missing for graceful deploys?

No drain: `ecs update-service --force-new-deployment` kills in-flight Gather/WebSocket calls. No pre-stop hook to play “please hold” or wait for `active` calls in `conversation_store`. `skip_final_snapshot = true` and `deletion_protection = false` on RDS. CloudWatch retention 7 days.

### G5. CI only runs `python tests/test_utils.py` as “unit tests”. What does that imply?

There is **no pytest**. Graph/node/card/WS tests are scripts, not collected. Intent eval needs a real Groq key. E2E Twilio runs only on master push. You can merge a broken `router_node` with a green unit-test job.

---

## H. Security and compliance

### H1. Twilio signature validation is optional and off by default. Exploit it.

`validate_twilio_signature` default `False`. When on, URL is `twilio_base_url + path` (not the raw request URL — important behind ALB/ngrok). If off, anyone who can reach the ALB can POST `/webhook/gather` with a fake `CallSid` and `SpeechResult` and drive auth + payment graph.

`/stream` **accepts the WebSocket first**, then checks `?token=` if `ws_auth_token` is set (empty = open). Twilio Media Streams do not use that query token unless TwiML adds it.

### H2. Dashboard Basic Auth vs `/chat`.

`/dashboard` and `/client` use `require_dashboard_auth`, which **no-ops when `DASHBOARD_PASSWORD` is empty**. `/chat/message`, `/chat/sessions`, `/chat/history/{id}` have **no auth at all**. Chat drives the same graph, including OTP if the classifier says so.

### H3. CORS default is `*`. When is that lethal?

`allowed_origins="*"`. Credentials are only enabled when origins are restricted. Combined with an open `/chat` and a passwordless dashboard, a malicious page can call the IVR API from a browser. In prod this should be the admin origin only.

### H4. PII redaction — what does it miss?

`redact_turn` only redacts **human** turns. Comment: “Bot turns never contain raw PII.” False: bot reads back formatted phones, spoken DOB (“I heard January 5th 1990”), last-4 PAN, **full CVV**, names. `update_call_metadata` stores **DOB unmasked** in `pii_captured.dateOfBirth`. Policy/phone are masked. Events via `log_event` can include `input=last_human[:40]` (may contain PAN/DOB) and DOB verify logs with `expected=expected_dob[:7]+"**"`.

### H5. Dockerfile runs as root, exposes 8000, app listens on 8080. Why does it still work on ECS?

ECS maps `containerPort = var.app_port` (8080) and the command is overridden or settings `APP_PORT`. `EXPOSE 8000` is documentation only. Process still runs as root; `.env` if copied via `COPY . .` may bake secrets into the image if the build context includes it.

### H6. Terraform network: public subnets only, Fargate `assign_public_ip = true`, RDS `publicly_accessible = false` but subnet group is **public subnets**. Why is that still wrong?

RDS without a public IP is not internet-reachable, but it sits on public subnets (no private subnet/NAT design). ElastiCache likewise. ALB is HTTP:80 only — no ACM, no TLS. Softphone mic requires HTTPS or localhost (documented Chrome workaround). `DATABASE_URL` including the password is an **environment variable**, not a secret. Redis URL has no AUTH.

---

## I. AWS / Terraform (expect these if the role is platform-leaning)

### I1. Why is the mock CNO API a sidecar in the ECS task (`localhost:8001`)?

`CNO_API_BASE_URL=http://localhost:8001` in the task definition. Production traffic still hits the mock unless someone changes that. Shared task CPU/memory with the IVR. Mock is `essential = true` — if mock dies, the whole task is replaced.

### I2. Dev and prod share one RDS instance with different database names. Risk?

`ivr_${environment}` on one `aws_db_instance`. Blast radius, noisy neighbor, credential model, can’t independently snapshot/restore, `skip_final_snapshot` destroys both. Redis: same cluster, DB 0 vs 1 — better isolation than Postgres-on-one-instance, still one failure domain.

### I3. S3 backend uses both `dynamodb_table` and `use_lockfile = true`. What is going on?

Transitional Terraform state locking (DynamoDB classic + S3 native lockfile). Not wrong, but an interviewer may ask which one is source of truth.

### I4. Secrets Manager `recovery_window_in_days = 0`. Why is that a demo smell?

Immediate secret deletion on destroy; no recovery. Fine for a throwaway env; not for prod keys.

---

## J. Debugging scenarios (give them a symptom)

Use these as table-top incidents.

### J1. “Callers who say goodbye still get asked for their phone number.”

Historical ISSUE-2-002. Auth wall ran before goodbye. Fix: `_caller_wants_goodbye` first. Regression test: unauthenticated “no thanks” — but `"no thanks"` is itself a goodbye keyword, which can **hang up a caller who declined an offer**.

### J2. “I said the date was wrong and got transferred to an agent.”

ISSUE-2-001: confirmation-no classified as escalate. Fix only applies when `cs_mode == escalate_only`. If `active_flow` is empty, LLM can still do it.

### J3. “After deploy, every live caller had to start over.”

MemorySaver. Also in-memory `_calls` deque (last 100) reloads from Postgres on boot, but **graph checkpoint does not**.

### J4. “First FAQ after deploy is slow; the rest are fine.”

RAG lazy `_get_store()` — OpenAI embeddings client + pgvector connection.

### J5. “Card payment DTMF collected, then the bot asked ‘how can I help you’ instead of expiry.”

Look at `gather_payment`: first `ainvoke` with the digit string as a human message (router + otp locked should stay in otp, but `otp_step` is still `collecting_card_dtmf`, so the node tries to parse digits from `last_human` — OK). Second invoke sets `otp_step=dtmf_complete`. If the first invoke already advanced the machine, the second can skip or reset. This double-invoke is a prime debugging question.

### J6. “Dashboard shows DOB in plaintext.”

`update_call_metadata` — by design today. Ask them to fix it without breaking support workflows (show age band vs full DOB).

### J7. “Two callers, same ANI, different people. Wrong policy.”

`parties[0]`.

### J8. “Caller named Person got stuck / transferred during name collection.”

`collecting_caller_name` short-circuit exists because escalation keywords include `"person"`. Ask if the same bug exists for `"help"` as a last name.

---

## K. “What would you change in the next 90 days?” (senior bar)

A strong 90-day plan, in order:

1. **PCI:** DTMF-only PAN and CVV; never speak CVV; pause or suppress recording in payment; do not persist PAN/CVV in graph messages; tokenize via a payment processor (Stripe/Braintree) instead of sending raw card JSON to `cno_api`.
2. **State:** `AsyncPostgresSaver` (or Redis checkpointer); sticky-session or shared state before any scale-out; drop dual Redis/graph writes on the webhook path or make one the authority.
3. **Security:** Twilio signatures on; WS token on; chat behind auth; dashboard password required in prod; CORS allowlist; Redis AUTH; DB URL in Secrets Manager; non-root Docker user; HTTPS on ALB.
4. **Latency:** shared `aiohttp` pool; filler TTS; template the policy/loan/payment readouts; pre-warm RAG; stream TTS.
5. **Voice path:** pick Gather *or* Media Streams as the production path; delete or feature-flag the other.
6. **Tests:** pytest; node-level state-machine tests without live LLM (fake router); PCI unit tests that fail if CVV appears in `tts_text`; load test Gather latency.
7. **Network:** private subnets + NAT; RDS/Redis private; TLS listener.

A candidate who starts with “add more LLM agents” is optimizing the wrong layer.

---

## L. Rapid-fire (expect short, precise answers)

1. Why `WindowsSelectorEventLoopPolicy` in `main.py`? — Playwright/psycopg/asyncio on Windows Proactor incompatibility.
2. Why tee stdout into `ivr.log` + `log_bus`? — SSE live logs for `/client/logs/stream`.
3. What does `add_messages` do on `CNOState.messages`? — append reducer, not overwrite.
4. Why `owner_change` routes to escalation? — not supported in IVR.
5. Why FAQ temperature 0.4 vs router 0? — phrasing vs classification.
6. What is `noOfPII` in customer? — documented, set to 0 in `_build_customer` — dead field.
7. Why Polly on webhook and ElevenLabs on stream? — Gather path has no audio socket to push ElevenLabs frames.
8. `fakeredis` when Redis is down — sessions vanish on process restart; two ECS tasks do not share it.
9. `chat.py` reset looks up `from core.graph.graph import _checkpointer` — that name is **not** defined; `cno_graph` is. Reset of graph state is likely a no-op.
10. Dockerfile Python 3.11 vs README 3.12+ vs `audioop` comment about 3.13 — version story is inconsistent.

---

# Part 2 — Issues and improvements found in this repo

Findings below are from reading the current tree, not from the older `security_audit_report.md` (dated 2026-07-08). Some audit items were fixed (Twilio validator exists, dashboard Basic Auth exists, CORS is configurable, docker-compose binds Redis/Postgres to 127.0.0.1). Several critical items remain.

---

## Critical — fix before real policyholders and real cards

### 1. Graph state is in-process `MemorySaver`

**Where:** `main.py` lifespan.  
**Impact:** Deploy, crash, or a second task loses every in-flight conversation. Autoscaling is therefore unsafe. Checkpoint tables are created and unused.  
**Fix:** `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` (already in `requirements.txt`), or a Redis checkpointer. Add ALB stickiness only as a temporary crutch.

### 2. PCI: CVV spoken back, PAN spoken, full-call recording

**Where:** `otp.py` `confirming_cvv`; progressive card readback; `twilio_voice.py` starts recording on every inbound call.  
**Impact:** CVV (SAD) in TTS, transcripts, and Twilio recordings. Spoken PAN in TTS. Redis masking does not help recordings or bot turns.  
**Fix:** DTMF-only for PAN, expiry, CVV; never echo CVV; use `<Pause>` / stop recording during payment (`Twilio.calls.recordings.update(status=stopped)`); send PAN to a hosted tokenization vault, not `POST /payment/card` with raw `CardNumber`+`CVV`.

### 3. `/webhook/gather-payment` double `ainvoke`, first with raw PAN as a chat message

**Where:** `twilio_voice.py` `gather_payment`.  
**Impact:** Card/account digits enter `messages` via the reducer (`add_messages`) and may persist in the checkpoint. Router/otp run twice per number. State can skip or duplicate steps.  
**Fix:** Single invoke: `{"otp_step": "dtmf_complete", "otp_data": {...}}` without putting PAN in `messages`.

### 4. Unauthenticated `/chat` drives the same graph as production voice

**Where:** `webhooks/chat.py` — no `require_dashboard_auth`.  
**Impact:** Anyone who can reach the ALB can run auth, FAQ, and potentially OTP; `/chat/sessions` lists conversations.  
**Fix:** Same auth as dashboard; disable chat in prod; rate-limit.

### 5. Twilio signature check and WS token default off

**Where:** `settings.validate_twilio_signature = False`, `ws_auth_token = ""`.  
**Impact:** Unauthenticated control of the IVR if the ALB is public (it is: `0.0.0.0/0` port 80).  
**Fix:** Fail closed in `environment=prod`; require signature + WS token; do not accept the WebSocket before checking the token.

### 6. Fallback JWT secret and mock API in the ECS task

**Where:** `payment_api.py` `"dev-fallback-secret-do-not-use-in-prod"`; `ecs.tf` `CNO_API_BASE_URL=http://localhost:8001`.  
**Impact:** Payments may be signed with a public secret and posted to a mock sidecar.  
**Fix:** Require `CNO_JWT_SECRET`; point prod at the real API; do not ship mock as `essential` in prod.

---

## High

### 7. Dual voice stacks (Gather+Polly vs Stream+Deepgram+ElevenLabs)

Two sources of truth for DTMF, transfer, greeting, barge-in, and timeouts. README/post-deploy URLs disagree (`/twilio/voice` vs `/webhook/voice`).  
**Fix:** Choose one production path; feature-flag the other; one transfer implementation.

### 8. Dual session authority (Redis vs checkpoint)

Webhook: checkpoint wins after ANI. WebSocket: Redis blob is passed into `ainvoke` every turn. Message objects vs serialized dicts.  
**Fix:** One store. If webhook-only, drop per-turn Redis writes.

### 9. `parties[0]` and fuzzy persona/name matching

Wrong household member can authenticate; `"John"` / startswith / 0.75 ratio is loose for life-insurance PII. Name factor at 0.80 in `check_auth_success`.  
**Fix:** Disambiguate multiple parties (DOB or last-4 policy first). Tighten persona match (full name or unique last name). Log false-accept metrics.

### 10. Keyword collisions

- `"help"`, `"person"` → escalate  
- `"no thanks"` / `"no thank you"` → goodbye (hangup)  
- Duplicate `"goodbye"` in the set  

**Fix:** Phrase-level classifiers with word boundaries; never treat `"help"` as a hard escalate; goodbye only on unambiguous close.

### 11. PII in dashboard metadata and bot transcripts

DOB stored raw; bot turns not redacted; `log_event(..., input=last_human[:40])`.  
**Fix:** Mask DOB; redact bot TTS; structured PII fields never in free-text events.

### 12. HTTP-only ALB, public subnets, public-IP Fargate, DB password in env

No TLS; RDS/Redis subnet groups are public; `DATABASE_URL` interpolates password into task env.  
**Fix:** ACM + HTTPS listener; private subnets + NAT; Secrets Manager for `DATABASE_URL`; Redis AUTH + encryption in transit.

### 13. Shallow `/health` + fakeredis fallback

ALB keeps routing to a task that cannot share state.  
**Fix:** Readiness vs liveness: ping Redis, Postgres, and optionally Groq. Do not hide Redis failure behind fakeredis in prod.

### 14. `otp_data` reused as a generic scratchpad

Contact/document/privacy steps live next to PAN/CVV.  
**Fix:** `contact_data`, `document_data`, `privacy_data` on `CNOState`.

### 15. No connection pooling (`aiohttp.ClientSession` per call)

FD and latency under concurrency.  
**Fix:** App-lifespan singleton `ClientSession` with `TCPConnector(limit=...)`.

---

## Medium

### 16. Silence during API+LLM (no filler, no streaming TTS)

Abandonment risk. Add “one moment”, overlap TTS.

### 17. RAG: no similarity threshold, lazy init, FAQ AUTH_OVERRIDE always-on

Pre-warm in lifespan; drop chunks below a score; only apply AUTH_OVERRIDE when `authenticated` is true.

### 18. `CNO_SYSTEM_PROMPT` describes tools that do not exist

Split phrasing prompt vs policy prompt. Stop instructing `[verify_caller]`.

### 19. Escalation on any graph exception (`/webhook/gather`) and on router LLM failure (no active_flow)

Transient Groq errors still transfer after retries. Prefer “let me try that again” once, then transfer.

### 20. Dockerfile root user; `COPY . .` may include `.env`; EXPOSE 8000 vs listen 8080; README Python 3.12 vs image 3.11

Multi-stage, non-root, `.dockerignore`, one Python version.

### 21. Tests are not pytest; CI unit job is `test_utils.py` only

Node state-machine tests with a fake LLM; assert CVV never in `tts_text`; graph routing table tests.

### 22. `conversation_store` is a process-local dict (deque maxlen 100)

Dashboard is per-task; SSE logs too (`log_bus`). Multi-task = split-brain UI. Move to Postgres as the read model.

### 23. Chat reset imports `_checkpointer` which does not exist

Dead code; graph threads leak in MemorySaver until process restart.

### 24. Beneficiary cap 5, percentage logic, and live `aiohttp` inside the node

Worth a dedicated review: allocation must sum to 100; API errors escalate; fuzzy name match to remove a beneficiary can remove the wrong person (`SequenceMatcher` again).

### 25. `desired_count = 1` but three autoscaling policies

Either pin to 1 until shared state exists, or implement shared state first.

### 26. Recording URLs stored and presumably playable from the dashboard

Twilio media requires auth to download; if the dashboard proxies or exposes the URL to a browser with Basic Auth only, treat as sensitive. Pause recording on PCI steps.

### 27. `skip_final_snapshot`, `deletion_protection = false`, secret `recovery_window_in_days = 0`

Demo flags left in Terraform. Dangerous if `environment=prod` uses the same modules.

---

## Low / quality

### 28. Greeting asks “How can I help you today?” then chat greeting asks for a phone number — channel inconsistency.

### 29. `_GOODBYE_KEYWORDS` contains `"goodbye"` twice.

### 30. `noOfPII` always 0; metric_data mostly unused.

### 31. stdout tee has no log rotation (`ivr.log` grows forever).

### 32. `get_llm()` at module import in every node — hard to test, captures settings once.

### 33. Privacy node requires auth to hear a general GLBA script — maybe it should not.

### 34. Document node lists document types in one TTS sentence (voice anti-pattern: long lists).

### 35. `eval_intents` in CI uses dummy keys for slot eval then real Groq for intent — slot eval may be vacuously passing.

---

## What is already in good shape (say this in an interview too)

Do not only dump bugs. This system has real engineering in it:

- Auth as a guard with same-turn merge of “Thanks, Jane” + service TTS is a clean pattern.
- Context-switch modes (`locked` / `escalate_only` / `open`) are the right idea for PCI vs FAQ.
- Explicit patches (goodbye vs auth wall, confirmation-no vs escalate, Groq retry, ANI pre-check, pending intents, DTMF for PAN, Luhn, ACH verbatim script, GLBA script, document-to-address-on-file only) show production learning.
- Persona `"other"` cannot hit restricted intents.
- Feature flags: `enable_rag`, `faq_fallback_to_escalate`, `max_auth_attempts`, `llm_provider` groq|bedrock.
- Dashboard analytics over structured `log_event`s (auth failure, STT confidence, graph latency, API errors) is the right observability direction.
- Terraform has the bones of a real deploy (ECS Fargate, ALB, RDS, ElastiCache, Secrets Manager, autoscaling, GH Actions).

The gap is **production hardening** (state, PCI, authn of admin/chat, TLS, pooling, tests), not “we need a smarter model.”

---

## Suggested interview loop (60–75 minutes)

| Min | Segment |
|---|---|
| 0–8 | A1 walkthrough — do they know Gather vs Streams? |
| 8–18 | Auth machine + 2-factor + persona (C1–C4) |
| 18–28 | Router + context switch + a live bug (B1, J1/J2) |
| 28–40 | PCI / OTP (E1–E4) — this is the senior filter |
| 40–50 | State: MemorySaver vs Redis vs dashboard (A5, A6, G2) |
| 50–60 | “90-day plan” (K) and one AWS question (I1 or H6) |
| 60–75 | Debugging scenario of your choice (J3 or J5) |

**Hire signal:** they volunteer MemorySaver, CVV readback, `parties[0]`, and dual pipelines without being prompted.

**No-hire signal:** they describe a single Deepgram→LangGraph→ElevenLabs happy path and call the system PCI-compliant because “DTMF exists.”
