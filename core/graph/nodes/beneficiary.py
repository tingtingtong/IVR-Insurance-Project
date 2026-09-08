"""
Beneficiary management node.

State machine steps:
  "listing"                → fetch + read current beneficiaries, ask action
  "asking_action"          → detect add / remove / done
  "collecting_name"        → collect new beneficiary full name
  "collecting_relationship"→ collect relationship to insured
  "collecting_percentage"  → collect desired percentage
  "confirming_add"         → confirm the new allocation before submitting
  "selecting_remove"       → ask which beneficiary to remove (by name)
  "confirming_remove"      → confirm removal + new allocation
  "confirming_update"      → confirm percentage reallocation
  "complete"               → done — clear active_flow
"""
import time
import re
import aiohttp
from difflib import SequenceMatcher
from langchain_core.messages import AIMessage
from core.graph.state import CNOState
from core.graph.auth_guard import ensure_authenticated, apply_auth_state, merge_auth_state
from core.prompts.retry_prompts import PROMPTS
from config import settings
from utils.call_logger import log_event

MAX_BENEFICIARIES = 5

VALID_RELATIONSHIPS = [
    "spouse", "child", "parent", "sibling", "grandchild",
    "trust", "estate", "charity", "other",
]


async def beneficiary_node(state: CNOState) -> dict:
    ok, auth_state = await ensure_authenticated(state, "beneficiary")
    if not ok:
        return auth_state
    state = apply_auth_state(state, auth_state)

    t0             = time.time()
    customer       = state.get("customer", {})
    access_token   = state.get("access_token", "")
    messages       = state.get("messages", [])
    call_sid       = state.get("call_sid", "unknown")
    policy_number  = customer.get("policyNumber", "")
    step           = state.get("beneficiary_step", "listing")
    # Reset to listing when re-entering after a completed flow
    if step == "complete" or step == "":
        step = "listing"
    edit           = dict(state.get("beneficiary_edit", {}))
    last_human     = _last_human(messages)

    log_event(call_sid, "node_enter", node="beneficiary", step=step,
              input=last_human[:40] if last_human else "")

    # ── Step: Listing — fetch current beneficiaries and present them ─────────
    if step == "listing":
        benes = await _fetch_beneficiaries(policy_number, access_token, call_sid)
        if benes is None:
            from config import settings as _settings
            return merge_auth_state(auth_state, {
                "tts_text": PROMPTS["escalation"]["error"],
                "transfer_to": _settings.twilio_agent_phone_number,
                "current_node": "beneficiary", "active_flow": "",
            })

        edit["current_beneficiaries"] = benes
        listing_text = _format_beneficiaries_tts(benes)

        if not benes:
            tts = (f"There are currently no beneficiaries on file for policy {policy_number}. "
                   "Would you like to add a beneficiary?")
            return merge_auth_state(auth_state, {
                "beneficiary_step": "asking_action",
                "beneficiary_edit": edit,
                "tts_text": tts,
                "current_node": "beneficiary", "active_flow": "beneficiary",
            })

        tts = (f"{listing_text} "
               "Would you like to add a new beneficiary, remove an existing one, or is this information sufficient?")
        return merge_auth_state(auth_state, {
            "beneficiary_step": "asking_action",
            "beneficiary_edit": edit,
            "tts_text": tts,
            "current_node": "beneficiary", "active_flow": "beneficiary",
        })

    # ── Step: Asking action — detect add / remove / done ────────────────────
    if step == "asking_action":
        action = _detect_action(last_human)

        if action == "done" or _is_no(last_human):
            return _done(auth_state, "Is there anything else I can help you with today?")

        if action == "add":
            benes = edit.get("current_beneficiaries", [])
            if len(benes) >= MAX_BENEFICIARIES:
                return _step(auth_state, "asking_action", edit,
                             f"I'm sorry, this policy already has {MAX_BENEFICIARIES} beneficiaries, "
                             "which is the maximum allowed. Would you like to remove one first, or is there anything else I can help with?")
            edit["action"] = "add"
            return _step(auth_state, "collecting_name", edit,
                         "What is the full name of the person you'd like to add as a beneficiary?")

        if action == "remove":
            benes = edit.get("current_beneficiaries", [])
            if len(benes) <= 1:
                return _step(auth_state, "asking_action", edit,
                             "I'm sorry, a policy must have at least one beneficiary. "
                             "You can add a new one first before removing the current one. Would you like to add a beneficiary?")
            names = _list_names(benes)
            edit["action"] = "remove"
            return _step(auth_state, "selecting_remove", edit,
                         f"Which beneficiary would you like to remove? The current beneficiaries are: {names}.")

        if action == "update":
            benes = edit.get("current_beneficiaries", [])
            if len(benes) < 2:
                return _step(auth_state, "asking_action", edit,
                             "There's only one beneficiary at 100%. Would you like to add another beneficiary instead?")
            edit["action"] = "update"
            names = _list_names(benes)
            return _step(auth_state, "collecting_name", edit,
                         f"Which beneficiary's percentage would you like to change? Current beneficiaries: {names}.")

        # Didn't understand
        return _step(auth_state, "asking_action", edit,
                     "I'm sorry, I didn't catch that. Would you like to add a new beneficiary, "
                     "remove an existing one, or change percentages? You can also say 'that's all' if you're done.")

    # ── Step: Collecting name ───────────────────────────────────────────────
    if step == "collecting_name":
        if _is_cancel(last_human):
            return _done(auth_state, "No problem. Is there anything else I can help you with today?")

        name = last_human.strip()
        if len(name) < 2:
            return _step(auth_state, "collecting_name", edit,
                         "Could you please provide the full name?")

        action = edit.get("action", "add")
        if action == "update":
            # Match name against existing beneficiaries
            benes = edit.get("current_beneficiaries", [])
            matched = _match_beneficiary_by_name(name, benes)
            if not matched:
                names = _list_names(benes)
                return _step(auth_state, "collecting_name", edit,
                             f"I couldn't find a beneficiary matching that name. Current beneficiaries are: {names}. "
                             "Please say the name of the person whose percentage you'd like to change.")
            edit["target_name"] = f"{matched['FirstName']} {matched['LastName']}"
            edit["target_index"] = benes.index(matched)
            return _step(auth_state, "collecting_percentage", edit,
                         f"What percentage would you like to assign to {edit['target_name']}?")

        # Add flow
        edit["new_name"] = name
        return _step(auth_state, "collecting_relationship", edit,
                     f"What is {name}'s relationship to the insured? "
                     "For example: spouse, child, parent, sibling, trust, or estate.")

    # ── Step: Collecting relationship ───────────────────────────────────────
    if step == "collecting_relationship":
        if _is_cancel(last_human):
            return _done(auth_state, "No problem. Is there anything else I can help you with today?")

        rel = _extract_relationship(last_human)
        if not rel:
            return _step(auth_state, "collecting_relationship", edit,
                         "I didn't catch the relationship. Valid options are: "
                         "spouse, child, parent, sibling, grandchild, trust, estate, or charity.")
        edit["new_relationship"] = rel
        return _step(auth_state, "collecting_percentage", edit,
                     f"What percentage would you like to assign to {edit.get('new_name', 'this person')}?")

    # ── Step: Collecting percentage ─────────────────────────────────────────
    if step == "collecting_percentage":
        if _is_cancel(last_human):
            return _done(auth_state, "No problem. Is there anything else I can help you with today?")

        pct = _extract_percentage(last_human)
        if pct is None or pct <= 0 or pct > 100:
            return _step(auth_state, "collecting_percentage", edit,
                         "Please provide a percentage between 1 and 100.")

        action = edit.get("action", "add")
        benes = edit.get("current_beneficiaries", [])

        if action == "update":
            # Update existing beneficiary's percentage
            idx = edit.get("target_index", 0)
            target_name = edit.get("target_name", "")
            old_pct = benes[idx].get("Percentage", 0)

            new_list = _redistribute_update(benes, idx, pct)
            if new_list is None:
                return _step(auth_state, "collecting_percentage", edit,
                             "That percentage doesn't work with the current allocation. "
                             "Please provide a valid percentage between 1 and 100.")
            edit["proposed_list"] = new_list
            summary = _format_proposed_tts(new_list)
            return _step(auth_state, "confirming_update", edit,
                         f"Here's the updated allocation: {summary}. Do you confirm this change?")

        # Add flow
        edit["new_percentage"] = pct
        new_list = _redistribute_add(benes, edit)
        if new_list is None:
            return _step(auth_state, "collecting_percentage", edit,
                         "That percentage doesn't leave enough for the existing beneficiaries. "
                         "Please provide a smaller percentage.")
        edit["proposed_list"] = new_list
        summary = _format_proposed_tts(new_list)
        return _step(auth_state, "confirming_add", edit,
                     f"Here's the proposed allocation: {summary}. Do you confirm this addition?")

    # ── Step: Confirming add ────────────────────────────────────────────────
    if step == "confirming_add":
        if _is_yes(last_human):
            result = await _submit_beneficiary_change(
                policy_number, access_token, call_sid,
                "/beneficiary/add", edit.get("proposed_list", []),
                extra={"Beneficiary": {
                    "FirstName": edit.get("new_name", "").split()[0] if edit.get("new_name") else "",
                    "LastName": " ".join(edit.get("new_name", "").split()[1:]) if edit.get("new_name") else "",
                    "Relationship": edit.get("new_relationship", ""),
                    "Percentage": edit.get("new_percentage", 0),
                }},
            )
            if result:
                edit["current_beneficiaries"] = result.get("Beneficiaries", edit.get("proposed_list", []))
                conf = result.get("ConfirmationNumber", "")
                return _step(auth_state, "asking_action", edit,
                             f"Done! {edit.get('new_name', 'The beneficiary')} has been added. "
                             f"Your confirmation number is {conf}. "
                             "Would you like to make any other changes, or is that all?")
            return _step(auth_state, "asking_action", edit,
                         "I'm sorry, there was an error processing that change. Would you like to try again?")

        if _is_no(last_human):
            return _step(auth_state, "asking_action", edit,
                         "Okay, I've cancelled the addition. Would you like to do something else with your beneficiaries?")
        return _step(auth_state, "confirming_add", edit,
                     "Please say yes to confirm or no to cancel.")

    # ── Step: Selecting remove ──────────────────────────────────────────────
    if step == "selecting_remove":
        if _is_cancel(last_human):
            return _done(auth_state, "No problem. Is there anything else I can help you with today?")

        benes = edit.get("current_beneficiaries", [])
        matched = _match_beneficiary_by_name(last_human, benes)
        if not matched:
            names = _list_names(benes)
            return _step(auth_state, "selecting_remove", edit,
                         f"I couldn't find that name. Current beneficiaries are: {names}. "
                         "Please say the full name of the person you'd like to remove.")

        remove_name = f"{matched['FirstName']} {matched['LastName']}"
        edit["remove_name"] = remove_name
        remaining = [b for b in benes if b is not matched]
        new_list = _redistribute_remove(remaining)
        edit["proposed_list"] = new_list
        summary = _format_proposed_tts(new_list)
        return _step(auth_state, "confirming_remove", edit,
                     f"I'll remove {remove_name}. The remaining allocation will be: {summary}. "
                     "Do you confirm this removal?")

    # ── Step: Confirming remove ─────────────────────────────────────────────
    if step == "confirming_remove":
        if _is_yes(last_human):
            result = await _submit_beneficiary_change(
                policy_number, access_token, call_sid,
                "/beneficiary/remove", edit.get("proposed_list", []),
                extra={"RemovedName": edit.get("remove_name", "")},
            )
            if result:
                edit["current_beneficiaries"] = result.get("Beneficiaries", edit.get("proposed_list", []))
                conf = result.get("ConfirmationNumber", "")
                return _step(auth_state, "asking_action", edit,
                             f"Done! {edit.get('remove_name', 'The beneficiary')} has been removed. "
                             f"Your confirmation number is {conf}. "
                             "Would you like to make any other changes, or is that all?")
            return _step(auth_state, "asking_action", edit,
                         "I'm sorry, there was an error processing that change. Would you like to try again?")

        if _is_no(last_human):
            return _step(auth_state, "asking_action", edit,
                         "Okay, I've cancelled the removal. Would you like to do something else?")
        return _step(auth_state, "confirming_remove", edit,
                     "Please say yes to confirm or no to cancel.")

    # ── Step: Confirming update ─────────────────────────────────────────────
    if step == "confirming_update":
        if _is_yes(last_human):
            result = await _submit_beneficiary_change(
                policy_number, access_token, call_sid,
                "/beneficiary/update", edit.get("proposed_list", []),
            )
            if result:
                edit["current_beneficiaries"] = result.get("Beneficiaries", edit.get("proposed_list", []))
                conf = result.get("ConfirmationNumber", "")
                return _step(auth_state, "asking_action", edit,
                             f"Done! The percentages have been updated. "
                             f"Your confirmation number is {conf}. "
                             "Would you like to make any other changes, or is that all?")
            return _step(auth_state, "asking_action", edit,
                         "I'm sorry, there was an error processing that change. Would you like to try again?")

        if _is_no(last_human):
            return _step(auth_state, "asking_action", edit,
                         "Okay, I've cancelled the change. Would you like to do something else?")
        return _step(auth_state, "confirming_update", edit,
                     "Please say yes to confirm or no to cancel.")

    # Fallback — shouldn't reach here
    return _done(auth_state, "Is there anything else I can help you with today?")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _step(auth_state, step: str, edit: dict, tts: str) -> dict:
    """Return a state update that keeps the beneficiary flow active."""
    return merge_auth_state(auth_state, {
        "beneficiary_step": step,
        "beneficiary_edit": edit,
        "tts_text": tts,
        "current_node": "beneficiary",
        "active_flow": "beneficiary",
        "messages": [AIMessage(content=tts)],
    })


def _done(auth_state, tts: str) -> dict:
    """Return a state update that exits the beneficiary flow."""
    return merge_auth_state(auth_state, {
        "beneficiary_step": "complete",
        "beneficiary_edit": {},
        "tts_text": tts,
        "current_node": "beneficiary",
        "active_flow": "",
        "messages": [AIMessage(content=tts)],
    })


async def _fetch_beneficiaries(policy_number: str, access_token: str, call_sid: str) -> list | None:
    """Fetch beneficiary list from API. Returns list or None on error."""
    url = f"{settings.cno_api_base_url}/beneficiary/inquiry"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"PolicyNumber": policy_number},
                                    headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                if resp.status != 200:
                    log_event(call_sid, "api_call", node="beneficiary", api="beneficiary_inquiry",
                              success=False, error=str(body)[:60])
                    return None
                log_event(call_sid, "api_call", node="beneficiary", api="beneficiary_inquiry",
                          success=True, count=len(body.get("Beneficiaries", [])))
                return body.get("Beneficiaries", [])
    except Exception as exc:
        log_event(call_sid, "api_call", node="beneficiary", api="beneficiary_inquiry",
                  success=False, error=str(exc)[:60])
        return None


async def _submit_beneficiary_change(
    policy_number: str, access_token: str, call_sid: str,
    endpoint: str, updated_list: list, extra: dict | None = None,
) -> dict | None:
    """Submit a beneficiary add/remove/update to the API."""
    url = f"{settings.cno_api_base_url}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"PolicyNumber": policy_number, "UpdatedBeneficiaries": updated_list}
    if extra:
        payload.update(extra)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                body = await resp.json()
                log_event(call_sid, "api_call", node="beneficiary", api=endpoint,
                          success=resp.status == 200, status=resp.status)
                return body if resp.status == 200 else None
    except Exception as exc:
        log_event(call_sid, "api_call", node="beneficiary", api=endpoint,
                  success=False, error=str(exc)[:60])
        return None


def _format_beneficiaries_tts(benes: list) -> str:
    """Format beneficiary list for TTS output."""
    if not benes:
        return "There are no beneficiaries on file."
    parts = []
    for b in benes:
        name = f"{b.get('FirstName', '')} {b.get('LastName', '')}".strip()
        rel = b.get("Relationship", "")
        pct = b.get("Percentage", 0)
        pct_str = f"{pct:.0f}" if pct == int(pct) else f"{pct:.1f}"
        parts.append(f"{name}, {rel}, at {pct_str}%")
    return "Your current beneficiaries are: " + "; ".join(parts) + "."


def _format_proposed_tts(benes: list) -> str:
    """Format proposed beneficiary list for confirmation."""
    parts = []
    for b in benes:
        name = f"{b.get('FirstName', '')} {b.get('LastName', '')}".strip()
        pct = b.get("Percentage", 0)
        pct_str = f"{pct:.0f}" if pct == int(pct) else f"{pct:.1f}"
        parts.append(f"{name} at {pct_str}%")
    return "; ".join(parts)


def _list_names(benes: list) -> str:
    """Comma-separated list of beneficiary names."""
    names = [f"{b.get('FirstName', '')} {b.get('LastName', '')}".strip() for b in benes]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _match_beneficiary_by_name(utterance: str, benes: list) -> dict | None:
    """Fuzzy-match a spoken name against the beneficiary list."""
    utterance_lower = utterance.lower().strip()
    best_match = None
    best_score = 0.0

    for b in benes:
        full_name = f"{b.get('FirstName', '')} {b.get('LastName', '')}".strip().lower()
        first = b.get("FirstName", "").lower()
        last = b.get("LastName", "").lower()

        # Exact full name
        if utterance_lower == full_name:
            return b

        # First or last name exact match
        if utterance_lower == first or utterance_lower == last:
            if best_score < 0.85:
                best_match = b
                best_score = 0.85

        # Substring match
        if first in utterance_lower or last in utterance_lower:
            if best_score < 0.8:
                best_match = b
                best_score = 0.8

        # Fuzzy match
        ratio = SequenceMatcher(None, utterance_lower, full_name).ratio()
        if ratio > best_score and ratio >= 0.6:
            best_match = b
            best_score = ratio

    return best_match


def _redistribute_add(current_benes: list, edit: dict) -> list | None:
    """Build new beneficiary list after adding a person.
    The new person gets their requested percentage; remaining is distributed
    proportionally among existing beneficiaries."""
    new_pct = edit.get("new_percentage", 0)
    if new_pct >= 100:
        return None

    remaining = 100 - new_pct
    old_total = sum(b.get("Percentage", 0) for b in current_benes)

    new_list = []
    running = 0.0
    for i, b in enumerate(current_benes):
        old_pct = b.get("Percentage", 0)
        if old_total > 0:
            adjusted = round((old_pct / old_total) * remaining, 2)
        else:
            adjusted = round(remaining / len(current_benes), 2)
        # Last existing beneficiary gets the remainder to ensure exact 100%
        if i == len(current_benes) - 1:
            adjusted = round(remaining - running, 2)
        running += adjusted
        new_list.append({**b, "Percentage": adjusted})

    # Add the new person
    name_parts = edit.get("new_name", "New Person").split(maxsplit=1)
    first = name_parts[0] if name_parts else "New"
    last = name_parts[1] if len(name_parts) > 1 else ""
    new_list.append({
        "FirstName": first,
        "LastName": last,
        "Relationship": edit.get("new_relationship", "Other").capitalize(),
        "Percentage": new_pct,
    })

    # Final validation
    total = sum(b["Percentage"] for b in new_list)
    if abs(total - 100) > 0.1:
        return None
    return new_list


def _redistribute_remove(remaining_benes: list) -> list:
    """Redistribute percentages proportionally after removing a beneficiary."""
    old_total = sum(b.get("Percentage", 0) for b in remaining_benes)
    new_list = []
    running = 0.0
    for i, b in enumerate(remaining_benes):
        old_pct = b.get("Percentage", 0)
        if old_total > 0:
            adjusted = round((old_pct / old_total) * 100, 2)
        else:
            adjusted = round(100 / len(remaining_benes), 2)
        if i == len(remaining_benes) - 1:
            adjusted = round(100 - running, 2)
        running += adjusted
        new_list.append({**b, "Percentage": adjusted})
    return new_list


def _redistribute_update(benes: list, target_idx: int, new_pct: float) -> list | None:
    """Update one beneficiary's percentage and redistribute the rest proportionally."""
    if new_pct >= 100 or new_pct <= 0:
        return None
    if len(benes) < 2:
        return None

    remaining = 100 - new_pct
    others_total = sum(b.get("Percentage", 0) for i, b in enumerate(benes) if i != target_idx)

    new_list = []
    running = 0.0
    other_count = 0
    for i, b in enumerate(benes):
        if i == target_idx:
            new_list.append({**b, "Percentage": new_pct})
            continue
        other_count += 1
        if others_total > 0:
            adjusted = round((b.get("Percentage", 0) / others_total) * remaining, 2)
        else:
            adjusted = round(remaining / (len(benes) - 1), 2)
        # Last other beneficiary gets remainder
        others_remaining = len(benes) - 1
        if other_count == others_remaining:
            adjusted = round(remaining - running, 2)
        running += adjusted
        new_list.append({**b, "Percentage": adjusted})

    total = sum(b["Percentage"] for b in new_list)
    if abs(total - 100) > 0.1:
        return None
    return new_list


def _detect_action(utterance: str) -> str:
    """Detect whether caller wants to add, remove, update, or is done."""
    u = utterance.lower()
    words = set(re.split(r"[^a-z']+", u))

    if any(w in u for w in ["add", "new", "another"]):
        return "add"
    if any(w in u for w in ["remove", "delete", "take off", "take out"]):
        return "remove"
    if any(w in u for w in ["change", "update", "modify", "adjust", "percentage", "percent", "reallocate"]):
        return "update"

    # "done" detection uses word-boundary matching to avoid "no" matching inside "now", "know", etc.
    done_phrases = ["that's all", "that is all", "no thanks", "all set", "i'm good"]
    if any(p in u for p in done_phrases):
        return "done"
    done_words = {"nothing", "done", "sufficient", "fine"}
    if words & done_words:
        return "done"
    # "no" and "good" need word-boundary check
    if "no" in words and "no" not in ("now", "know", "not"):
        return "done"
    if "good" in words:
        return "done"
    # "thanks"/"thank you" only counts as done when not part of a longer request
    if ("thanks" in words or "thank you" in u) and len(words) <= 4:
        return "done"
    return ""


def _extract_relationship(utterance: str) -> str:
    """Extract a relationship from the utterance."""
    u = utterance.lower()
    for rel in VALID_RELATIONSHIPS:
        if rel in u:
            return rel.capitalize()
    # Common aliases
    aliases = {
        "son": "Child", "daughter": "Child", "kid": "Child",
        "husband": "Spouse", "wife": "Spouse",
        "mother": "Parent", "father": "Parent", "mom": "Parent", "dad": "Parent",
        "brother": "Sibling", "sister": "Sibling",
        "grandson": "Grandchild", "granddaughter": "Grandchild",
        "organization": "Charity", "nonprofit": "Charity",
    }
    for alias, rel in aliases.items():
        if alias in u:
            return rel
    return ""


def _extract_percentage(utterance: str) -> float | None:
    """Extract a percentage from speech."""
    # Direct numeric: "50%", "50 percent", "50"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", utterance)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Word-based
    WORD_NUMS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "ten": 10, "fifteen": 15, "twenty": 20, "twenty-five": 25,
        "thirty": 30, "thirty-five": 35, "forty": 40, "forty-five": 45,
        "fifty": 50, "sixty": 60, "seventy": 70, "seventy-five": 75,
        "eighty": 80, "ninety": 90, "hundred": 100,
    }
    lower = utterance.lower()
    for word, val in WORD_NUMS.items():
        if word in lower:
            return float(val)
    return None


def _is_yes(utterance: str) -> bool:
    u = utterance.lower()
    return any(w in u for w in ["yes", "correct", "right", "sure", "ok", "okay", "confirm", "yep", "yeah"])


def _is_no(utterance: str) -> bool:
    u = utterance.lower()
    return any(w in u for w in ["no", "wrong", "cancel", "nope", "incorrect"])


def _is_cancel(utterance: str) -> bool:
    u = utterance.lower()
    return any(w in u for w in ["cancel", "never mind", "nevermind", "forget it", "stop", "go back"])


def _last_human(messages: list) -> str:
    for msg in reversed(messages):
        role = getattr(msg, "type", "") or getattr(msg, "role", "")
        if role == "human":
            return msg.content.strip()
    return ""
