"""Per-turn Twilio <Gather> settings (#79).

Twilio waits `speechTimeout` seconds of silence after the caller stops before it
returns the transcript, so that wait is dead air on every turn. A one-word yes/no
does not need the 3 s a spoken card number or DOB does, so the profile is picked
from the graph state instead of using one global value.
"""
from dataclasses import dataclass

from config import settings

_DEFAULT_HINTS = (
    "yes, no, correct, incorrect, right, wrong, confirm, cancel, repeat, policy, "
    "beneficiary, payment, loan, status, help, agent, transfer, january, february, "
    "march, april, may, june, july, august, september, october, november, december, "
    "nineteen, twenty, sixty, seventy, eighty, ninety"
)
# Never narrower than the default list: even a short-answer turn can get a spoken correction.
_CONFIRM_HINTS = _DEFAULT_HINTS + ", yeah, yep, nope, that's right, that's wrong, that's all"
_CHOICE_HINTS = "card, credit card, debit card, bank, bank account, checking, savings, ach, agent, repeat"

# A last prompt containing one of these means "say yes or no" (used for the timeout retry wording).
CONFIRM_PHRASES = (
    "is that correct",
    "say yes",
    "yes or no",
    "please confirm",
    "repeat those numbers",
    "anything else i can help",
)

# Steps/prompts where the answer is genuinely short. Deliberately NOT every "confirming_*" step:
# the app accepts corrections there ("no, my DOB is 15 June 1965"), which have natural pauses and
# need the default 3 s and the full hints.
_SHORT_STEPS = frozenset({"ach_auth_script"})
_CHOICE_STEPS = frozenset({"choosing_method"})
_SHORT_PROMPT_PHRASES = ("anything else i can help",)


@dataclass(frozen=True)
class GatherProfile:
    name: str
    speech_timeout: int
    hints: str
    speech_model: str | None = None  # None = Twilio default; benchmark before changing


def default_profile() -> GatherProfile:
    return GatherProfile("default", settings.gather_speech_timeout_default, _DEFAULT_HINTS)


def confirm_profile() -> GatherProfile:
    return GatherProfile("confirm", settings.gather_speech_timeout_confirm, _CONFIRM_HINTS)


def choice_profile() -> GatherProfile:
    return GatherProfile("choice", settings.gather_speech_timeout_confirm, _CHOICE_HINTS)


def is_confirm_prompt(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in CONFIRM_PHRASES)


def _is_short_prompt(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in _SHORT_PROMPT_PHRASES)


def profile_for(state: dict | None = None, prompt: str = "") -> GatherProfile:
    """Pick the profile for the next Gather from graph state and the prompt being spoken."""
    state = state or {}
    steps = (state.get("auth_step") or "", state.get("otp_step") or "")
    if any(s in _SHORT_STEPS for s in steps) or _is_short_prompt(prompt):
        return confirm_profile()
    if any(s in _CHOICE_STEPS for s in steps):
        return choice_profile()
    return default_profile()
