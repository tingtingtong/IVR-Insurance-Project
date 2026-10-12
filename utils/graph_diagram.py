"""Mermaid diagram of the LangGraph IVR flow, generated from the real compiled graph (#115).

    python -m utils.graph_diagram      # rewrites docs/langgraph_flow.mmd and docs/langgraph_flow.md

The dashboard Graph tab serves graph_mermaid() live, and a test fails if the committed
docs files differ from it, so the diagram cannot drift from core/graph/graph.py.
"""
import pathlib

_DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
MMD_PATH = _DOCS / "langgraph_flow.mmd"
MD_PATH = _DOCS / "langgraph_flow.md"

_NOTE = (
    "Dotted edges are the conditional routing in `_route_after_router`. Authentication is not a "
    "graph node: each service node calls `ensure_authenticated` (`core/graph/auth_guard.py`) "
    "and auth runs inside that turn."
)


def graph_mermaid() -> str:
    from core.graph.graph import build_graph
    return build_graph().compile().get_graph().draw_mermaid()


def graph_markdown() -> str:
    return (
        "# LangGraph IVR flow\n\n"
        "Generated from `build_graph()` by `python -m utils.graph_diagram`. Do not edit by hand.\n\n"
        f"```mermaid\n{graph_mermaid().rstrip()}\n```\n\n"
        f"{_NOTE}\n"
    )


def write_docs() -> None:
    MMD_PATH.write_text(graph_mermaid().rstrip() + "\n", encoding="utf-8", newline="\n")
    MD_PATH.write_text(graph_markdown(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_docs()
    print(f"wrote {MMD_PATH} and {MD_PATH}")
