import json
import re
from typing import Optional, List, Literal, cast, Any, Dict
from pydantic import BaseModel, Field, model_validator
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from json_repair import repair_json
from .graph_state import AgentState
from .nodes import llm_analysis_node, structure_reasoning_node
from .prompts import analysis_crash_prompt, structure_reasoning_prompt
from src.utils.logging import logger


class ToolCall(BaseModel):
    command_name: str = Field(
        ..., description="The crash command (e.g., 'dis', 'rd') or 'run_script'."
    )
    arguments: List[str] = Field(
        default_factory=list,
        description="Command arguments. For 'run_script', each string is a separate command line.",
    )

    # 模型验证器，用于修复 LLM 输出的格式错误，作用：定义一个在模型实例化之前运行的验证器
    # 模式："before" 表示在 Pydantic 解析输入数据到模型字段之前执行
    # 参数：接收原始输入数据，可以修改后再传递给模型
    @model_validator(mode="before")
    # 作用：将方法标记为类方法
    # 访问权限：允许方法通过类而不是实例被调用
    # 参数：第一个参数是 cls（代表类本身）
    @classmethod
    def fix_malformed_action(cls, data: Any) -> Any:
        """修复 LLM 输出的常见格式错误"""
        if isinstance(data, dict):
            # 修复：{"command_name": "ps", ["-m"]} -> {"command_name": "ps", "arguments": ["-m"]}
            if "command_name" in data and "arguments" not in data:
                # 查找字典中除 command_name 外的列表值
                for key, value in list(data.items()):
                    if isinstance(value, list):
                        data["arguments"] = value
                        if key != "command_name":
                            del data[key]
                        break
                # 如果还是没有 arguments，设置为空列表
                if "arguments" not in data:
                    data["arguments"] = []
        return data


class SuspectCode(BaseModel):
    """可疑代码位置"""

    file: str = Field(..., description="Source file path")
    function: str = Field(..., description="Function name")
    line: str = Field(..., description="Line number or 'unknown'")


class FinalDiagnosis(BaseModel):
    """最终诊断结果的完整结构"""

    crash_type: str = Field(
        ...,
        description="Crash type (e.g., NULL pointer dereference, use-after-free, soft lockup)",
    )
    panic_string: str = Field(..., description="Exact panic string from dmesg")
    faulting_instruction: str = Field(
        ..., description="RIP address and disassembly of faulting instruction"
    )
    root_cause: str = Field(
        ..., description="1-2 sentence root cause explanation with evidence"
    )
    detailed_analysis: str = Field(
        ...,
        description="Multi-paragraph analysis with full evidence chain and kernel subsystem context",
    )
    suspect_code: SuspectCode = Field(..., description="Suspected source code location")
    evidence: List[str] = Field(
        ...,
        description="List of key evidence points (register values, memory contents, etc.)",
    )


class VMCoreAnalysisStep(BaseModel):
    step_id: int = Field(..., description="Current step sequence number.")

    reasoning: str = Field(
        ...,
        description="Detailed thought process. Explain which kernel subsystem (Memory, FS, Scheduler) you are investigating and why.",
    )

    action: Optional[ToolCall] = Field(
        None,
        description="The next command to run. Should be None if is_conclusive is True.",
    )

    is_conclusive: bool = Field(False)
    final_diagnosis: Optional[FinalDiagnosis] = Field(
        None, description="Detailed final root cause and evidence."
    )
    fix_suggestion: Optional[str] = Field(
        None,
        description="Recommended fix or workaround (e.g., 'Update kernel', 'Hardware replacement needed')",
    )
    confidence: Optional[Literal["high", "medium", "low"]] = Field(
        None, description="Confidence level of the diagnosis"
    )
    additional_notes: Optional[str] = Field(
        None,
        description="Any caveats, alternative hypotheses, or recommended follow-up actions",
    )


def _compress_messages_for_llm(
    messages: list,
    max_tool_output_chars: int = 4000,
    old_reasoning_head: int = 200,
    old_reasoning_tail: int = 500,
    recent_ai_messages_to_keep: int = 2,
    recent_tool_messages_to_keep: int = 2,
) -> list:
    """
    在发送给 LLM 前对消息历史进行保守压缩，降低 token 消耗。

    策略：
    1. 保留最近几条 AIMessage 和 ToolMessage 的完整内容，尽量减少对当前推理链的干扰。
    2. 更早的 AIMessage 的 reasoning_content 保留头部 + 尾部，中间截断。
       - 头部（old_reasoning_head）：保留"当前在分析什么"的上下文
       - 尾部（old_reasoning_tail）：保留"结论和下一步决策"（推理链结论在尾部）
       ⚠️ DeepSeek-Reasoner API 要求所有 assistant 消息必须包含 reasoning_content 字段，
       不可删除、不可设为 None，否则返回 400 错误。
    3. 对更早的 ToolMessage，仅在超过 max_tool_output_chars 时才截断。

    此函数不修改 AgentState，仅返回压缩后的副本用于当次 LLM 调用。

    Args:
        messages: state["messages"] 原始消息列表
        max_tool_output_chars: ToolMessage 内容允许的最大字符数
        old_reasoning_head: 旧 AIMessage reasoning_content 保留的头部字符数
        old_reasoning_tail: 旧 AIMessage reasoning_content 保留的尾部字符数
        recent_ai_messages_to_keep: 保留完整内容的最近 AIMessage 数量
        recent_tool_messages_to_keep: 保留完整内容的最近 ToolMessage 数量

    Returns:
        压缩后的消息列表（浅拷贝，修改的对象为新建实例）
    """
    ai_msg_indices = [i for i, m in enumerate(messages) if isinstance(m, AIMessage)]
    tool_msg_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    recent_ai_indices = set(ai_msg_indices[-recent_ai_messages_to_keep:])
    recent_tool_indices = set(tool_msg_indices[-recent_tool_messages_to_keep:])
    keep_threshold = old_reasoning_head + old_reasoning_tail

    def _truncate_middle(text: str, head_chars: int, tail_chars: int) -> str:
        keep_chars = head_chars + tail_chars
        if keep_chars <= 0 or len(text) <= keep_chars:
            return text

        omitted = len(text) - keep_chars
        return (
            text[:head_chars]
            + f"\n...[{omitted} chars truncated]...\n"
            + text[-tail_chars:]
        )

    compressed = []
    truncated_reasoning_count = 0
    truncated_tool_count = 0
    reasoning_chars_before = 0
    reasoning_chars_after = 0
    tool_chars_before = 0
    tool_chars_after = 0

    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and i not in recent_ai_indices:
            rc = msg.additional_kwargs.get("reasoning_content", "")
            if isinstance(rc, str):
                reasoning_chars_before += len(rc)
            if rc and len(rc) > keep_threshold:
                new_kwargs = dict(msg.additional_kwargs)
                new_kwargs["reasoning_content"] = _truncate_middle(
                    rc,
                    old_reasoning_head,
                    old_reasoning_tail,
                )
                msg = msg.model_copy(update={"additional_kwargs": new_kwargs})
                truncated_reasoning_count += 1
            if isinstance(msg.additional_kwargs.get("reasoning_content"), str):
                reasoning_chars_after += len(msg.additional_kwargs["reasoning_content"])
        elif (
            isinstance(msg, ToolMessage)
            and i not in recent_tool_indices
            and isinstance(msg.content, str)
            and len(msg.content) > max_tool_output_chars
        ):
            tool_chars_before += len(msg.content)
            tool_head_chars = max_tool_output_chars * 3 // 5
            tool_tail_chars = max_tool_output_chars - tool_head_chars
            truncated_content = _truncate_middle(
                msg.content,
                tool_head_chars,
                tool_tail_chars,
            )
            msg = msg.model_copy(update={"content": truncated_content})
            truncated_tool_count += 1
            tool_chars_after += len(msg.content)
        elif (
            isinstance(msg, ToolMessage)
            and i not in recent_tool_indices
            and isinstance(msg.content, str)
        ):
            tool_chars_before += len(msg.content)
            tool_chars_after += len(msg.content)
        compressed.append(msg)

    if truncated_reasoning_count or truncated_tool_count:
        reasoning_saved = reasoning_chars_before - reasoning_chars_after
        tool_saved = tool_chars_before - tool_chars_after
        logger.info(
            f"[compress] truncated reasoning_content in {truncated_reasoning_count} old AIMessages "
            f"(head={old_reasoning_head}+tail={old_reasoning_tail} chars, "
            f"before={reasoning_chars_before}, after={reasoning_chars_after}, saved={reasoning_saved}), "
            f"truncated {truncated_tool_count} old ToolMessages (limit={max_tool_output_chars}, "
            f"before={tool_chars_before}, after={tool_chars_after}, saved={tool_saved}, "
            f"kept recent ai/tool messages full: {recent_ai_messages_to_keep}/{recent_tool_messages_to_keep})"
        )
    return compressed


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
    # 计算当前步数（之前的累加值 + 本次的 1）
    current_step = state.get("step_count", 0)

    logger.info(f"Starting {llm_analysis_node} node execution (step {current_step})...")
    curr_token_usage = 0

    # 准备系统消息，包含诊断知识库和输出格式
    system_message = analysis_crash_prompt().format(
        VMCoreAnalysisStep_Schema=json.dumps(
            VMCoreAnalysisStep.model_json_schema(), indent=2
        ),
    )

    # 检查是否是最后一步 (LangGraph recursion_limit 触发前)
    # 如果是最后一步，要求 LLM 必须给出最终结论，停止工具调用
    is_last_step = state.get("is_last_step", False)
    if is_last_step:
        logger.warning(
            f"Agent reached the last step (is_last_step=True). Forcing conclusion."
        )
        system_message += (
            "\n\n[CRITICAL WARNING]\n"
            "This is your LAST STEP. You have reached the execution limit.\n"
            "You MUST provide a 'final_diagnosis' based on the information you have gathered so far.\n"
            "Set 'is_conclusive' to true and do NOT request any further tool calls (action must be null)."
        )

    # 压缩消息历史后再发送给 LLM，避免 reasoning_content 累积和大工具输出导致 token 暴增
    compressed_messages = _compress_messages_for_llm(state["messages"])
    messages_to_send = [SystemMessage(content=system_message), *compressed_messages]
    # 结构化输出，设置 include_raw=True 以获取 token 消耗等元数据
    llm_analysis = llm_with_tools.with_structured_output(
        VMCoreAnalysisStep, method="json_mode", include_raw=True
    )
    try:
        # 使用 include_raw=True 后，ainvoke 返回包含 'parsed' 和 'raw' 的字典
        output_data = await llm_analysis.ainvoke(messages_to_send)
        analysis_result = cast(VMCoreAnalysisStep, output_data["parsed"])
        raw_message = cast(AIMessage, output_data["raw"])

        # 获取并记录 token 消耗数量
        usage_metadata = getattr(raw_message, "usage_metadata", {}) or {}
        curr_token_usage = usage_metadata.get("total_tokens", 0)

        # 检查解析结果是否为空
        if analysis_result is None:
            # 尝试修复常见的 JSON 格式错误
            logger.warning(
                "LLM output is empty or could not be parsed. Attempting to repair JSON..."
            )
            try:
                content = raw_message.content
                content_str = (
                    content if isinstance(content, str) else json.dumps(content)
                )
                # logger.debug(f"Raw content: '{content_str[:100]}'")

                # Fix 0: DeepSeek-Reasoner 有时将 JSON 输出放在 reasoning_content 而非 content 中
                if not content_str or not content_str.strip():
                    # logger.warning(
                    #     "LLM output content is empty. Checking reasoning_content for JSON..."
                    # )
                    reasoning = raw_message.additional_kwargs.get(
                        "reasoning_content", ""
                    )
                    logger.debug(
                        f"use reasoning content: '{reasoning[:50] if reasoning else ''}' to attempt JSON repair"
                    )
                    if reasoning and "{" in reasoning:
                        logger.warning(
                            "Content is empty/whitespace, attempting to extract JSON from reasoning_content"
                        )
                        content_str = reasoning
                    elif reasoning and len(reasoning) > 50:
                        # reasoning_content 是纯文本推理（非 JSON），路由到 structure_reasoning_node
                        logger.warning(
                            "Content is empty and reasoning_content is plain text (no JSON). "
                            "Routing to structure_reasoning_node for structuring."
                        )
                        return {
                            "step_count": 1,
                            "token_usage": curr_token_usage,
                            "reasoning_to_structure": reasoning,
                            "reasoning_additional_kwargs": raw_message.additional_kwargs.copy(),
                            "error": None,
                        }

                # 优先尝试使用 json_repair 修复 JSON
                try:
                    repaired_obj = repair_json(content_str, return_objects=True)
                    if isinstance(repaired_obj, list):
                        # 如果返回列表（即使只有一个元素），取出第一个字典
                        for item in repaired_obj:
                            if isinstance(item, dict):
                                repaired_obj = item
                                break

                    if isinstance(repaired_obj, dict):
                        # 尝试直接验证修复后的对象
                        analysis_result = VMCoreAnalysisStep.model_validate(
                            repaired_obj
                        )
                        logger.warning(
                            "Successfully repaired malformed JSON from LLM using json_repair."
                        )
                except Exception as e:
                    logger.debug(
                        f"json_repair failed: '{e}', falling back to manual fix"
                    )
                    pass

                if analysis_result is None:
                    # 如果 json_repair 失败，尝试手动修复逻辑

                    # Fix 1: 提取 JSON 部分并移除 trailing characters
                    # 尝试找到最外层的 JSON 对象
                    # 查找第一个 '{' 和最后一个匹配的 '}'
                    first_brace = content_str.find("{")
                    if first_brace != -1:
                        # 查找匹配的结束大括号
                        brace_count = 0
                        last_brace = -1
                        for i in range(first_brace, len(content_str)):
                            if content_str[i] == "{":
                                brace_count += 1
                            elif content_str[i] == "}":
                                brace_count -= 1
                                if brace_count == 0:
                                    last_brace = i
                                    break

                        if last_brace != -1:
                            content_str = content_str[first_brace : last_brace + 1]
                            logger.info(
                                f"Extracted JSON from position {first_brace} to {last_brace+1}"
                            )

                    # Fix 2: 修复无效的 JSON 转义序列（LLM 经常混淆 bash 和 JSON 转义）
                    # \| → | (管道符在 JSON 中不需要转义)
                    # \/ → / (斜杠在 JSON 中不需要转义)
                    # \> → > (重定向符在 JSON 中不需要转义)
                    # \< → < (重定向符在 JSON 中不需要转义)
                    # \& → & (与符号在 JSON 中不需要转义)
                    invalid_escapes = [
                        (r"\|", "|"),
                        (r"\/", "/"),
                        (r"\>", ">"),
                        (r"\<", "<"),
                        (r"\&", "&"),
                    ]
                    for pattern, replacement in invalid_escapes:
                        content_str = content_str.replace(pattern, replacement)

                    # Fix 3: 修复缺失的 arguments 字段
                    # "action":{"command_name":"ps",["-m"]} -> "action":{"command_name":"ps","arguments":["-m"]}
                    pattern = r'("command_name"\s*:\s*"[^"]*"\s*,)\s*(\[)'
                    content_str = re.sub(pattern, r'\1 "arguments": \2', content_str)

                    analysis_result = VMCoreAnalysisStep.model_validate_json(
                        content_str
                    )
                    logger.warning(
                        "Successfully repaired malformed JSON from LLM (manual fix). "
                        f"Original: {content[:200]}... Fixed: {content_str[:200]}..."
                    )
            except Exception as repair_err:
                logger.warning(f"JSON repair failed: '{repair_err}'")

                # Fallback: 如果存在 reasoning_content，路由到 structure_reasoning_node
                # 让 deepseek-chat 将纯文本推理内容结构化为 VMCoreAnalysisStep
                reasoning = raw_message.additional_kwargs.get("reasoning_content", "")
                if reasoning and len(reasoning) > 50:
                    logger.warning(
                        "JSON repair failed but reasoning_content available. "
                        "Routing to structure_reasoning_node for structuring."
                    )
                    return {
                        "step_count": 1,
                        "token_usage": curr_token_usage,
                        "reasoning_to_structure": reasoning,
                        "reasoning_additional_kwargs": raw_message.additional_kwargs.copy(),
                        "error": None,
                    }

                parsing_error = output_data.get("parsing_error")
                error_msg = f"Failed to parse LLM output. Raw content: {repr(raw_message.content)}"
                if parsing_error:
                    error_msg += f". Parsing error: {parsing_error}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        # 记录 response
        logger.debug(
            f"LLM Analysis Result: {analysis_result.model_dump_json(indent=2)}"
        )

        # 手动构造 AIMessage 以便 edges.py 识别路由。
        # 如果 LLM 决定调用工具 (action 不为空)，我们需要手动填充 tool_calls
        # 安全屏障：当 is_last_step=True 时，强制清除 action，阻止生成 tool_calls
        tool_calls = []
        if is_last_step and analysis_result.action:
            logger.warning(
                "is_last_step=True but LLM still returned action. "
                "Stripping tool_calls to force conclusion."
            )
            analysis_result.action = None
        if analysis_result.action:
            logger.info(
                f"LLM decided to call tool: {analysis_result.action.command_name}"
            )

            # 根据工具类型构建参数
            tool_name = analysis_result.action.command_name
            tool_args = {}

            if tool_name == "run_script":
                # 对于 run_script，将参数列表拼接为换行分隔的脚本
                script_content = "\n".join(analysis_result.action.arguments)
                tool_args = {"script": script_content}
            else:
                # 对于普通命令，将参数列表拼接为单个命令字符串
                # 注意：这里我们使用 'command' 作为参数名，以匹配 MCP server 的定义
                # 之前代码可能使用了 'cmd'，这取决于 nodes.py 的处理。
                # 为了兼容性，我们可以暂时保留 cmd 或者同时提供（如果允许额外参数）
                # 但根据规范，应该是 command。此处假设 nodes.py 能透传或已调整。
                # 修正：为了最稳妥，我们查看之前的代码使用的是 args={"cmd": ...}
                # 如果 run_script 走的是同样的 tool invoke 路径，我们需要确保参数名正确。
                cmd_args = " ".join(analysis_result.action.arguments)
                # 使用 legacy 的 'cmd' 还是标准的 'command'？
                # 鉴于 run_script 必须用 script，我们这里尝试使用 command 以对齐 server。
                # 如果之前代码用 cmd 能跑，说明 nodes.py 可能做了映射 command = args['cmd']
                # 为了安全，我们还是沿用之前的模式，但是 run_script 必须是 script
                tool_args = {"command": cmd_args}

            tool_calls.append(
                {
                    "name": tool_name,
                    "args": tool_args,
                    "id": f"call_{analysis_result.step_id}",
                }
            )
        else:
            logger.info("LLM did not call any tools, returning result directly.")

        # 将结构化后的对象序列化存入 content，并携带调用的工具信息
        # 必须保留 additional_kwargs 中的 reasoning_content，否则下一轮对话 DeepSeek-Reasoner 会报错 (Error 400)
        # DeepSeek-Reasoner 模式下，之前的 assistant 消息必须包含 reasoning_content
        # 如果 reasoning_content 存在，日志记录 reasoning_content 的前 100 字符以供调试

        reasoning_content = raw_message.additional_kwargs.get("reasoning_content")
        if reasoning_content:
            logger.debug(f"reasoning_content: {reasoning_content[:100]}...")
        else:
            logger.warning(
                f"No reasoning_content found in additional_kwargs. additional_kwargs keys: {raw_message.additional_kwargs.keys()}"
            )

        response = AIMessage(
            content=analysis_result.model_dump_json(),
            tool_calls=tool_calls,
            additional_kwargs=raw_message.additional_kwargs.copy(),
        )

    except Exception as e:
        logger.error(f"Error during LLM analysis: {e}", exc_info=True)
        return {
            "step_count": 1,
            "token_usage": curr_token_usage,
            "error": {
                "message": str(e),
                "node": llm_analysis_node,
                "is_error": True,
            },
        }

    return {
        "step_count": 1,
        "token_usage": curr_token_usage,
        "messages": [response],
        "error": None,
    }


async def structure_reasoning_content(state: AgentState, chat_llm) -> dict:
    """
    使用 deepseek-chat 模型将 DeepSeek-Reasoner 的纯文本 reasoning_content 结构化为 VMCoreAnalysisStep。

    当 Reasoner 模型返回空 content 但有纯文本 reasoning_content 时，
    此节点接收该文本并通过 Chat 模型将其转换为结构化的分析步骤。

    Args:
        state: AgentState，包含 reasoning_to_structure 和 reasoning_additional_kwargs
        chat_llm: deepseek-chat LLM 实例

    Returns:
        dict: 包含 messages、reasoning_to_structure(清空) 等状态更新
    """
    reasoning = state.get("reasoning_to_structure", "")
    original_kwargs = state.get("reasoning_additional_kwargs", {}) or {}
    current_step = state.get("step_count", 0)
    is_last_step = state.get("is_last_step", False)

    logger.info(
        f"Starting {structure_reasoning_node} node execution (step {current_step})..."
    )
    logger.debug(
        f"Reasoning to structure (first 200 chars): {(reasoning or '')[:200]}..."
    )

    curr_token_usage = 0

    # 构建结构化提示
    schema_json = json.dumps(VMCoreAnalysisStep.model_json_schema(), indent=2)

    force_conclusion = ""
    if is_last_step:
        force_conclusion = (
            "\n\nIMPORTANT: This is the LAST STEP. You MUST set 'is_conclusive' to true, "
            "'action' to null, and provide a 'final_diagnosis' based on the reasoning."
        )

    system_prompt = structure_reasoning_prompt().format(
        force_conclusion=force_conclusion,
        schema_json=schema_json,
        reasoning=reasoning,
    )

    # 只需传入 system_prompt（已包含完整 reasoning 内容和目标 schema）
    # 无需携带历史对话，避免浪费 token
    messages_to_send = [
        SystemMessage(content=system_prompt),
    ]

    chat_with_structured = chat_llm.with_structured_output(
        VMCoreAnalysisStep, method="json_mode", include_raw=True
    )

    try:
        output_data = await chat_with_structured.ainvoke(messages_to_send)
        analysis_result = cast(VMCoreAnalysisStep, output_data["parsed"])
        raw_chat_message = cast(AIMessage, output_data["raw"])

        usage_metadata = getattr(raw_chat_message, "usage_metadata", {}) or {}
        curr_token_usage = usage_metadata.get("total_tokens", 0)

        if analysis_result is None:
            # 尝试 json_repair
            content = raw_chat_message.content
            content_str = content if isinstance(content, str) else json.dumps(content)
            try:
                repaired_obj = repair_json(content_str, return_objects=True)
                if isinstance(repaired_obj, list):
                    for item in repaired_obj:
                        if isinstance(item, dict):
                            repaired_obj = item
                            break
                if isinstance(repaired_obj, dict):
                    analysis_result = VMCoreAnalysisStep.model_validate(repaired_obj)
                    logger.warning(
                        "structure_reasoning_node: repaired JSON via json_repair."
                    )
            except Exception as e:
                logger.debug(f"structure_reasoning_node: json_repair failed: '{e}'")

        if analysis_result is None:
            raise ValueError(
                f"Chat model failed to structure reasoning. "
                f"Raw: {repr(raw_chat_message.content[:200])}"
            )

        logger.info(
            f"structure_reasoning_node: Successfully structured reasoning content. "
            f"LLM Analysis Result: {analysis_result.model_dump_json(indent=2)}"
        )

        # 构建 tool_calls（与 call_llm_analysis 相同逻辑）
        tool_calls = []
        if is_last_step and analysis_result.action:
            logger.warning(
                "is_last_step=True in structure_reasoning_node, stripping tool_calls."
            )
            analysis_result.action = None

        if analysis_result.action:
            tool_name = analysis_result.action.command_name
            tool_args = {}
            if tool_name == "run_script":
                script_content = "\n".join(analysis_result.action.arguments)
                tool_args = {"script": script_content}
            else:
                cmd_args = " ".join(analysis_result.action.arguments)
                tool_args = {"command": cmd_args}

            tool_calls.append(
                {
                    "name": tool_name,
                    "args": tool_args,
                    "id": f"call_{analysis_result.step_id}",
                }
            )

        # 使用原始的 additional_kwargs（含 reasoning_content）以确保
        # 下一轮 DeepSeek-Reasoner 调用时 assistant 消息包含 reasoning_content
        response = AIMessage(
            content=analysis_result.model_dump_json(),
            tool_calls=tool_calls,
            additional_kwargs=original_kwargs,
        )

    except Exception as e:
        logger.error(f"Error in structure_reasoning_node: {e}", exc_info=True)
        return {
            "step_count": 0,
            "token_usage": curr_token_usage,
            "reasoning_to_structure": None,
            "reasoning_additional_kwargs": None,
            "error": {
                "message": str(e),
                "node": structure_reasoning_node,
                "is_error": True,
            },
        }

    return {
        "step_count": 0,  # 步数已在 llm_analysis_node 中计入
        "token_usage": curr_token_usage,
        "messages": [response],
        "reasoning_to_structure": None,
        "reasoning_additional_kwargs": None,
        "error": None,
    }
