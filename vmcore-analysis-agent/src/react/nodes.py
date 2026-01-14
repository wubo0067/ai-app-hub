"""
VMCore 分析 Agent 节点实现

此模块定义了 LangGraph 中各个节点的具体执行逻辑。
包括：
1. gather_vmcore_detail: 收集 vmcore 基础诊断信息
2. call_llm_analysis: LLM 分析节点
3. call_crash_tool: 执行 crash 工具调用
"""

import asyncio
from typing import List, Tuple

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from src.utils.logging import logger
from .graph_state import AgentState
from .prompts import vmcore_detail_prompt
from src.mcp_tools.crash.client import crash_client


# =========================================================================
# 节点名称常量定义
# =========================================================================
crash_tool_node = "crash_tool_node"
gather_vmcore_detail_node = "gather_vmcore_detail_node"
llm_analysis_node = "llm_analysis_node"

# =========================================================================
# 默认 crash 命令集合
# =========================================================================
DEFAULT_CRASH_COMMANDS: list[str] = [
    "sys",  # 系统信息
    "bt",  # 所有线程的堆栈回溯
    # "ps -a",  # 进程列表
    "runq",  # 运行队列
    # "dev -i",  # 设备信息
    "swap",  # 交换空间
    "timer",  # 定时器信息
    "sig",  # 信号处理
    "mach",  # 机器相关信息
    "ipcs",  # IPC 信息
    "waitq",  # 等待队列
]


async def _invoke_tool(tool, cmd: str, state: AgentState) -> str:
    """
    安全地调用 crash MCP 工具执行指定命令。

    Args:
        tool: MCP 工具实例
        cmd: crash 命令字符串
        state: 当前 Agent 状态，包含 vmcore 和 vmlinux 路径

    Returns:
        str: 工具执行的输出结果，如果失败则返回错误信息字符串
    """
    try:
        logger.debug(f"Invoking tool for command: {cmd}")
        result = await tool.ainvoke(
            {
                "command": cmd,
                "vmcore_path": state["vmcore_path"],
                "vmlinux_path": state["vmlinux_path"],
            }
        )
        logger.debug(f"Tool invocation succeeded for: {cmd}")
        return result
    except Exception as exc:
        error_msg = f"[error] Tool invocation failed for '{cmd}': {exc}"
        logger.error(error_msg)
        return error_msg


async def gather_vmcore_detail(state: AgentState) -> dict:
    """
    执行默认 crash 命令集合并汇总输出为 ToolMessage。

    此节点是分析流程的第一步，负责收集 vmcore 的基础诊断信息。
    采用并发执行策略提升性能，并对工具缺失和执行异常进行健壮处理。

    执行流程：
    1. 获取 MCP 会话和工具列表
    2. 为每个默认命令匹配对应的工具
    3. 并发执行所有工具调用
    4. 汇总所有输出结果
    5. 格式化为 prompt 并包装成 ToolMessage

    Args:
        state: AgentState，包含 vmcore_path 和 vmlinux_path

    Returns:
        dict: 包含 messages、error 和 step_count 的状态更新
    """
    crash_output_parts: List[str] = []
    logger.info(f"Starting {gather_vmcore_detail_node} node execution...")

    try:
        # 创建 MCP 会话并获取工具列表
        async with crash_client.session("crash") as session:
            tools = await load_mcp_tools(session)
            logger.info(f"Retrieved {len(tools)} tools from MCP client.")

            # 为每个命令匹配工具并创建异步任务
            tasks: List[Tuple[str, asyncio.Task]] = []
            for cmd in DEFAULT_CRASH_COMMANDS:
                # 按命令前缀匹配工具（例如 "bt -a" 匹配 "bt" 工具）
                tool = next(
                    (t for t in tools if cmd.startswith(getattr(t, "name", ""))),
                    None,
                )
                if tool:
                    tasks.append(
                        (cmd, asyncio.create_task(_invoke_tool(tool, cmd, state)))
                    )
                    logger.debug(f"Matched tool for command: {cmd}")
                else:
                    warning_msg = f"$ {cmd}\n[warn] No matching crash tool found.\n\n"
                    crash_output_parts.append(warning_msg)
                    logger.warning(f"No tool found for command: {cmd}")

            if tasks:
                # 并发执行所有工具调用
                logger.info(f"Executing {len(tasks)} tool invocations concurrently...")
                results = await asyncio.gather(
                    *(task for _, task in tasks), return_exceptions=False
                )

                # 汇总结果
                for (cmd, _), output in zip(tasks, results):
                    crash_output_parts.append(f"$ {cmd}\n{output}\n\n")

                logger.info(f"Successfully executed {len(tasks)} tool invocations.")
            else:
                logger.warning("No tools were executed (all commands failed to match).")

    except Exception as exc:
        # 严重错误：返回错误状态
        error_msg = f"Critical error in {gather_vmcore_detail_node}: {exc}"
        logger.error(error_msg, exc_info=True)
        return {
            "step_count": 1,
            "error": {
                "message": str(exc),
                "node": gather_vmcore_detail_node,
                "is_error": True,
            },
        }

    # 格式化输出为 prompt
    vmcore_output = "".join(crash_output_parts)
    prompt = vmcore_detail_prompt().format(vmcore_detail=vmcore_output)

    logger.info(f"{gather_vmcore_detail_node} completed successfully.")
    return {
        "messages": [HumanMessage(content=prompt)],
        "error": None,
        "step_count": 1,
    }


async def call_llm_analysis(state: AgentState, llm_with_tools) -> dict:
    """
    调用 LLM 分析节点，根据收集到的 vmcore 信息进行智能分析。

    此节点接收前序节点收集的诊断信息，通过 LLM 进行分析并决定：
    1. 是否需要执行更多 crash 命令获取详细信息
    2. 是否已有足够信息给出诊断结论

    Args:
        state: AgentState，包含历史消息和上下文
        llm_with_tools: 绑定了工具的 LLM 实例

    Returns:
        dict: 包含 messages、error 和 step_count 的状态更新
    """
    logger.info(
        f"Starting {llm_analysis_node} node execution (step {state.get('step_count', 0)})..."
    )

    # TODO: 实现 LLM 调用逻辑
    # 1. 提取历史消息
    # 2. 调用 llm_with_tools.ainvoke(messages)
    # 3. 解析 LLM 响应（工具调用 or 最终答案）
    # 4. 返回对应的状态更新

    return {
        "step_count": 1,
        "messages": [
            AIMessage(
                content=f"LLM analysis step {state.get('step_count', 0)} - analyzing vmcore data..."
            )
        ],
        "error": None,
    }


async def call_crash_tool(state: AgentState) -> dict:
    """
    根据 LLM 返回的分析决策，执行具体的 crash 工具调用。

    此节点从 LLM 的响应中提取工具调用请求，执行对应的 crash 命令，
    并将结果返回给 LLM 进行进一步分析。

    Args:
        state: AgentState，包含 LLM 的工具调用请求

    Returns:
        dict: 包含 messages、error 和 step_count 的状态更新
    """
    logger.info(
        f"Starting {crash_tool_node} node execution (step {state.get('step_count', 0)})..."
    )

    # TODO: 实现工具调用逻辑
    # 1. 从 state.messages 中提取最后一条 AIMessage
    # 2. 解析其中的 tool_calls
    # 3. 调用对应的 MCP 工具
    # 4. 将结果包装为 ToolMessage 返回

    return {
        "step_count": 1,
        "messages": [
            ToolMessage(
                content=f"Crash tool execution step {state.get('step_count', 0)} - results here...",
                tool_call_id="example_tool_call_id",
            )
        ],
        "error": None,
    }
