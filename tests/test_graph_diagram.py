"""#115: the Mermaid diagram comes from the real compiled graph and the docs copies stay in sync."""
import asyncio
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import graph_diagram
from core.graph.graph import build_graph

SERVICE_NODES = {
    "policy", "payment", "otp", "loan", "beneficiary", "contact",
    "document", "privacy", "faq", "escalation", "goodbye",
}


class GraphDiagramTests(unittest.TestCase):
    def test_diagram_has_every_node_and_the_router_fan_out(self):
        text = graph_diagram.graph_mermaid()
        g = build_graph().compile().get_graph()
        self.assertEqual(set(g.nodes) - {"__start__", "__end__"}, SERVICE_NODES | {"router"})
        self.assertIn("__start__ --> router", text)
        for node in SERVICE_NODES:
            self.assertIn(f"router -.-> {node}", text)     # conditional edge
            self.assertIn(f"{node} --> __end__", text)      # every service node ends the turn
        self.assertIn("router -.-> __end__", text)          # empty-intent path

    def test_committed_docs_match_the_compiled_graph(self):
        self.assertEqual(graph_diagram.MMD_PATH.read_text(encoding="utf-8"),
                         graph_diagram.graph_mermaid().rstrip() + "\n",
                         "docs/langgraph_flow.mmd is stale: run `python -m utils.graph_diagram`")
        self.assertEqual(graph_diagram.MD_PATH.read_text(encoding="utf-8"),
                         graph_diagram.graph_markdown(),
                         "docs/langgraph_flow.md is stale: run `python -m utils.graph_diagram`")

    def test_markdown_is_a_fenced_mermaid_block(self):
        md = graph_diagram.graph_markdown()
        self.assertIn("```mermaid\n", md)
        self.assertEqual(md.count("```"), 2)


class DashboardGraphTabTests(unittest.TestCase):
    def test_endpoint_serves_the_live_diagram(self):
        import webhooks.dashboard as dash
        resp = asyncio.run(dash.graph_source())
        self.assertEqual(resp.body.decode(), graph_diagram.graph_mermaid())
        self.assertTrue(resp.media_type.startswith("text/plain"))

    def test_graph_tab_loads_the_endpoint_and_keeps_a_fallback(self):
        import webhooks.dashboard as dash
        html = dash._HTML
        self.assertIn('id="lg-diagram"', html)
        self.assertIn("/dashboard/graph.mmd", html)
        self.assertIn('id="lg-source"', html)             # source shown if the renderer cannot load
        self.assertIn("mermaid.live", html)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class DashboardScriptSyntaxTests(unittest.TestCase):
    def test_inline_scripts_parse(self):
        """A bad escape in the Python string that holds the page once broke every dashboard tab."""
        import webhooks.dashboard as dash
        scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", dash._HTML, flags=re.S)
        self.assertTrue(scripts, "no inline <script> found")
        for i, code in enumerate(scripts):
            with tempfile.TemporaryDirectory() as tmp:
                f = pathlib.Path(tmp) / f"dash_{i}.js"
                f.write_text(code, encoding="utf-8")
                r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"script #{i} has a syntax error: {r.stderr[:600]}")


if __name__ == "__main__":
    unittest.main()
