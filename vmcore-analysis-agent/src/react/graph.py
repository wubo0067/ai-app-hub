#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# graph.py - VMCore 分析 Agent 图构建模块
# Author: CalmWU
# Created: 2026-01-09

"""
VMCore 分析 Agent 图构建模块

此模块负责创建和编译 LangGraph 状态图，定义节点之间的转换关系和执行流程。
整体流程：
1. 收集 vmcore 基础信息 (collect_crash_init_data_node)
2. LLM 分析并决策下一步行动 (llm_analysis_node)
3. 执行 crash 工具调用 (crash_tool_node)
4. 循环执行 2-3 直到得出最终结论或达到递归限制
"""

from functools import partial
from typing import List

from langgraph.graph import START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

from src.utils.config import config_manager
from src.utils.logging import logger
from .graph_state import AgentState
from .nodes import (
    collect_crash_init_data,
    collect_crash_init_data_node,
    call_crash_tool,
    crash_tool_node,
    llm_analysis_node,
    structure_reasoning_node,
)
from .llm_node import call_llm_analysis, structure_reasoning_content
from .edges import should_continue, after_crash_tool


def _debug_log_bound_tools_payload(bound_llm) -> None:
    """Log bound tool schema payload when debug switch is enabled.

    This helper intentionally uses best-effort introspection because different
    LangChain model wrappers expose bound tool payloads via different fields.
    """

    if not config_manager.get("debug_tools_schema", False):
        return

    payload = None

    # Common path for RunnableBinding-like objects created by bind_tools.
    kwargs = getattr(bound_llm, "kwargs", None)
    if isinstance(kwargs, dict):
        payload = kwargs.get("tools") or kwargs.get("functions")

    # Fallback for wrappers that store kwargs on an internal bound object.
    if payload is None:
        bound = getattr(bound_llm, "bound", None)
        bound_kwargs = getattr(bound, "kwargs", None)
        if isinstance(bound_kwargs, dict):
            payload = bound_kwargs.get("tools") or bound_kwargs.get("functions")

    if payload is None:
        logger.warning(
            "debug_tools_schema is enabled, but no bound tools/functions payload was found on llm_with_tools."
        )
        return

    logger.info("[DEBUG] Bound tool schema payload (tools/functions): %s", payload)


def create_agent_graph(llm, tools_list: List, structured_llm=None):
    """创建并编译 VMCore 分析 Agent 的 LangGraph 状态图。

    该函数负责完成以下工作：
    1. 将可用工具绑定到主 LLM，使其具备工具调用能力。
    2. 初始化图状态检查点，用于在多轮推理过程中保存状态。
    3. 按照既定流程注册各个节点及其转换边。
    4. 在存在 structured_llm 时，启用结构化推理兜底节点。
    5. 编译并返回可执行的 LangGraph 对象。

    整体执行流程如下：
    START -> collect_crash_init_data_node
          -> llm_analysis_node
          -> crash_tool_node
          -> llm_analysis_node
          -> ...

    当满足结束条件时，图会跳转到 __end__ 并结束执行。

    Args:
        llm: 主推理模型实例，用于执行常规分析与决策。
        tools_list: 提供给 LLM 使用的工具列表；为空时仅执行纯 LLM 推理。
        structured_llm: 可选的结构化输出模型实例，用于在特定场景下生成结构化推理结果。

    Returns:
        编译完成的 LangGraph 可执行图对象。
    """

    # 当未提供工具时，直接使用原始 LLM 执行分析。
    if not tools_list:
        logger.warning(
            "No tools provided to the agent. LLM will run without tool-calling capability."
        )
        llm_with_tools = llm
    else:
        # 将工具绑定到 LLM，使其能够在分析过程中主动发起工具调用。
        llm_with_tools = llm.bind_tools(tools_list)
        logger.info(f"Bound {len(tools_list)} tools to LLM for agent execution.")
        _debug_log_bound_tools_payload(llm_with_tools)

    # 初始化内存检查点，用于保存图执行过程中的状态。
    checkpointer = InMemorySaver()
    logger.debug("Initialized InMemorySaver for graph checkpointing.")

    # 创建基于 AgentState 的状态图构建器。
    logger.info("Building agent graph structure...")
    builder = StateGraph(AgentState)

    # 注册 LLM 分析节点：负责读取当前状态并决定下一步动作。
    builder.add_node(
        llm_analysis_node,
        partial(call_llm_analysis, llm_with_tools=llm_with_tools),
    )
    logger.debug(f"Added node: {llm_analysis_node}")

    # 注册 crash 工具节点：负责执行被 LLM 选中的 crash 命令或工具。
    builder.add_node(crash_tool_node, call_crash_tool)
    logger.debug(f"Added node: {crash_tool_node}")

    # 如果提供了结构化模型，则启用结构化推理节点作为补充或兜底路径。
    if structured_llm:
        builder.add_node(
            structure_reasoning_node,
            partial(structure_reasoning_content, structured_llm=structured_llm),
        )
        logger.debug(f"Added node: {structure_reasoning_node}")
    else:
        # 未提供结构化模型时，仅记录告警，不注册该节点。
        logger.warning(
            "No structured_llm provided. structure_reasoning_node will not be available. "
            "DeepSeek-Reasoner empty content fallback will be disabled."
        )

    # 注册初始化节点：用于收集 vmcore 分析所需的基础上下文数据。
    builder.add_node(collect_crash_init_data_node, collect_crash_init_data)
    logger.debug(f"Added node: {collect_crash_init_data_node}")

    # 设置图的入口，从初始化数据收集节点开始执行。
    builder.add_edge(START, collect_crash_init_data_node)
    logger.debug(f"Added edge: START -> {collect_crash_init_data_node}")

    # 初始化节点完成后，根据状态决定进入 LLM 分析或直接结束。
    builder.add_conditional_edges(
        collect_crash_init_data_node,
        should_continue,
        [llm_analysis_node, "__end__"],
    )
    logger.debug(
        f"Added conditional edge: {collect_crash_init_data_node} -> [llm_analysis_node, __end__]"
    )

    # 定义 LLM 分析节点的所有可能后继节点。
    llm_analysis_targets = [crash_tool_node, "__end__", llm_analysis_node]

    # 仅在结构化推理节点可用时，将其加入 LLM 分析的跳转目标中。
    if structured_llm:
        llm_analysis_targets.append(structure_reasoning_node)

    # 根据 LLM 当前输出结果，决定继续工具调用、重复分析、结构化推理或结束。
    builder.add_conditional_edges(
        llm_analysis_node,
        should_continue,
        llm_analysis_targets,
    )
    logger.debug(
        f"Added conditional edge: {llm_analysis_node} -> {llm_analysis_targets}"
    )

    # 结构化推理节点完成后，可继续调用工具、返回分析节点，或直接结束。
    if structured_llm:
        builder.add_conditional_edges(
            structure_reasoning_node,
            should_continue,
            [crash_tool_node, llm_analysis_node, "__end__"],
        )
        logger.debug(
            f"Added conditional edge: {structure_reasoning_node} -> [crash_tool_node, llm_analysis_node, __end__]"
        )

    # 工具执行完成后，根据执行结果决定回到 LLM 继续推理，或结束流程。
    builder.add_conditional_edges(
        crash_tool_node,
        after_crash_tool,
        [llm_analysis_node, "__end__"],
    )
    logger.debug(
        f"Added conditional edge: {crash_tool_node} -> [{llm_analysis_node}, __end__]"
    )

    # 编译状态图，生成最终可执行的 Agent Graph。
    logger.info("Compiling agent graph...")
    graph = builder.compile(
        checkpointer=checkpointer,  # 启用状态检查点
        name="vmcore_analysis_agent",  # 图的唯一标识名称
    )
    logger.info("✅ Agent graph compiled successfully.")

    # 可选：在调试模式下保存图结构到文件（需要 graphviz 支持）。
    # try:
    #     graph_png = graph.get_graph().draw_mermaid_png()
    #     output_path = os.path.join(
    #         os.path.dirname(__file__), "../../graph_visualization.png"
    #     )
    #     with open(output_path, "wb") as f:
    #         f.write(graph_png)
    #     logger.info(f"Graph visualization saved to: {output_path}")
    # except Exception as e:
    #     logger.error(f"Could not display graph visualization: {e}")

    # 返回编译后的图对象，供上层流程直接调用。
    return graph
