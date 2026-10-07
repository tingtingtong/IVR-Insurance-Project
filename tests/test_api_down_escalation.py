import unittest
from unittest.mock import patch, AsyncMock

from langchain_core.messages import HumanMessage
from core.graph.nodes.auth import auth_node


def _state(**kw):
    base = {"call_sid": "CA_APIDOWN", "messages": [HumanMessage(content="I want my policy status")]}
    base.update(kw)
    return base


class ApiDownEscalationTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_down_transfers_before_asking_anything(self):
        with patch("core.graph.nodes.auth.api_reachable", new=AsyncMock(return_value=False)):
            result = await auth_node(_state())
        self.assertEqual(result["current_node"], "escalation")
        self.assertEqual(result["auth_step"], "failed")
        self.assertIn("technical issue", result["tts_text"])
        self.assertNotIn("policy number", result["tts_text"].lower())

    async def test_api_up_continues_auth(self):
        with patch("core.graph.nodes.auth.api_reachable", new=AsyncMock(return_value=True)):
            result = await auth_node(_state())
        self.assertNotEqual(result.get("current_node"), "escalation")

    async def test_no_probe_once_auth_is_underway(self):
        probe = AsyncMock(return_value=False)
        with patch("core.graph.nodes.auth.api_reachable", new=probe):
            await auth_node(_state(auth_step="collecting_dob",
                                   pii_collected={"phoneNumber": "5551234567"},
                                   candidate_party={"DOB": "1965-07-15"}))
        probe.assert_not_called()


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_host_is_false(self):
        from core.tools.http import probe
        self.assertFalse(await probe("http://127.0.0.1:1", timeout=1))
