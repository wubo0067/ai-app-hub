import asyncio
from typing import List, Tuple

from .graph_state import AgentState
from src.mcp_tools.crash import crash_client
from langgraph.graph.message import ToolMessage
from langgraph.runtime import Runtime


crash_tool_node = "crash_tool_node"
gather_vmcore_detail_node = "gather_vmcore_detail_node"
llm_analysis_node = "llm_analysis_node"

DEFAULT_CRASH_COMMANDS: list[str] = [
    "sys",
    "bt -a",
    "ps -a",
    "runq",
    "dev -i",
    "swap",
    "timer",
    "sig",
    "mach",
    "ipcs",
    "waitq",
]

async def _invoke_tool(tool, cmd: str, state: AgentState) -> str:
    """Invoke a crash MCP tool for a given command safely.

    Returns the tool output or an error string if invocation fails.
    """
    try:
        return await tool.ainvoke(
            {
                "command": cmd,
                "vmcore_path": state.vmcore_path,
                "vmlinux_path": state.vmlinux_path,
            }
        )
    except Exception as exc:
        return f"[error] Tool invocation failed: {exc}"


# 调用 crash MCP 客户端的工具来收集 vmcore 的基本信息
async def gather_vmcore_detail(
    state: AgentState,
) -> dict:
    """执行默认 crash 命令并汇总输出为 ToolMessage。

    - 并发运行可匹配的工具调用以提升整体速度。
    - 对缺少匹配工具与运行异常进行稳健处理并保留上下文。
    """
    crash_output_parts: List[str] = []

    # 快速返回：未配置默认命令
    if not getattr(state, "default_crash_cmd", None):
        return {"messages": [ToolMessage(content="No crash commands configured.")]}

    try:
        with crash_client.session("crash") as session:
            tools = await crash_client.get_tools(session)

            # 为每个命令选择匹配的工具（按前缀匹配），并构建任务
            tasks: List[Tuple[str, asyncio.Future]] = []
            for cmd in DEFAULT_CRASH_COMMANDS:
                tool = next(
                    (t for t in tools if cmd.startswith(getattr(t, "name", ""))), None
                )
                if tool:
                    tasks.append(
                        (cmd, asyncio.ensure_future(_invoke_tool(tool, cmd, state)))
                    )
                else:
                    crash_output_parts.append(
                        f"$ {cmd}\n[warn] No matching crash tool found.\n\n"
                    )

            if tasks:
                # 并发调用匹配的 MCP 工具，减少整体等待时间。
                results = await asyncio.gather(
                    *(t for _, t in tasks), return_exceptions=False
                )
                for (cmd, _), output in zip(tasks, results):
                    crash_output_parts.append(f"$ {cmd}\n{output}\n\n")
    except Exception as exc:
        # 如果是严重错误，直接抛出异常
        return {
            "error": {
                "message": str(exc),
                "node": gather_vmcore_detail_node,
            }
        }

    return {
        "messages": [ToolMessage(content="".join(crash_output_parts))],
        "error": None,
    }


async def call_llm_analysis(state: AgentState, llm_with_tools):

    # 需要结构化输出，返回执行命令，
    pass


async def call_crash_tool(state: AgentState):
    """根据 llm 返回的分析信息，执行 llm 的 crash 工具调用"""
    pass
