"""
VMCore 分析 Agent 边（路由）逻辑

定义节点之间的转换条件和路由规则。
"""

from typing import Literal
from .graph_state import AgentState
from .nodes import llm_analysis_node, collect_crash_init_data_node, crash_tool_node
from langchain_core.messages import AIMessage
from src.utils.logging import logger


def should_continue(state: AgentState) -> str:
    """
    根据当前 AgentState 决定下一步执行的节点。

    路由逻辑：
    1. 如果有错误 -> __end__
    2. 如果最后一条消息不是 AIMessage -> __end__
    3. 如果 AIMessage 没有 tool_calls -> __end__
    4. 如果有 tool_calls -> crash_tool_node
    5. 其他情况 -> llm_analysis_node

    Args:
        state: AgentState 字典

    Returns:
        str: 下一个节点名称或 "__end__"
    """
    # ✅ 修复：使用字典访问方式而不是属性访问
    messages = state.get("messages", [])
    error_state = state.get("error")

    last_message = messages[-1] if messages else None

    # 1. 检查错误状态
    if error_state and error_state.get("is_error"):
        node = error_state.get("node", "<unknown>")
        msg = error_state.get("message", "")
        logger.error(f"Routing to __end__ from node '{node}' due to error: {msg}")
        return "__end__"

    # 2. 如果没有消息，继续 LLM 分析
    if last_message is None:
        logger.info("No messages yet, routing to llm_analysis_node")
        return llm_analysis_node

    # 3. 检查消息类型
    if not isinstance(last_message, AIMessage):
        logger.warning(
            f"Last message is {type(last_message).__name__}, not AIMessage. "
            f"Routing to llm_analysis_node"
        )
        return llm_analysis_node

    # 4. 检查是否有工具调用
    tool_calls = getattr(last_message, "tool_calls", None) or []

    if tool_calls:
        logger.info(f"Found {len(tool_calls)} tool calls, routing to crash_tool_node")
        return crash_tool_node
    else:
        # 没有工具调用，说明 LLM 给出了最终答案
        logger.info("No tool calls in AIMessage, analysis complete. Routing to __end__")
        return "__end__"
