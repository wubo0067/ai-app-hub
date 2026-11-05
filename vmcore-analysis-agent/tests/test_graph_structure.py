import sys
import types
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.graph import create_agent_graph


class _FakeLLM:
    """Minimal LLM double: graph construction only needs bind_tools."""

    def bind_tools(self, tools):
        return self


class GraphStructureTests(unittest.TestCase):
    def test_structure_node_registered_without_tools(self) -> None:
        graph = create_agent_graph(_FakeLLM(), [])

        self.assertIn("structure_reasoning_node", set(graph.get_graph().nodes))

    def test_structure_node_registered_with_tools(self) -> None:
        graph = create_agent_graph(_FakeLLM(), [object()])

        self.assertIn("structure_reasoning_node", set(graph.get_graph().nodes))


if __name__ == "__main__":
    unittest.main()