from typing import Literal
from .graph_state import AgentState
from .nodes import llm_analysis_node, gather_vmcore_detail_node, crash_tool_node
from langchain_core.messages import AIMessage
from src.utils.logging import logger


def should_continue(state: AgentState) -> str:
    """
    根据当前 AgentState 决定是继续 LLM 分析还是结束流程。
    """
    last_message = state.messages[-1] if state.messages else None
    error_state = state.error

    # 1. 任意已标记的错误：直接结束
    if error_state and error_state.get("is_error"):
        node = error_state.get("node", "<unknown>")
        msg = error_state.get("message", "")
        logger.error(f"jump from node:{node} to node:__end__ due to error:{msg}")
        return "__end__"

    # 2. 达到最大分析步数：结束
    if state.analysis_steps >= state.max_analysis_steps:
        logger.error(
            f"Reached max analysis steps ({state.analysis_steps}). Ending analysis."
        )
        return "__end__"

    # 3. 输出消息类型不对：记录错误并结束
    if last_message is not None and not isinstance(last_message, AIMessage):
        state.error = {
            "message": (
                f"Expected AIMessage in output edges, "
                f"but got {type(last_message).__name__}"
            ),
            "node": llm_analysis_node,
            "is_error": True,
        }
        logger.error(
            f"jump from node:{llm_analysis_node} to node:__end__ "
            f"due to error:{state.error['message']}"
        )
        return "__end__"

    # 4. 正常情况：进入 llm_analysis_node
    return llm_analysis_node
