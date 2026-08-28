"""
Slot filling (PII extraction) accuracy evaluation.

Tests the PII normalization functions against known inputs.

Usage:
  cd cno_ivr
  venv/Scripts/python tests/eval_slots.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.pii_validator import normalize_phone, normalize_policy_number, normalize_dob

# Terminal colours
OK   = "\033[92m"
FAIL = "\033[91m"
BOLD = "\033[1m"
DIM  = "\033[90m"
RESET = "\033[0m"

EVAL_FILE = os.path.join(os.path.dirname(__file__), "eval_slots.json")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")

SLOT_NORMALIZERS = {
    "phone": normalize_phone,
    "policy": normalize_policy_number,
    "dob": normalize_dob,
}


def run_eval():
    with open(EVAL_FILE, "r") as f:
        test_cases = json.load(f)

    print(f"\n{BOLD}Slot Filling Accuracy Evaluation{RESET}")
    print(f"{DIM}{'=' * 70}{RESET}")
    print(f"Test cases: {len(test_cases)}\n")

    results_by_slot = {}
    all_results = []
    correct = 0
    total = len(test_cases)

    for tc in test_cases:
        slot = tc["slot"]
        input_text = tc["input"]
        expected = tc.get("expected") or tc.get("expected_pattern", "")

        normalizer = SLOT_NORMALIZERS.get(slot)
        if not normalizer:
            print(f"  {FAIL}[SKIP]{RESET} Unknown slot type: {slot}")
            continue

        predicted = normalizer(input_text)
        is_correct = predicted == expected

        if is_correct:
            correct += 1
            icon = f"{OK}PASS{RESET}"
        else:
            icon = f"{FAIL}FAIL{RESET}"

        print(f"  [{icon}] {slot:<8} input={input_text[:40]:<40} expected={expected:<15} got={predicted}")

        all_results.append({
            "slot": slot,
            "input": input_text,
            "expected": expected,
            "predicted": predicted,
            "correct": is_correct,
        })

        if slot not in results_by_slot:
            results_by_slot[slot] = {"correct": 0, "total": 0}
        results_by_slot[slot]["total"] += 1
        if is_correct:
            results_by_slot[slot]["correct"] += 1

    overall_accuracy = correct / total * 100 if total else 0
    print(f"\n{BOLD}Overall Slot Accuracy: {overall_accuracy:.1f}% ({correct}/{total}){RESET}\n")

    print(f"{BOLD}{'Slot':<12} {'Accuracy':>10} {'Correct':>10} {'Total':>10}{RESET}")
    print(f"{DIM}{'-' * 42}{RESET}")
    for slot, data in sorted(results_by_slot.items()):
        acc = data["correct"] / data["total"] * 100 if data["total"] else 0
        color = OK if acc >= 90 else FAIL
        print(f"  {slot:<12} {color}{acc:>9.1f}%{RESET} {data['correct']:>10} {data['total']:>10}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    results = {
        "accuracy": round(overall_accuracy, 2),
        "correct": correct,
        "total": total,
        "by_slot": {k: {**v, "accuracy": round(v["correct"] / v["total"] * 100, 2) if v["total"] else 0}
                    for k, v in results_by_slot.items()},
        "predictions": all_results,
    }
    results_path = os.path.join(RESULTS_DIR, "slot_eval.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{DIM}Results saved to {results_path}{RESET}")

    return results


if __name__ == "__main__":
    results = run_eval()
    sys.exit(0 if results["accuracy"] >= 90 else 1)
