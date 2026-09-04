# Changelog

All notable changes to the IVR system are documented here.
Each version is tagged in git and deployed as a Docker image to ECR.

---

## v1.6.0 — 2026-09-04 (0a8badc)
**Persona matching + dial safety**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| BUG-012 | "John Smith" heard as "Johnson" by STT → persona = "other" → caller blocked from policy info | `_match_persona` used exact word matching only; "johnson" ≠ "john" | Added 3-tier matching: exact word, substring/starts-with, fuzzy (SequenceMatcher ≥ 0.75) | `core/graph/nodes/auth.py` |
| BUG-013 | After failed agent transfer (`<Dial>` fails), IVR restarts from phone collection instead of ending | No TwiML after `<Dial>` — Twilio continues to next instruction (redirect loop) | Added `<Say>` + `<Hangup>` after every `<Dial>` so call ends cleanly on transfer failure | `webhooks/twilio_voice.py` |
| BUG-014 | No logging when "other" persona is blocked from restricted intents | Silent override to escalation | Added `persona_gate_blocked` log event with intent and persona | `core/graph/nodes/router.py` |

---

## v1.5.0 — 2026-09-04 (931ac40)
**Speech loss diagnostics + Gather tuning**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| BUG-010 | Caller's spoken words/sentences silently lost — not in logs or transcripts | `speechTimeout="auto"` cuts off mid-sentence on brief pauses (e.g., phone numbers, dates) | Changed `speechTimeout` from `"auto"` to `"3"` (3s silence before ending) | `webhooks/twilio_voice.py` |
| BUG-011 | No diagnostic data when Twilio sends empty or unexpected gather results | Only `SpeechResult` was logged; other Twilio params ignored | Added `gather_raw` log (all form params), `stt_empty` event, `actionOnEmptyResult=True`, `timeout` 8→10 | `webhooks/twilio_voice.py` |

---

## v1.4.0 — 2026-09-04 (5d8efd6)
**Goodbye fix + auth guards + End Call button**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| BUG-007 | "No, thank you" did not trigger goodbye — caller stuck in auth loop | `_caller_wants_goodbye` matched `"no thank you"` but comma in `"no, thank you"` broke the `in` check | Strip all punctuation before keyword matching using regex `[^a-z0-9 ]` | `core/graph/nodes/router.py` |
| BUG-008 | Phone number said when asked for name → persona = "other" → blocked | No detection of digit input at `collecting_caller_name` step | Detect exactly 10 digits → re-prompt: "I couldn't get the complete number you mentioned. Can you give me a 10-digit phone number?" | `core/graph/nodes/auth.py` |
| BUG-009 | Auth restarts from `collecting_phone` even after phone was verified and party found | State corruption: `auth_step` reset to default `"collecting_phone"` while `candidate_party` still populated | Defensive guard: if `candidate_party` exists and `auth_step == "collecting_phone"`, skip to `collecting_dob` or `collecting_name` | `core/graph/nodes/auth.py` |
| FEAT-004 | Active calls in dashboard stuck as "active" after call ends — no manual override | No single-call end endpoint | Added `POST /dashboard/calls/{call_sid}/end` endpoint + red "End" button on call list items and call detail header | `webhooks/dashboard.py` |

---

## v1.3.0 — 2026-09-04 (34f4562)
**RAG eval expansion**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| FEAT-003 | Only 8 FAQ test cases — insufficient coverage of knowledge base | Test set didn't cover all 14 RAG categories | Expanded to 32 FAQ-only test cases covering all categories in `seed_knowledge.py` | `tests/eval_rag.py` |

---

## v1.2.0 — 2026-09-04 (aa67a99)
**Stale call fix + cleanup endpoint**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| BUG-006 | Calls stay "active" forever in dashboard after caller hangs up | `/webhook/status` callback only logged but never called `end_call()` | Added `end_call()` on terminal statuses: completed, canceled, failed, busy, no-answer | `webhooks/twilio_voice.py` |
| FEAT-002 | No way to batch-clean stale active calls | N/A | Added `POST /dashboard/calls/cleanup` endpoint | `webhooks/dashboard.py` |

---

## v1.1.0 — 2026-09-04 (122a42f)
**Comprehensive structured logging + analytics dashboard**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| FEAT-001 | Insufficient logging — unforeseen issues undetectable | Only basic structlog; no `log_event` in most nodes, no API call timing, no auth guard tracing | Added `log_event` calls to every node entry/exit, API call, LLM invocation, auth guard decision, STT result, graph timing | All node files, `auth_guard.py`, `twilio_voice.py`, tool files |
| FEAT-001b | No call analytics or issue auto-detection | N/A | Built analytics dashboard tab: summary cards, intent/node charts, auto-detected issues (auth failures, low STT confidence, slow graph, API errors) | `webhooks/dashboard.py` |

---

## v1.0.0 — 2026-09-04 (26d317a)
**Auth overhaul + multi-intent + ANI check**

| ID | Issue | Root Cause | Fix | Files |
|----|-------|-----------|-----|-------|
| BUG-001 | "No that is wrong" during confirmation leaks to escalation | LLM mis-classifies correction phrases as "escalate" intent | Added `_is_confirmation_no()` check before LLM call in `escalate_only` mode | `core/graph/nodes/router.py` |
| BUG-002 | "Goodbye" blocked by auth wall — caller can't hang up without verifying | Router's `active_flow + not authenticated` check runs before goodbye detection | Moved `_caller_wants_goodbye()` check BEFORE auth redirect in router | `core/graph/nodes/router.py` |
| BUG-003 | DOB mismatch immediately escalates — no retry | Single attempt: DOB wrong → name fallback → escalate | Added 1 DOB retry before falling through to name collection | `core/graph/nodes/auth.py` |
| BUG-004 | Multi-intent utterances lose the second intent | Router only classified first intent | Parse comma-separated intents, queue extras in `pending_intents`, dequeue on confirmation | `core/graph/nodes/router.py` |
| BUG-005 | No ANI pre-check — phone always asked even when caller ID available | No phone lookup on call start | Added ANI check in `/webhook/voice`: search by caller phone, pre-populate session state, skip to DOB if found | `webhooks/twilio_voice.py` |

---

## v0.x — Pre-2026-09-04
**Initial system: LangGraph IVR, dual LLM (Groq/Bedrock), AWS infra, Terraform, CI/CD**

See commits `e8b8387` through `1da061b` for initial build history.
