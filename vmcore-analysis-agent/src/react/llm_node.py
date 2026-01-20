import json
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage
from .graph_state import AgentState
from src.utils.logging import logger
from src.rag.retrival import diagnostic_knowledge_base
from .prompts import analysis_crash_prompt

llm_analysis_node = "llm_analysis_node"


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
    # diagnostic_knowledge_base json 格式化为字符串

    system_message = analysis_crash_prompt().format(
        diagnostic_knowledge_base=json.dumps(
            diagnostic_knowledge_base, indent=2, ensure_ascii=False
        ),
        VMCoreAnalysisStep_Schema=json.dumps(
            VMCoreAnalysisStep.model_json_schema(), indent=2
        ),
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
