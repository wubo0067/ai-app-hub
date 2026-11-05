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


def create_agent_graph(llm, tools_list: List):
    """创建并编译 VMCore 分析 Agent 的 LangGraph 状态图。

    该函数负责完成以下工作：
    1. 使用原始推理 LLM 构造主分析节点；结构化 JSON 的 action 由 build_tool_calls 转换。
    2. 初始化图状态检查点，用于在多轮推理过程中保存状态。
    3. 按照既定流程注册各个节点及其转换边。
    4. 结构化推理兜底节点与主分析节点共用同一个 LLM 实例。
    5. 编译并返回可执行的 LangGraph 对象。

    整体执行流程如下：
    START -> collect_crash_init_data_node
          -> llm_analysis_node
          -> crash_tool_node
          -> llm_analysis_node
          -> ...

    当满足结束条件时，图会跳转到 __end__ 并结束执行。

    Args:
        llm: 推理模型实例；主分析节点与结构化兜底节点共用该实例。
        tools_list: 当前可用的 MCP 工具列表；仅用于记录可用性，工具调用由
            build_tool_calls 从结构化 action 字段合成。

    Returns:
        编译完成的 LangGraph 可执行图对象。
    """

    # 结构化 JSON 的 action 由 build_tool_calls 转成 LangChain tool_calls；
    # 这里不再 bind_tools，避免 RunnableBinding 代理导致 max_tokens 等绑定丢失。
    if not tools_list:
        logger.warning(
            "No tools provided to the agent. LLM will run without tool-calling capability."
        )
    else:
        logger.info(
            f"{len(tools_list)} tools available; tool calls are synthesized from "
            "structured action fields."
        )

    # 初始化内存检查点，用于保存图执行过程中的状态。
    checkpointer = InMemorySaver()
    logger.debug("Initialized InMemorySaver for graph checkpointing.")

    # 创建基于 AgentState 的状态图构建器。
    logger.info("Building agent graph structure...")
    builder = StateGraph(AgentState)

    # 注册 LLM 分析节点：负责读取当前状态并决定下一步动作。
    builder.add_node(
        llm_analysis_node,
        partial(call_llm_analysis, llm=llm),
    )
    logger.debug(f"Added node: {llm_analysis_node}")

    # 注册 crash 工具节点：负责执行被 LLM 选中的 crash 命令或工具。
    builder.add_node(crash_tool_node, call_crash_tool)
    logger.debug(f"Added node: {crash_tool_node}")

    # 注册结构化推理兜底节点：与主分析节点共用同一个 LLM 实例。
    builder.add_node(
        structure_reasoning_node,
        partial(structure_reasoning_content, llm=llm),
    )
    logger.debug(f"Added node: {structure_reasoning_node}")

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
    llm_analysis_targets = [
        crash_tool_node,
        "__end__",
        llm_analysis_node,
        structure_reasoning_node,
    ]

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
