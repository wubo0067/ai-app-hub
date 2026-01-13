from src.llm.model import llm
from src.utils.logging import logger
from .graph_state import AgentState
from .nodes import (
    gather_vmcore_detail,
    call_llm_analysis,
    call_crash_tool,
    llm_analysis_node,
    gather_vmcore_detail_node,
    crash_tool_node,
)
from .edges import should_continue
from langgraph.graph import START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from functools import partial


def create_agent_graph(llm, tools_list):
    """
    Build and compile the VMCore analysis agent graph.
    """
    if not tools_list:
        logger.warning("No tools provided; LLM will run without bound tools.")
    llm_with_tools = llm.bind_tools(tools_list or [])

    checkpointer = InMemorySaver()

    logger.info("Compiling agent graph...")
    builder = StateGraph(AgentState)

    builder.add_node(
        llm_analysis_node, partial(call_llm_analysis, llm_with_tools=llm_with_tools)
    )
    builder.add_node(crash_tool_node, call_crash_tool)
    builder.add_node(gather_vmcore_detail_node, gather_vmcore_detail)

    builder.add_edge(START, gather_vmcore_detail_node)
    builder.add_conditional_edges(gather_vmcore_detail_node, should_continue)
    builder.add_conditional_edges(llm_analysis_node, should_continue)

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("Agent graph compiled successfully.")
    return graph
