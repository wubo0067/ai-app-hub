import json
from typing import Optional, List, Literal, cast
from pydantic import BaseModel, Field
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

    # 准备系统消息，包含诊断知识库和输出格式
    system_message = analysis_crash_prompt().format(
        diagnostic_knowledge_base=dkb.model_dump_json(indent=2),
        VMCoreAnalysisStep_Schema=json.dumps(
            VMCoreAnalysisStep.model_json_schema(), indent=2
        ),
    )

    # 核心原因：LLM 是无状态的
    # LLM 本身没有记忆。每次调用 invoke() 都是一个独立的 API 请求，LLM 不会"记住"之前的对话。这和我们使用的聊天界面不同：

    # 每次调用都需要传入完整的上下文消息列表，包括系统消息和之前的对话历史。
    messages_to_send = [SystemMessage(content=system_message), *state["messages"]]
    # 结构化输出
    llm_analysis = llm_with_tools.with_structured_output(
        VMCoreAnalysisStep, method="json_mode"
    )
    try:
        # ✅ 修复：with_structured_output 返回的是 VMCoreAnalysisStep Pydantic 对象
        analysis_result = cast(
            VMCoreAnalysisStep, await llm_analysis.ainvoke(messages_to_send)
        )

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
            "error": {
                "message": str(e),
                "node": llm_analysis_node,
                "is_error": True,
            },
        }

    return {
        "step_count": 1,
        "messages": [response],
        "error": None,
    }
