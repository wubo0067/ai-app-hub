"""
VMCore 分析 Agent 图构建模块

此模块负责创建和编译 LangGraph 状态图，定义节点之间的转换关系和执行流程。
整体流程：
1. 收集 vmcore 基础信息 (gather_vmcore_detail_node)
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
    gather_vmcore_detail,
    call_llm_analysis,
    call_crash_tool,
    llm_analysis_node,
    gather_vmcore_detail_node,
    crash_tool_node,
)
from .edges import should_continue


def create_agent_graph(llm, tools_list: List):
    """
    构建并编译 VMCore 分析 Agent 的状态图。

    Args:
        llm: 语言模型实例（通常是 ChatOpenAI 或类似的模型）
        tools_list: 可用的工具列表（MCP crash 工具）

    Returns:
        CompiledGraph: 编译后的 LangGraph 图实例，可执行 invoke/astream 等方法

    架构说明：
        - 使用 StateGraph 管理 AgentState 状态流转
        - 使用 InMemorySaver 作为检查点存储器，支持状态回溯和恢复
        - 通过条件边（conditional_edges）实现智能路由决策

    节点说明：
        - gather_vmcore_detail_node: 初始节点，收集 vmcore 基础诊断信息
        - llm_analysis_node: LLM 分析节点，根据当前信息生成下一步计划
        - crash_tool_node: 工具执行节点，调用 crash 命令获取详细信息

    边说明：
        - START -> gather_vmcore_detail_node: 固定起点
        - gather_vmcore_detail_node -> should_continue: 根据收集结果决定下一步
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

    # 添加节点 3：收集 vmcore 详细信息节点
    # 初始节点，执行默认的 crash 命令集合收集基础信息
    builder.add_node(gather_vmcore_detail_node, gather_vmcore_detail)
    logger.debug(f"Added node: {gather_vmcore_detail_node}")

    # =========================================================================
    # 4. 定义节点之间的边（执行流程）
    # =========================================================================
    # 固定边：图的入口点，从 START 直接进入信息收集节点
    builder.add_edge(START, gather_vmcore_detail_node)
    logger.debug(f"Added edge: START -> {gather_vmcore_detail_node}")

    # 条件边 1：信息收集完成后根据状态决定下一步
    # should_continue 函数会检查：
    #   - 是否发生错误 -> END
    #   - 是否已有最终答案 -> END
    #   - 否则 -> llm_analysis_node（继续分析）
    builder.add_conditional_edges(
        gather_vmcore_detail_node,
        should_continue,
    )
    logger.debug(
        f"Added conditional edge: {gather_vmcore_detail_node} -> should_continue"
    )

    # 条件边 2：LLM 分析后根据决策结果路由
    # LLM 可能返回：
    #   - 工具调用请求 -> crash_tool_node
    #   - 最终答案 -> END
    #   - 错误状态 -> END
    builder.add_conditional_edges(
        llm_analysis_node,
        should_continue,
    )
    logger.debug(f"Added conditional edge: {llm_analysis_node} -> should_continue")

    # 注意：crash_tool_node 执行完成后会自动返回 llm_analysis_node
    # 这是通过 should_continue 函数中的逻辑实现的

    # =========================================================================
    # 5. 编译图
    # =========================================================================
    logger.info("Compiling agent graph...")
    graph = builder.compile(
        checkpointer=checkpointer,  # 启用状态检查点
        name="vmcore_analysis_agent",  # 图的唯一标识名称
    )
    logger.info("✅ Agent graph compiled successfully.")

    # 可选：在调试模式下打印图结构（需要 graphviz 支持）
    # try:
    #     from IPython.display import Image, display
    #     display(Image(graph.get_graph().draw_mermaid_png()))
    # except Exception as e:
    #     logger.debug(f"Could not display graph visualization: {e}")

    return graph
