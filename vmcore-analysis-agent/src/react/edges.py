"""
VMCore 分析 Agent 边（路由）逻辑

定义节点之间的转换条件和路由规则。
"""

from typing import Literal
from langchain_core.messages import AIMessage, HumanMessage
from .graph_state import AgentState
from src.utils.logging import logger
from .nodes import crash_tool_node, llm_analysis_node


def should_continue(state: AgentState) -> str:
    """
    根据当前 AgentState 决定下一步执行的节点。

    Args:
        state: AgentState 字典

    Returns:
        str: 下一个节点名称或 "__end__"
    """
    # ✅ 修复：使用字典访问方式而不是属性访问
    messages = state.get("messages", [])
    error_state = state.get("error")
    is_last_step = state.get("is_last_step", False)

    last_message = messages[-1] if messages else None

    # 1. 检查错误状态
    if error_state and error_state.get("is_error"):
        node = error_state.get("node", "<unknown>")
        msg = error_state.get("message", "")
        logger.error(f"Routing to __end__ from node '{node}' due to error: {msg}")
        return "__end__"

    # 2. 根据消息类型判断路由
    if isinstance(last_message, AIMessage):
        tool_calls = getattr(last_message, "tool_calls", None) or []
        if tool_calls:
            if is_last_step:
                logger.warning(
                    "LLM requested tool calls on the last step. Forcing completion to avoid recursion limit error."
                )
                return "__end__"
            logger.info(
                f"Found {len(tool_calls)} tool calls, routing to {crash_tool_node}"
            )
            return crash_tool_node
        else:
            logger.info(
                "No tool calls in AIMessage, analysis complete. Routing to __end__"
            )
            return "__end__"

    # 3. 如果是 HumanMessage (初始收集完成)，路由到分析节点
    if isinstance(last_message, HumanMessage):
        logger.info(f"Initial data collected, routing to {llm_analysis_node}")
        return llm_analysis_node

    # 4. 默认安全回退
    logger.warning(
        f"Unexpected message type/state: {type(last_message)}, routing to __end__"
    )
    return "__end__"
