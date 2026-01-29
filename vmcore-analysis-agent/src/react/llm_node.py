import json
import re
from typing import Optional, List, Literal, cast, Any, Dict
from pydantic import BaseModel, Field, model_validator
from langchain_core.messages import AIMessage, SystemMessage
from .graph_state import AgentState
from .nodes import llm_analysis_node
from .prompts import analysis_crash_prompt
from src.utils.logging import logger
from src.rag.retrieval import dkb


class ToolCall(BaseModel):
    command_name: str = Field(
        ..., description="The crash command (e.g., 'dis', 'rd', 'struct')."
    )
    arguments: List[str] = Field(default_factory=list, description="Command arguments.")

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


class VMCoreAnalysisStep(BaseModel):
    step_id: int = Field(..., description="Current step sequence number.")

    analysis_path: Literal["knowledge_base", "general_debugging"] = Field(
        ...,
        description="Specify if you are following a DKB pattern or using general kernel debugging experts logic.",
    )

    reasoning: str = Field(
        ...,
        description="Detailed thought process. If general_debugging, explain which kernel subsystem (Memory, FS, Scheduler) you are investigating.",
    )

    knowledge_base_hit: Optional[str] = Field(
        None, description="The 'trigger' name from DKB (if applicable)."
    )

    action: Optional[ToolCall] = Field(
        None,
        description="The next command to run. Should be None if is_conclusive is True.",
    )

    is_conclusive: bool = Field(False)
    final_diagnosis: Optional[str] = Field(
        None, description="Detailed final root cause and evidence."
    )


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
    curr_token_usage = 0

    # 准备系统消息，包含诊断知识库和输出格式
    system_message = analysis_crash_prompt().format(
        diagnostic_knowledge_base=dkb.model_dump_json(indent=2),
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

    # 核心原因：LLM 是无状态的
    # LLM 本身没有记忆。每次调用 invoke() 都是一个独立的 API 请求，LLM 不会"记住"之前的对话。这和我们使用的聊天界面不同：

    # 每次调用都需要传入完整的上下文消息列表，包括系统消息和之前的对话历史。
    messages_to_send = [SystemMessage(content=system_message), *state["messages"]]
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
            # Fix for: "action":{"command_name":"ps",["-m|grep","UN"]} -> "action":{"command_name":"ps","arguments":["-m|grep","UN"]}
            try:
                content = raw_message.content
                pattern = r'("command_name"\s*:\s*"[^"]*"\s*,)\s*(\[)'
                fixed_content = re.sub(pattern, r'\1 "arguments": \2', content)
                analysis_result = VMCoreAnalysisStep.model_validate_json(fixed_content)
                logger.warning(
                    "Successfully repaired malformed JSON from LLM. "
                    f"Original: {content[:100]}... Fixed: {fixed_content[:100]}..."
                )
            except Exception as repair_err:
                logger.warning(f"JSON repair failed: {repair_err}")

                parsing_error = output_data.get("parsing_error")
                error_msg = (
                    f"Failed to parse LLM output. Raw content: {raw_message.content}"
                )
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
        tool_calls = []
        if analysis_result.action:
            logger.info(
                f"LLM decided to call tool: {analysis_result.action.command_name}"
            )
            # ✅ 修复：LangChain 的 tool_calls["args"] 必须是 dict，不能是 list
            # 将参数列表合并为一个字符串，供后续 nodes.py 拼接
            cmd_args = " ".join(analysis_result.action.arguments)
            tool_calls.append(
                {
                    "name": analysis_result.action.command_name,
                    "args": {"cmd": cmd_args},
                    "id": f"call_{analysis_result.step_id}",
                }
            )
        else:
            logger.info("LLM did not call any tools, returning result directly.")

        # 将结构化后的对象序列化存入 content，并携带调用的工具信息
        response = AIMessage(
            content=analysis_result.model_dump_json(), tool_calls=tool_calls
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
