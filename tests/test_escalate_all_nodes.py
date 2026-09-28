"""Every real transfer path must be terminal (no Gather after 'let me transfer').

Run:  .venv/Scripts/python tests/test_escalate_all_nodes.py
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.graph.escalate import transfer_now, is_terminal

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODES = ROOT / "core" / "graph" / "nodes"


class TransferNowTests(unittest.TestCase):
    def test_helper_is_always_terminal(self):
        result = transfer_now("Let me transfer you to a representative.")
        self.assertTrue(is_terminal(result))
        self.assertEqual(result["current_node"], "escalation")
        self.assertEqual(result["current_intent"], "escalate")
        twiml_keys = ("current_node", "current_intent")
        self.assertTrue(all(k in result for k in twiml_keys))

    def test_empty_agent_number_still_terminal(self):
        result = transfer_now("Let me transfer you.")
        result["transfer_to"] = ""
        self.assertTrue(is_terminal(result))


class SourceScanTests(unittest.TestCase):
    """Nodes that assign transfer_to must go through transfer_now / current_node=escalation."""

    def test_no_bare_transfer_to_assignments_in_nodes(self):
        offenders = []
        for path in NODES.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            # Direct dict literals that set transfer_to without current_node escalation
            if '"transfer_to"' not in src and "'transfer_to'" not in src:
                continue
            if path.name in ("goodbye.py",):
                continue
            if "transfer_now" not in src and path.name != "escalation.py":
                # escalation.py may still mention the field in a docstring
                if path.name == "escalation.py" and "transfer_now" in src:
                    continue
                if "transfer_now" not in src:
                    offenders.append(path.name)
        self.assertEqual(offenders, [], f"nodes still set transfer_to by hand: {offenders}")

    def test_let_me_transfer_uses_helper(self):
        offenders = []
        for path in NODES.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            if "Let me transfer" in src or "Let me connect you with a representative who can help." in src:
                if "transfer_now" not in src:
                    offenders.append(path.name)
        self.assertEqual(offenders, [], f"transfer TTS without transfer_now: {offenders}")


class NodeReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_missing_number_is_terminal(self):
        from core.graph.nodes.policy import policy_node
        state = {
            "call_sid": "CA_E",
            "authenticated": True,
            "caller_persona": "insured",
            "auth_step": "complete",
            "customer": {},
            "access_token": "tok",
            "messages": [],
        }
        result = await policy_node(state)
        self.assertTrue(is_terminal(result), result)

    async def test_payment_api_shape_missing_policy_is_terminal(self):
        from core.graph.nodes.payment import payment_node
        state = {
            "call_sid": "CA_E2",
            "authenticated": True,
            "caller_persona": "insured",
            "auth_step": "complete",
            "customer": {},
            "access_token": "tok",
            "messages": [],
        }
        result = await payment_node(state)
        self.assertTrue(is_terminal(result), result)

    def test_auth_escalate_helper(self):
        from core.graph.nodes.auth import _escalate
        result = _escalate({"call_sid": "CA_E3", "auth_attempts": 3})
        self.assertTrue(is_terminal(result))
        self.assertEqual(result["current_node"], "escalation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
