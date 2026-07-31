"""
Interactive / automated test for RealtimeAuthSession.

Uses text injection instead of audio — no microphone or audio hardware needed.
The model receives typed text as conversation items and responds with audio+transcript.
We display the transcript (response.audio_transcript.delta) so you can read it.

Modes:
  python tests/test_realtime_auth.py              → interactive REPL
  python tests/test_realtime_auth.py auto         → run 3 scripted scenarios

Requires:
  .env with OPENAI_API_KEY
  Mock insuranceCompany API running: python tests/mock_cno_api.py
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.realtime_auth import RealtimeAuthSession


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session(call_id: str, auth_done_event: asyncio.Event, result_box: list):
    """Create a RealtimeAuthSession wired to text-mode callbacks."""

    async def on_audio(mulaw_bytes: bytes):
        pass  # discard audio in text test mode

    async def on_auth_done(result: dict):
        result_box.append(result)
        auth_done_event.set()

    session = RealtimeAuthSession(
        call_sid=call_id,
        on_audio=on_audio,
        on_auth_done=on_auth_done,
    )
    return session


def _patch_for_display(session: RealtimeAuthSession, verbose: bool = True):
    """Monkey-patch _dispatch to print transcript + function calls."""
    original = session._dispatch

    async def patched(event: dict):
        t = event.get("type", "")

        if t == "response.output_audio_transcript.delta":
            if verbose:
                print(event.get("delta", ""), end="", flush=True)

        elif t == "response.output_audio_transcript.done":
            if verbose:
                print()  # newline after complete utterance

        elif t == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call" and verbose:
                print(f"\n  [FUNCTION → {item.get('name')}]  args: {item.get('arguments', '{}')}")

        elif t == "session.created" and verbose:
            print("  [OpenAI Realtime API connected]")

        elif t == "error":
            print(f"\n  [API ERROR: {event.get('error', {}).get('message', str(event))}]")

        await original(event)

    session._dispatch = patched


async def _inject(session: RealtimeAuthSession, text: str):
    """Inject a text utterance into the Realtime session as a user message."""
    await session._send({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    })
    await session._send({"type": "response.create"})


async def _wait_for_response(done_event: asyncio.Event, timeout: float = 6.0) -> bool:
    """Wait up to `timeout` seconds. Returns True if auth completed."""
    try:
        await asyncio.wait_for(asyncio.shield(done_event.wait()), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


# ── Interactive mode ──────────────────────────────────────────────────────────

async def run_interactive():
    print("=" * 62)
    print("  insuranceCompany IVR - Realtime Auth  (interactive text mode)")
    print("  Test data:")
    print("    Phone:  555 123 4567  (John Doe, DOB 1978-01-22)")
    print("    Policy: P400567890    (Sarah Johnson, DOB 1985-07-15)")
    print("  Type 'quit' to exit.")
    print("=" * 62)

    auth_done = asyncio.Event()
    result_box: list = []

    session = _make_session("rt-interactive-001", auth_done, result_box)
    _patch_for_display(session, verbose=True)
    await session.start()

    print("\n  IVR: ", end="", flush=True)
    await asyncio.sleep(3)  # let model speak greeting

    while not auth_done.is_set():
        try:
            loop = asyncio.get_event_loop()
            utterance = await loop.run_in_executor(None, lambda: input("\nYou: "))
        except (EOFError, KeyboardInterrupt):
            break

        if utterance.lower() in ("quit", "exit", "q"):
            break
        if not utterance.strip():
            continue

        print("  IVR: ", end="", flush=True)
        await _inject(session, utterance)
        await asyncio.sleep(3)  # wait for model response

    await session.close()

    if result_box:
        r = result_box[0]
        print("\n" + "=" * 62)
        print(f"  Authenticated : {r.get('authenticated')}")
        c = r.get("customer", {})
        if c:
            print(f"  Customer      : {c.get('firstName')} {c.get('lastName')}")
            print(f"  Policy        : {c.get('policyNumber')}")
            print(f"  DOB           : {c.get('dateOfBirth')}")
        print("=" * 62)


# ── Automated scenarios ───────────────────────────────────────────────────────

SCENARIOS = [
    {
        "id":    "rt-a1",
        "name":  "A1: Phone (found) + DOB -> auth",
        "turns": [
            "555 123 4567",
            "January twenty-second nineteen seventy-eight",
        ],
        "expect": True,
    },
    {
        "id":    "rt-a2",
        "name":  "A2: Phone (found) + wrong DOB + correct name -> auth",
        "turns": [
            "555 123 4567",
            "January first two thousand",   # wrong DOB
            "John Doe",                     # name fallback
        ],
        "expect": True,
    },
    {
        "id":    "rt-a3",
        "name":  "A3: Phone not found -> confirm yes -> policy + DOB (Sarah Johnson)",
        "turns": [
            "800 555 1234",                            # not in system
            "yes that is correct",                     # confirm phone
            "P 400 567 890",                           # policy
            "July fifteenth nineteen eighty five",     # DOB
        ],
        "expect": True,
    },
]


async def run_auto():
    print("=" * 62)
    print("  insuranceCompany IVR - Realtime Auth  (automated scenarios)")
    print("=" * 62)

    passes = 0

    for sc in SCENARIOS:
        print(f"\n--- {sc['name']} ---")

        auth_done = asyncio.Event()
        result_box: list = []

        session = _make_session(sc["id"], auth_done, result_box)
        _patch_for_display(session, verbose=True)
        await session.start()

        print("  IVR: ", end="", flush=True)
        await asyncio.sleep(3)  # greeting

        for utterance in sc["turns"]:
            if auth_done.is_set():
                break
            print(f"\nCALLER: {utterance}")
            print("  IVR: ", end="", flush=True)
            await _inject(session, utterance)
            completed = await _wait_for_response(auth_done, timeout=7)
            if not completed:
                await asyncio.sleep(1)  # small buffer between turns

        # Give a final moment for auth_done to fire after last turn
        if not auth_done.is_set():
            await asyncio.sleep(4)

        await session.close()

        got = result_box[0].get("authenticated") if result_box else None
        ok  = got == sc["expect"]
        if ok:
            passes += 1
        tag = "PASS" if ok else "FAIL"
        c   = result_box[0].get("customer", {}) if result_box else {}
        print(f"\n  {tag}  authenticated={got}  "
              f"customer={c.get('firstName', '-')} {c.get('lastName', '-')}")

    print(f"\n{'=' * 62}")
    print(f"  Realtime Auth: {passes}/{len(SCENARIOS)} passed")
    print("=" * 62)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "interactive"
    if mode == "auto":
        asyncio.run(run_auto())
    else:
        asyncio.run(run_interactive())
