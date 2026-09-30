"""#56: caller PII must be redacted before it is sent to the LLM or embedding API."""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage

from utils.pii_redactor import redact, redact_for_llm, redact_messages

CARD = "4111111111111111"
NODES = ROOT / "core" / "graph" / "nodes"


class RedactForLlmTests(unittest.TestCase):
    def test_pii_is_removed(self):
        out = redact_for_llm(f"card {CARD}, phone 317-555-1234, born January 15 1965, policy P300123456")
        for secret in (CARD, "555-1234", "1965", "P300123456"):
            self.assertNotIn(secret, out)

    def test_dollar_amounts_are_kept_for_llm(self):
        self.assertEqual(redact_for_llm("can I borrow $5,000"), "can I borrow $5,000")
        self.assertIn("[AMOUNT REDACTED]", redact("can I borrow $5,000"))

    def test_intent_words_survive(self):
        self.assertEqual(redact_for_llm("I want to check my policy status"),
                         "I want to check my policy status")

    def test_redact_messages_copies_and_keeps_types(self):
        msgs = [HumanMessage(content=CARD), AIMessage(content="The security code I heard is 5, 4, 3.")]
        out = redact_messages(msgs)
        self.assertIsInstance(out[0], HumanMessage)
        self.assertIsInstance(out[1], AIMessage)
        self.assertEqual(out[0].content, "[CARD REDACTED]")
        self.assertIn("[CVV REDACTED]", out[1].content)
        self.assertEqual(msgs[0].content, CARD)  # original state untouched


class CallSiteTests(unittest.TestCase):
    def test_every_llm_history_slice_is_redacted(self):
        for path in NODES.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            raw = re.findall(r"^\s*\*messages\[-\d+:\]", src, re.M)
            self.assertEqual(raw, [], f"{path.name} sends raw message history to the LLM")

    def test_router_prompt_is_redacted(self):
        src = (NODES / "router.py").read_text(encoding="utf-8")
        self.assertNotIn("ROUTER_PROMPT.format(utterance=last_human)", src)
        self.assertEqual(src.count("ROUTER_PROMPT.format(utterance=redact_for_llm(last_human))"), 2)

    def test_faq_query_and_prompt_are_redacted(self):
        src = (NODES / "faq.py").read_text(encoding="utf-8")
        self.assertIn("search_knowledge(redact_for_llm(last_human)", src)
        self.assertIn("for voice: {redact_for_llm(last_human)}", src)


if __name__ == "__main__":
    unittest.main()
