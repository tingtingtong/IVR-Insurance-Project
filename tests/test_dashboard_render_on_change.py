"""#77: dashboard polling must only touch the DOM when the data changed."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (ROOT / "webhooks" / "dashboard.py").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = SRC.index(f"function {name}(")
    nxt = re.search(r"\n(async )?function \w+\(", SRC[start + 10:])
    return SRC[start:start + 10 + nxt.start()] if nxt else SRC[start:]


class RenderOnChangeTests(unittest.TestCase):
    def test_polled_sections_go_through_set_html(self):
        for section in ("call-list", "call-hdr", "call-state-panel", "call-turns", "ev-rows"):
            self.assertIn(f"setHtml('{section}'", SRC, section)

    def test_no_direct_innerhtml_in_polled_renderers(self):
        for name in ("loadCalls", "refreshCallDetail", "renderEventTimeline", "renderStatePanel"):
            self.assertNotIn("innerHTML", _fn(name), f"{name} writes innerHTML directly")

    def test_redundant_detail_poll_removed(self):
        self.assertNotIn("setInterval(() => { if (selCall) selCallFn(selCall); }, 5000)", SRC)

    def test_polling_pauses_when_tab_hidden(self):
        self.assertIn("if (document.hidden) return;", _fn("loadCalls"))
        self.assertIn("visibilitychange", SRC)

    def test_loading_placeholder_only_when_switching_calls(self):
        timeline = _fn("renderEventTimeline")
        self.assertIn("if (switched) setHtml('ev-rows'", timeline)

    def test_scroll_follows_only_when_already_at_bottom(self):
        detail = _fn("refreshCallDetail")
        self.assertIn("closest('.call-detail-scroll')", detail)
        self.assertIn("!switched && nearBottom(scroller)", detail)


if __name__ == "__main__":
    unittest.main()
