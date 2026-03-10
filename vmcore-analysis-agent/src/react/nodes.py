"""
VMCore 分析 Agent 节点实现

此模块定义了 LangGraph 中各个节点的具体执行逻辑。
包括：
1. collect_crash_init_data: 收集 vmcore 基础诊断信息
2. call_llm_analysis: LLM 分析节点
3. call_crash_tool: 执行 crash 工具调用
"""

import asyncio
import re
from typing import List, Tuple, Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from src.utils.logging import logger
from .graph_state import AgentState
from .prompts import crash_init_data_prompt
from src.mcp_tools.crash.client import crash_client


# =========================================================================
# 节点名称常量定义
# =========================================================================
crash_tool_node = "crash_tool_node"
collect_crash_init_data_node = "collect_crash_init_data_node"
llm_analysis_node = "llm_analysis_node"
structure_reasoning_node = "structure_reasoning_node"

# =========================================================================
# 默认 crash 命令集合
# =========================================================================
DEFAULT_CRASH_COMMANDS: list[str] = [
    "sys",  # 系统信息
    "sys -t",  # 内核的 taint 状态（kernel taint flags
    "bt",  # 所有线程的堆栈回溯
    # "ps -a",  # 进程列表
    # "runq",  # 运行队列
    # "dev -i",  # 设备信息
    # "swap",  # 交换空间
    # "timer",  # 定时器信息
    # "sig",  # 信号处理
    # "mach",  # 机器相关信息
    # "ipcs",  # IPC 信息
    # "waitq",  # 等待队列
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

        # 处理可能返回的列表结构（LangChain MCP 工具通常返回 [{'type': 'text', 'text': '...'}]）
        if isinstance(result, list):
            text_parts = [
                item.get("text", "")
                for item in result
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "".join(text_parts).strip()

        return str(result).strip()
    except Exception as exc:
        error_msg = f"[error] Tool invocation failed for '{cmd}': {exc}"
        logger.error(error_msg)
        return error_msg


async def dispatch_crash_commands(
    commands: List[str], state: AgentState
) -> List[Tuple[str, Any]]:
    """
    匹配工具并并发执行 crash 命令。
    此函数会管理 MCP 会话的生命周期。

    Args:
        commands: 待执行的命令列表
        state: 当前 Agent 状态

    Returns:
        List[Tuple[str, Any]]: [(命令，结果)]
    """
    tasks: List[Tuple[str, asyncio.Task]] = []

    # 创建 MCP 会话并获取工具列表
    async with crash_client.session("crash") as session:
        tools = await load_mcp_tools(session)
        logger.info(f"Retrieved {len(tools)} tools from MCP client.")

        for cmd in commands:
            # 命令分解：工具名 + 参数。例如 "run_script mod -s ..." -> tool_name="run_script"
            parts = cmd.split(" ", 1)
            tool_name = parts[0]
            # 注意：实际传递给 invoke 的 command 并不只是剩余参数，
            # 需要根据工具定义的 schema 构造 payload。
            # 这里目前的 dispatch_crash_commands 假设所有工具只有一个参数 'command'，值为完整命令行。
            # 但 run_script 的参数名是 'script'。这就需要差异化处理。

            # 使用精准匹配而不是 startswith，防止 "sys" 匹配到 "systemd" 等（如果有的话）
            tool = next(
                (t for t in tools if t.name == tool_name),
                None,
            )

            if tool:
                # 构造调用载荷
                payload = {
                    "vmcore_path": state["vmcore_path"],
                    "vmlinux_path": state["vmlinux_path"],
                }

                if tool_name == "run_script":
                    # run_script 工具需要的参数是 'script'
                    # 其内容应该是除了工具名之外的所有部分
                    script_content = parts[1] if len(parts) > 1 else ""
                    # 之前的 cmd 构造逻辑已经处理了 join，这里直接透传内容即可
                    # 但需要注意，cmd 是 "run_script line1\nline2..."
                    payload["script"] = script_content
                else:
                    # 其他标准 crash 工具，需要的参数是 'command'，值为完整命令行
                    payload["command"] = cmd

                tasks.append((cmd, asyncio.create_task(tool.ainvoke(payload))))
                logger.debug(f"Matched tool '{tool_name}' for input: {cmd[:50]}...")
            else:
                logger.warning(f"No tool found for command: {cmd}")

        results = []
        if tasks:
            logger.info(f"Executing {len(tasks)} tool invocations concurrently...")
            raw_results = await asyncio.gather(
                *(task for _, task in tasks), return_exceptions=True
            )

            # 处理 MCP 工具返回的结果格式（通常是 [{'type': 'text', 'text': '...'}]）
            for res in raw_results:
                if isinstance(res, Exception):
                    results.append(res)
                elif isinstance(res, list):
                    text_parts = [
                        item.get("text", "")
                        for item in res
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    # 如果提取到了文本块，拼接并去除首尾空白
                    if text_parts:
                        results.append("".join(text_parts).strip())
                    else:
                        # 列表非空但未匹配到标准 text 块，兜底转字符串
                        results.append(str(res).strip() if res else "")
                else:
                    results.append(str(res).strip())

    return list(zip([cmd for cmd, _ in tasks], results))


async def collect_crash_init_data(state: AgentState) -> dict:
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
    logger.info(f"Starting {collect_crash_init_data_node} node execution...")

    pid = 0
    cpu = 0
    command = ""
    found_panic_task = False

    try:
        # 委托给 dispatch_crash_commands 处理工具加载和执行
        matched_results = await dispatch_crash_commands(DEFAULT_CRASH_COMMANDS, state)

        if matched_results:
            # 汇总结果
            for cmd, output in matched_results:
                if isinstance(output, Exception):
                    error_msg = f"$ {cmd}\n[error] Execution failed: {str(output)}\n\n"
                    crash_output_parts.append(error_msg)
                    logger.error(f"Tool execution failed for '{cmd}': {output}")
                    continue

                # 从 bt 输出中提取 vmcore-dmesg.txt 的查找特征
                if cmd.startswith("bt") and not found_panic_task:
                    for line in output.splitlines():
                        if line.startswith("PID:"):
                            match = re.search(
                                r"PID:\s+(\d+)\s+TASK:\s+\S+\s+CPU:\s+(\d+)\s+COMMAND:\s+\"([^\"]+)\"",
                                line,
                            )
                            if match:
                                pid = int(match.group(1))
                                cpu = int(match.group(2))
                                command = match.group(3)
                                found_panic_task = True
                                logger.debug(
                                    f"Extracted from bt - PID: {pid}, CPU: {cpu}, COMMAND: {command}"
                                )
                                break  # 只提取第一个（通常是崩溃现场）

                crash_output_parts.append(f"$ {cmd}\n{output}\n\n")

            logger.info(
                f"Successfully executed tools. Valid results: {len(crash_output_parts)}"
            )
        else:
            logger.warning("No tools were executed (all commands failed to match).")

    except Exception as exc:
        # 严重错误：返回错误状态
        error_msg = f"Critical error in {collect_crash_init_data_node}: {exc}"
        logger.error(error_msg, exc_info=True)
        return {
            "step_count": 1,
            "error": {
                "message": str(exc),
                "node": collect_crash_init_data_node,
                "is_error": True,
            },
        }

    # 只有在成功提取到关键信息后，才去处理 dmesg
    if found_panic_task:

        def _read_dmesg_context(path: str, t_cpu: int, t_pid: int, t_comm: str) -> str:
            """Synchronous helper to read dmesg, to be run in a thread."""
            try:
                # 读取 vmcore-dmesg.txt 全部内容
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    dmesg_lines = f.readlines()

                # 对进程名进行正则转义，防止特殊字符干扰匹配
                esc_comm = re.escape(t_comm)
                # 构建正则：匹配包含指定 CPU、PID 和进程名的 dmesg 行，
                # 用于定位崩溃现场在 dmesg 日志中的位置
                pattern = re.compile(
                    rf"CPU:\s*{t_cpu}.*PID:\s*{t_pid}.*Comm:\s*{esc_comm}"
                )

                # 逐行扫描，找到匹配行后提取上下文窗口
                for i, line in enumerate(dmesg_lines):
                    if pattern.search(line):
                        # 向前取 50 行（崩溃前的上下文），向后取 50 行（崩溃后的调用栈/日志）
                        start = max(0, i - 50)
                        end = min(len(dmesg_lines), i + 50)
                        return (
                            f"$ vmcore-dmesg (extracted around CPU:{t_cpu} PID:{t_pid} Comm:{t_comm})\n"
                            + "".join(dmesg_lines[start:end])
                            + "\n\n"
                        )
                # 未找到匹配行，返回空字符串
                return ""
            except Exception as e:
                logger.error(f"Failed to read dmesg: {e}")
                return ""

        # 在线程池中执行文件 IO，避免阻塞事件循环
        try:
            dmesg_output = await asyncio.to_thread(
                _read_dmesg_context, state["vmcore_dmesg_path"], cpu, pid, command
            )

            if dmesg_output:
                crash_output_parts.append(dmesg_output)
                logger.info(
                    f"Extracted vmcore-dmesg content around CPU:{cpu} PID:{pid} Comm:{command}. dmesg_output: {dmesg_output}"
                )
            else:
                logger.warning(
                    f"No matching line found in vmcore-dmesg for CPU:{cpu} PID:{pid} Comm:{command}."
                )
        except Exception as exc:
            logger.error(f"Error during dmesg extraction: {exc}", exc_info=True)
    else:
        logger.warning(
            "Skipping vmcore-dmesg extraction: match info (PID/CPU/COMMAND) not found in 'bt' output."
        )

    if state.get("debug_symbol_paths"):
        crash_output_parts.append(
            "$ Third-Party Kernel Modules with Debugging Symbols:\n"
        )
        for module_path in state["debug_symbol_paths"]:
            crash_output_parts.append(f"- {module_path}\n")
        crash_output_parts.append("\n")

    # 格式化输出为 prompt
    vmcore_init_info = "".join(crash_output_parts)
    prompt = crash_init_data_prompt().format(init_info=vmcore_init_info)

    logger.info(f"{collect_crash_init_data_node} completed successfully.")
    return {
        "messages": [HumanMessage(content=prompt)],
        "error": None,
        "step_count": 1,
    }


async def call_crash_tool(state: AgentState) -> dict:
    """
    根据 LLM 返回的分析决策，执行具体的 crash 工具调用。
    支持处理一次响应中的多个工具调用。

    此节点从 LLM 的响应中提取所有工具调用请求，并发执行对应的 crash 命令，
    并为每个调用生成对应的 ToolMessage 返回给 LLM。

    Args:
        state: AgentState，包含 LLM 的工具调用请求

    Returns:
        dict: 包含 messages、error 和 step_count 的状态更新
    """
    current_step = state.get("step_count", 0)
    logger.info(f"Starting {crash_tool_node} node execution (step {current_step})...")

    last_message = state["messages"][-1]
    tool_messages = []

    # 提取所有工具调用的命令，准备批量执行
    # 为了后续能将结果匹配回 tool_call_id，我们需要维护一个映射或顺序
    tool_calls_data = []  # List[(tool_call_id, tool_name, command_string)]
    commands_to_run = []  # List[str]

    try:
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            for tool_call in last_message.tool_calls:
                tool_call_id = tool_call["id"]
                name = tool_call["name"]
                args = tool_call.get("args", {})

                # 拼接命令：假设 args 的值即为参数，按顺序拼接
                args_str = (
                    " ".join(str(v) for v in args.values())
                    if isinstance(args, dict)
                    else str(args)
                )
                full_cmd = f"{name} {args_str}".strip()

                tool_calls_data.append((tool_call_id, name, full_cmd))
                commands_to_run.append(full_cmd)

                logger.debug(
                    f"Processing tool call: {name} (ID: {tool_call_id}) -> Cmd: {full_cmd}"
                )

            # 使用公共函数批量执行命令
            if commands_to_run:
                matched_results = await dispatch_crash_commands(commands_to_run, state)
                # matched_results 是 List[(cmd, output)]，只包含成功匹配并执行的结果

                # 将结果映射回 ToolMessage
                for tool_call_id, tool_name, original_cmd in tool_calls_data:
                    found_result = False

                    # 在执行结果中查找匹配的命令
                    # 注意：如果命令重复，这里可能会总是取到第一个结果。
                    # 但对于 crash 工具来说，只要命令完全一致，结果通常是一样的。
                    for r_cmd, r_output in matched_results:
                        if r_cmd == original_cmd:
                            content = str(r_output)
                            if isinstance(r_output, Exception):
                                content = f"[error] Execution failed: {r_output}"
                            tool_messages.append(
                                ToolMessage(
                                    content=content,
                                    tool_call_id=tool_call_id,
                                    name=tool_name,
                                )
                            )
                            found_result = True
                            # 找到一个就可以停止内层循环，进入下一个 tool_call
                            # 实际上这可以处理重复命令的情况：每个 tool_call 都能匹配到结果
                            break

                    if not found_result:
                        # 没在结果里找到，说明 tool 没匹配上（因为 dispatch_crash_commands 会忽略未匹配的命令）
                        msg = f"[error] No matching crash tool found for command: {original_cmd}"
                        tool_messages.append(
                            ToolMessage(
                                content=msg, tool_call_id=tool_call_id, name=tool_name
                            )
                        )

    except Exception as exc:
        logger.error(f"Critical error in call_crash_tool: {exc}", exc_info=True)
        # 即使发生严重错误，也尽量返回一个空的或错误的消息以避免流程卡死
        return {
            "step_count": 1,
            "error": {"message": str(exc), "node": crash_tool_node, "is_error": True},
        }

    logger.info(f"Generated {len(tool_messages)} tool messages.")

    return {
        "step_count": 1,
        "messages": tool_messages,
        "error": None,
    }
