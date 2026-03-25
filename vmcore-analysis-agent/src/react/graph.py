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

import os
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


def create_agent_graph(llm, tools_list: List, structured_llm=None):
    """
    构建并编译 VMCore 分析 Agent 的状态图。

    Args:
        llm: 语言模型实例（通常是 ChatOpenAI 或类似的模型）
        tools_list: 可用的工具列表（MCP crash 工具）
        structured_llm: 可选的 deepseek-chat 模型实例，用于结构化 Reasoner 的纯文本推理内容

    Returns:
        CompiledGraph: 编译后的 LangGraph 图实例，可执行 invoke/astream 等方法

    架构说明：
        - 使用 StateGraph 管理 AgentState 状态流转
        - 使用 InMemorySaver 作为检查点存储器，支持状态回溯和恢复
        - 通过条件边（conditional_edges）实现智能路由决策

    节点说明：
        - collect_crash_init_data_node: 初始节点，收集 vmcore 基础诊断信息
        - llm_analysis_node: LLM 分析节点，根据当前信息生成下一步计划
        - crash_tool_node: 工具执行节点，调用 crash 命令获取详细信息

    边说明：
        - START -> collect_crash_init_data_node: 固定起点
        - collect_crash_init_data_node -> should_continue: 根据收集结果决定下一步
        - llm_analysis_node -> should_continue: 根据 LLM 决策决定继续或结束
        - crash_tool_node -> llm_analysis_node: 工具执行后返回 LLM 分析
    """
    # =========================================================================
    # 1. 验证并绑定工具到 LLM
    # =========================================================================
    if not tools_list:
        logger.warning(
            "No tools provided to the agent. LLM will run without tool-calling capability."
        )
        llm_with_tools = llm
    else:
        # bind_tools 会将工具列表绑定到 LLM，使其能够在生成响应时调用工具
        llm_with_tools = llm.bind_tools(tools_list)
        logger.info(f"Bound {len(tools_list)} tools to LLM for agent execution.")

    # =========================================================================
    # 2. 初始化检查点存储器
    # =========================================================================
    # InMemorySaver 用于在内存中保存图执行过程中的状态快照
    # 优点：支持状态回溯、中断恢复、调试追踪
    # 生产环境可替换为 PostgresSaver 或 RedisSaver 实现持久化
    checkpointer = InMemorySaver()
    logger.debug("Initialized InMemorySaver for graph checkpointing.")

    # =========================================================================
    # 3. 构建状态图
    # =========================================================================
    logger.info("Building agent graph structure...")
    builder = StateGraph(AgentState)

    # 添加节点 1：LLM 分析节点
    # 使用 partial 预填充 llm_with_tools 参数，使节点函数签名符合 LangGraph 要求
    builder.add_node(
        llm_analysis_node,
        partial(call_llm_analysis, llm_with_tools=llm_with_tools),
    )
    logger.debug(f"Added node: {llm_analysis_node}")

    # 添加节点 2：Crash 工具调用节点
    # 执行 LLM 决策后的具体 crash 命令，获取诊断数据
    builder.add_node(crash_tool_node, call_crash_tool)
    logger.debug(f"Added node: {crash_tool_node}")

    # 添加节点 3：推理内容结构化节点
    # 当 DeepSeek-Reasoner 返回空 content 但有纯文本 reasoning_content 时，
    # 使用 deepseek-chat 将推理内容结构化为 VMCoreAnalysisStep
    if structured_llm:
        builder.add_node(
            structure_reasoning_node,
            partial(structure_reasoning_content, structured_llm=structured_llm),
        )
        logger.debug(f"Added node: {structure_reasoning_node}")
    else:
        logger.warning(
            "No structured_llm provided. structure_reasoning_node will not be available. "
            "DeepSeek-Reasoner empty content fallback will be disabled."
        )

    # 添加节点 3：收集 vmcore 详细信息节点
    # 初始节点，执行默认的 crash 命令集合收集基础信息
    builder.add_node(collect_crash_init_data_node, collect_crash_init_data)
    logger.debug(f"Added node: {collect_crash_init_data_node}")

    # =========================================================================
    # 4. 定义节点之间的边（执行流程）
    # =========================================================================
    # 固定边：图的入口点，从 START 直接进入信息收集节点
    builder.add_edge(START, collect_crash_init_data_node)
    logger.debug(f"Added edge: START -> {collect_crash_init_data_node}")

    # 条件边 1：信息收集完成后根据状态决定下一步
    # should_continue 函数会检查：
    #   - 是否发生错误 -> END
    #   - 是否已有最终答案 -> END
    #   - 否则 -> llm_analysis_node（继续分析）
    builder.add_conditional_edges(
        collect_crash_init_data_node,
        should_continue,
        [llm_analysis_node, "__end__"],
    )
    logger.debug(
        f"Added conditional edge: {collect_crash_init_data_node} -> [llm_analysis_node, __end__]"
    )

    # 条件边 2：LLM 分析后根据决策结果路由
    # LLM 可能返回：
    #   - 工具调用请求 -> crash_tool_node
    #   - 需要结构化 reasoning_content -> structure_reasoning_node
    #   - 最终答案 -> END
    #   - 如果 is_conclusive=False 且没有 tool_calls -> llm_analysis_node
    #   - 错误状态 -> END
    llm_analysis_targets = [crash_tool_node, "__end__", llm_analysis_node]
    if structured_llm:
        llm_analysis_targets.append(structure_reasoning_node)
    builder.add_conditional_edges(
        llm_analysis_node,
        should_continue,
        llm_analysis_targets,
    )
    logger.debug(
        f"Added conditional edge: {llm_analysis_node} -> {llm_analysis_targets}"
    )

    # 条件边 3：structure_reasoning_node 结构化完成后路由
    # 结构化节点产生的 AIMessage 可能包含 tool_calls 或直接结束
    if structured_llm:
        builder.add_conditional_edges(
            structure_reasoning_node,
            should_continue,
            [crash_tool_node, "__end__"],
        )
        logger.debug(
            f"Added conditional edge: {structure_reasoning_node} -> [crash_tool_node, __end__]"
        )

    # 条件边 3：crash_tool_node 执行完毕后，检查是否是最后一步
    # 如果是最后一步（is_last_step=True），直接结束，避免超出 recursion_limit
    # 这是因为从 llm_analysis_node 经 should_continue 路由到 crash_tool_node 时，
    # is_last_step 可能为 False（remaining=2），但 crash_tool_node 执行后 remaining=1，
    # 此时再用固定边进入 llm_analysis_node 会导致 remaining=0 → 递归超限
    builder.add_conditional_edges(
        crash_tool_node,
        after_crash_tool,
        [llm_analysis_node, "__end__"],
    )
    logger.debug(
        f"Added conditional edge: {crash_tool_node} -> [{llm_analysis_node}, __end__]"
    )

    # =========================================================================
    # 5. 编译图
    # =========================================================================
    logger.info("Compiling agent graph...")
    graph = builder.compile(
        checkpointer=checkpointer,  # 启用状态检查点
        name="vmcore_analysis_agent",  # 图的唯一标识名称
    )
    logger.info("✅ Agent graph compiled successfully.")

    # 可选：在调试模式下保存图结构到文件（需要 graphviz 支持）
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

    return graph
