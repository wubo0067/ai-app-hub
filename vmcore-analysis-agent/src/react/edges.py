#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# edges.py - VMCore 分析 Agent 边（路由）逻辑模块
# Author: CalmWU
# Created: 2026-01-13

"""
VMCore 分析 Agent 边（路由）逻辑

定义节点之间的转换条件和路由规则。
"""

import json
from typing import Literal
from langchain_core.messages import AIMessage, HumanMessage
from .graph_state import AgentState
from .schema import VMCoreAnalysisStep
from src.utils.logging import logger
from .nodes import (
    crash_tool_node,
    llm_analysis_node,
    structure_reasoning_node,
)


def _parse_analysis_step(message: AIMessage) -> VMCoreAnalysisStep | None:
    try:
        raw = (
            message.content
            if isinstance(message.content, str)
            else json.dumps(message.content)
        )
        return VMCoreAnalysisStep.model_validate_json(raw)
    except Exception:
        return None


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

    # 1.5 检查是否需要结构化 reasoning_content
    if state.get("reasoning_to_structure"):
        logger.info(
            f"reasoning_to_structure is set, routing to {structure_reasoning_node}"
        )
        return structure_reasoning_node

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
            # If the LLM returned no tool calls but also is_conclusive=False,
            # allow at most one retry. A repeated no-tool/non-conclusive AIMessage means
            # the retry prompt did not unblock progress, so keep the bounded non-conclusive
            # result instead of looping until recursion_limit.
            if not is_last_step:
                step_obj = _parse_analysis_step(last_message)
                if step_obj is not None and not step_obj.is_conclusive:
                    # Sometimes json_repair fixes a truncated model output and sets is_conclusive=False
                    # by default, even though final_diagnosis is populated. Check if we actually have
                    # a final_diagnosis, and if so, consider it conclusive to prevent an infinite loop.
                    if step_obj.final_diagnosis is not None:
                        logger.info(
                            "LLM returned is_conclusive=False but final_diagnosis is populated. "
                            "Treating as conclusive to prevent infinite loop. Routing to __end__."
                        )
                        return "__end__"

                    previous_message = messages[-2] if len(messages) >= 2 else None
                    previous_step = (
                        _parse_analysis_step(previous_message)
                        if isinstance(previous_message, AIMessage)
                        and not (getattr(previous_message, "tool_calls", None) or [])
                        else None
                    )
                    if previous_step is not None and not previous_step.is_conclusive:
                        logger.warning(
                            "LLM remained non-conclusive without tool calls after one retry "
                            "(previous step %s, current step %s). Treating the latest response as a bounded "
                            "non-conclusive terminal state to avoid an infinite loop.",
                            previous_step.step_id,
                            step_obj.step_id,
                        )
                        return "__end__"

                    logger.warning(
                        "LLM returned no tool calls but is_conclusive=False "
                        "(step %s). Routing back to %s for one retry.",
                        step_obj.step_id,
                        llm_analysis_node,
                    )
                    return llm_analysis_node
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


def after_crash_tool(state: AgentState) -> str:
    """
    crash_tool_node 执行完毕后的路由判断。

    当 crash_tool_node 处于最后一步时，直接结束而非返回 llm_analysis_node，
    避免超出 recursion_limit。
    """
    is_last_step = state.get("is_last_step", False)
    if is_last_step:
        logger.warning(
            "crash_tool_node is on the last step. "
            "Routing to __end__ to avoid exceeding recursion_limit."
        )
        return "__end__"
    return llm_analysis_node
