import json
import sys
import types
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

langgraph_module = types.ModuleType("langgraph")
graph_module = types.ModuleType("langgraph.graph")
graph_module.MessagesState = dict
managed_module = types.ModuleType("langgraph.managed")
managed_module.IsLastStep = bool
langgraph_module.graph = graph_module
langgraph_module.managed = managed_module
sys.modules.setdefault("langgraph", langgraph_module)
sys.modules.setdefault("langgraph.graph", graph_module)
sys.modules.setdefault("langgraph.managed", managed_module)

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)
nodes_module = types.ModuleType("src.react.nodes")
nodes_module.crash_tool_node = "crash_tool_node"
nodes_module.llm_analysis_node = "llm_analysis_node"
nodes_module.structure_reasoning_node = "structure_reasoning_node"
sys.modules.setdefault("src.react.nodes", nodes_module)

from src.react.edges import should_continue


class EdgeRoutingTests(unittest.TestCase):
    def test_retries_once_for_non_conclusive_ai_message_without_tool_calls(
        self,
    ) -> None:
        step_json = json.dumps(
            {
                "step_id": 12,
                "reasoning": "verification gaps remain",
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "unknown",
                "partial_dump": "partial",
                "additional_notes": "bounded non-conclusive summary",
            }
        )
        state = {
            "messages": [
                HumanMessage(content="ctx"),
                AIMessage(content=step_json, tool_calls=[]),
            ],
            "error": None,
            "is_last_step": False,
        }

        self.assertEqual(should_continue(state), "llm_analysis_node")

    def test_ends_after_second_non_conclusive_ai_message_without_tool_calls(
        self,
    ) -> None:
        step_json = json.dumps(
            {
                "step_id": 13,
                "reasoning": "verification gaps remain",
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "unknown",
                "partial_dump": "partial",
                "additional_notes": "bounded non-conclusive summary",
            }
        )
        state = {
            "messages": [
                HumanMessage(content="ctx"),
                AIMessage(content=step_json, tool_calls=[]),
                AIMessage(content=step_json, tool_calls=[]),
            ],
            "error": None,
            "is_last_step": False,
        }

        self.assertEqual(should_continue(state), "__end__")


if __name__ == "__main__":
    unittest.main()
