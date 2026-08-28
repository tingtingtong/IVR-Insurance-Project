"""
LLM-as-Judge conversation quality evaluation using DeepEval GEval.

Runs E2E call scenarios through the graph, captures transcripts,
and scores them on a 7-dimension rubric using Groq LLM as judge.

Usage:
  cd cno_ivr
  venv/Scripts/python tests/eval_conversation.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK   = "\033[92m"
FAIL = "\033[91m"
BOLD = "\033[1m"
DIM  = "\033[90m"
CYAN = "\033[96m"
RESET = "\033[0m"

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")


# ── Scenario runner (reuses test_call_flow infrastructure) ───────────────────

from langgraph.checkpoint.memory import MemorySaver
from core.graph.graph import build_graph, set_graph
import core.graph.graph as _graph_module
from langchain_core.messages import HumanMessage

if _graph_module.cno_graph is None:
    set_graph(build_graph().compile(checkpointer=MemorySaver()))


SCENARIOS = [
    {
        "name": "policy_inquiry",
        "description": "Caller authenticates and asks about policy status",
        "turns": [
            "five five five one two three four five six seven",
            "July fifteenth nineteen sixty five",
            "John Smith",
            "I want to check my policy status",
            "goodbye",
        ],
    },
    {
        "name": "loan_inquiry",
        "description": "Caller authenticates and asks about loan balance",
        "turns": [
            "five five five one two three four five six seven",
            "July 15th 1965",
            "John Smith",
            "What is my loan balance?",
            "no thanks goodbye",
        ],
    },
    {
        "name": "faq_and_escalate",
        "description": "Caller asks FAQ then requests escalation",
        "turns": [
            "What is the grace period for a missed payment?",
            "I want to speak to a representative",
        ],
    },
    {
        "name": "auth_failure",
        "description": "Caller gives wrong PII and gets escalated",
        "turns": [
            "five five five one two three four five six seven",
            "January first two thousand",
            "Donald Duck",
        ],
    },
]


async def run_scenario(scenario: dict) -> dict:
    """Run a scenario through the graph and capture the transcript."""
    import uuid
    call_sid = f"eval_{uuid.uuid4().hex[:16]}"
    state = {
        "call_sid": call_sid, "stream_sid": "", "authenticated": False,
        "auth_step": "collecting_phone", "auth_attempts": 0,
        "customer": {}, "access_token": "", "finalized_party": {},
        "candidate_party": {}, "pii_collected": {}, "current_intent": "",
        "current_node": "", "active_flow": "", "slot_attempts": {},
        "tts_text": "", "transfer_to": "", "otp_step": "", "otp_data": {},
        "metric_data": {"intentList": [], "apiCallsList": [], "piiSuccessList": [], "piiFailureList": []},
        "messages": [],
    }

    transcript_lines = []
    for utterance in scenario["turns"]:
        state["messages"] = list(state.get("messages", [])) + [HumanMessage(content=utterance)]
        try:
            result = await _graph_module.cno_graph.ainvoke(
                state, config={"configurable": {"thread_id": call_sid}},
            )
            state = {**state, **result}
            transcript_lines.append(f"CALLER: {utterance}")
            if result.get("tts_text"):
                transcript_lines.append(f"IVR: {result['tts_text']}")
        except Exception as e:
            transcript_lines.append(f"CALLER: {utterance}")
            transcript_lines.append(f"ERROR: {str(e)}")

    return {
        "name": scenario["name"],
        "description": scenario["description"],
        "transcript": "\n".join(transcript_lines),
        "authenticated": state.get("authenticated", False),
        "final_node": state.get("current_node", ""),
        "final_intent": state.get("current_intent", ""),
        "turn_count": len(scenario["turns"]),
    }


# ── LLM-as-Judge scoring ────────────────────────────────────────────────────

CRITERIA = [
    ("greeting_quality", "Rate 1-10: Was the IVR greeting professional, clear, and welcoming? Consider if it identified the company and asked how it can help."),
    ("auth_flow", "Rate 1-10: Was the authentication process smooth with clear instructions for each PII piece? Did it ask for one piece at a time?"),
    ("intent_handling", "Rate 1-10: Did the system correctly understand and route the caller's intent? Did it provide relevant information?"),
    ("response_quality", "Rate 1-10: Were responses natural, concise, and suitable for a voice IVR? Were they 1-3 sentences max?"),
    ("error_recovery", "Rate 1-10: When invalid input was given, did the system recover gracefully with helpful re-prompts?"),
    ("conversation_flow", "Rate 1-10: Was the conversation logical, efficient, and free of unnecessary repetition?"),
    ("closure", "Rate 1-10: Did the call end properly with appropriate resolution or transfer?"),
]


async def score_with_llm(transcript: str, scenario_desc: str) -> dict:
    """Score a transcript using Groq LLM as judge."""
    from langchain_groq import ChatGroq
    from langchain_core.messages import SystemMessage, HumanMessage as HM
    from config import settings

    llm = ChatGroq(
        model=settings.groq_model,
        temperature=0,
        api_key=settings.groq_api_key,
    )

    scores = {}
    for name, criterion in CRITERIA:
        prompt = f"""You are evaluating an IVR (phone system) conversation for quality.

Scenario: {scenario_desc}

Transcript:
{transcript}

Evaluation criterion: {criterion}

Respond with ONLY a JSON object: {{"score": <1-10>, "reason": "<brief explanation>"}}
"""
        try:
            response = await llm.ainvoke([
                SystemMessage(content="You are a QA evaluator for IVR systems. Respond only with valid JSON."),
                HM(content=prompt),
            ])
            text = response.content.strip()
            # Extract JSON from response
            if "{" in text:
                json_str = text[text.index("{"):text.rindex("}") + 1]
                parsed = json.loads(json_str)
                scores[name] = {"score": parsed.get("score", 5), "reason": parsed.get("reason", "")}
            else:
                scores[name] = {"score": 5, "reason": "Could not parse LLM response"}
        except Exception as e:
            scores[name] = {"score": 5, "reason": f"Error: {str(e)[:80]}"}

    return scores


async def run_eval():
    print(f"\n{BOLD}LLM-as-Judge Conversation Quality Evaluation{RESET}")
    print(f"{DIM}{'=' * 70}{RESET}")
    print(f"Scenarios: {len(SCENARIOS)} | Dimensions: {len(CRITERIA)}\n")

    # Start mock API
    from tests.test_call_flow import start_mock_api, stop_mock_api
    mock_proc = start_mock_api()

    all_results = []

    for scenario in SCENARIOS:
        print(f"\n{CYAN}Scenario: {scenario['name']}{RESET} — {scenario['description']}")

        # Run scenario
        run_result = await run_scenario(scenario)
        print(f"  Turns: {run_result['turn_count']} | Auth: {run_result['authenticated']} | "
              f"Node: {run_result['final_node']} | Intent: {run_result['final_intent']}")

        # Score with LLM
        scores = await score_with_llm(run_result["transcript"], scenario["description"])

        avg_score = sum(s["score"] for s in scores.values()) / len(scores) if scores else 0
        print(f"  {BOLD}Average Score: {avg_score:.1f}/10{RESET}")

        for name, data in scores.items():
            color = OK if data["score"] >= 7 else (FAIL if data["score"] < 5 else CYAN)
            print(f"    {name:<22} {color}{data['score']:>2}/10{RESET}  {DIM}{data['reason'][:60]}{RESET}")

        all_results.append({
            **run_result,
            "scores": scores,
            "avg_score": round(avg_score, 2),
        })

    stop_mock_api(mock_proc)

    # Summary
    overall_avg = sum(r["avg_score"] for r in all_results) / len(all_results) if all_results else 0
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}Overall Average: {overall_avg:.1f}/10{RESET}")

    # Per-dimension averages
    print(f"\n{BOLD}{'Dimension':<22} {'Average':>8}{RESET}")
    print(f"{DIM}{'-' * 30}{RESET}")
    for name, _ in CRITERIA:
        dim_scores = [r["scores"][name]["score"] for r in all_results if name in r["scores"]]
        if dim_scores:
            avg = sum(dim_scores) / len(dim_scores)
            color = OK if avg >= 7 else (FAIL if avg < 5 else CYAN)
            print(f"  {name:<22} {color}{avg:>7.1f}{RESET}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results = {
        "overall_avg": round(overall_avg, 2),
        "scenarios": all_results,
        "dimensions": {
            name: {
                "avg": round(sum(r["scores"][name]["score"] for r in all_results if name in r["scores"])
                             / max(1, len([r for r in all_results if name in r["scores"]])), 2)
            }
            for name, _ in CRITERIA
        },
    }
    results_path = os.path.join(RESULTS_DIR, "conversation_eval.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n{DIM}Results saved to {results_path}{RESET}")

    return results


if __name__ == "__main__":
    results = asyncio.run(run_eval())
    sys.exit(0 if results["overall_avg"] >= 6.0 else 1)
