from src.llm.model import llm
from src.mcp_tools.crash.client import crash_tools
from src.utils.logging import logger
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from functools import partial

from .graph_state import AgentState


def create_agent_graph(llm, tools_list):
    """"""
    llm_with_tools = llm.bind_tools(crash_tools)
    tool_node = ToolNode(crash_tools)

    logger.info("Compiling agent graph...")
    agent_builder = StateGraph(AgentState)
