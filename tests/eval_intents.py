"""
Intent classification evaluation.

Runs each utterance in eval_intents.json through the router_node
and compares predicted intent against expected.

Produces: accuracy, per-intent precision/recall/F1, confusion matrix.

Usage:
  cd cno_ivr
  venv/Scripts/python tests/eval_intents.py
"""

import asyncio
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.graph.nodes.router import router_node, VALID_INTENTS

# Terminal colours
OK   = "\033[92m"
FAIL = "\033[91m"
BOLD = "\033[1m"
DIM  = "\033[90m"
CYAN = "\033[96m"
RESET = "\033[0m"

EVAL_FILE = os.path.join(os.path.dirname(__file__), "eval_intents.json")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")


def _make_auth_state() -> dict:
    """Minimal authenticated state so router always invokes LLM classification."""
    from langchain_core.messages import HumanMessage
    return {
        "messages": [],
        "authenticated": True,
        "auth_step": "complete",
        "active_flow": "",
        "caller_persona": "insured",
        "call_sid": "eval_intent_test",
    }


async def classify(utterance: str) -> str:
    from langchain_core.messages import HumanMessage
    state = _make_auth_state()
    state["messages"] = [HumanMessage(content=utterance)]
    result = await router_node(state)
    return result.get("current_intent", "")


async def run_eval():
    with open(EVAL_FILE, "r") as f:
        test_cases = json.load(f)

    print(f"\n{BOLD}Intent Classification Evaluation{RESET}")
    print(f"{DIM}{'=' * 70}{RESET}")
    print(f"Test cases: {len(test_cases)}\n")

    predictions = []
    correct = 0
    total = len(test_cases)

    for i, tc in enumerate(test_cases):
        utterance = tc["utterance"]
        expected = tc["expected"]
        predicted = await classify(utterance)

        is_correct = predicted == expected
        if is_correct:
            correct += 1
            icon = f"{OK}PASS{RESET}"
        else:
            icon = f"{FAIL}FAIL{RESET}"

        predictions.append({
            "utterance": utterance,
            "expected": expected,
            "predicted": predicted,
            "correct": is_correct,
        })

        print(f"  [{icon}] {utterance[:50]:<50} expected={expected:<15} got={predicted}")

    accuracy = correct / total * 100 if total else 0
    print(f"\n{BOLD}Overall Accuracy: {accuracy:.1f}% ({correct}/{total}){RESET}\n")

    # Per-intent precision / recall / F1
    intents = sorted(VALID_INTENTS)
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)

    for p in predictions:
        exp, pred = p["expected"], p["predicted"]
        if pred == exp:
            tp[exp] += 1
        else:
            fp[pred] += 1
            fn[exp] += 1

    print(f"{BOLD}{'Intent':<18} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Support':>10}{RESET}")
    print(f"{DIM}{'-' * 58}{RESET}")

    results_by_intent = {}
    for intent in intents:
        support = tp[intent] + fn[intent]
        if support == 0:
            continue
        precision = tp[intent] / (tp[intent] + fp[intent]) if (tp[intent] + fp[intent]) > 0 else 0
        recall = tp[intent] / (tp[intent] + fn[intent]) if (tp[intent] + fn[intent]) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        results_by_intent[intent] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": support,
        }

        color = OK if f1 >= 0.9 else (FAIL if f1 < 0.7 else CYAN)
        print(f"  {intent:<18} {color}{precision:>9.1%}{RESET} {color}{recall:>9.1%}{RESET} {color}{f1:>9.1%}{RESET} {support:>10}")

    # Confusion matrix
    print(f"\n{BOLD}Confusion Matrix{RESET}")
    confusion = defaultdict(lambda: defaultdict(int))
    for p in predictions:
        confusion[p["expected"]][p["predicted"]] += 1

    active_intents = sorted(set(p["expected"] for p in predictions) | set(p["predicted"] for p in predictions))
    short = {i: i[:6] for i in active_intents}

    print(f"  {'':>12}", end="")
    for ai in active_intents:
        print(f" {short[ai]:>6}", end="")
    print()

    for ai in active_intents:
        print(f"  {short[ai]:>12}", end="")
        for aj in active_intents:
            count = confusion[ai][aj]
            if count == 0:
                print(f" {DIM}{'·':>6}{RESET}", end="")
            elif ai == aj:
                print(f" {OK}{count:>6}{RESET}", end="")
            else:
                print(f" {FAIL}{count:>6}{RESET}", end="")
        print()

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results = {
        "accuracy": round(accuracy, 2),
        "correct": correct,
        "total": total,
        "by_intent": results_by_intent,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "predictions": predictions,
    }
    results_path = os.path.join(RESULTS_DIR, "intent_eval.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{DIM}Results saved to {results_path}{RESET}")

    return results


if __name__ == "__main__":
    results = asyncio.run(run_eval())
    sys.exit(0 if results["accuracy"] >= 90 else 1)
