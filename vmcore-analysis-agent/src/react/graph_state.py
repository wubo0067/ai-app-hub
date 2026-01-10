from dataclasses import dataclass, field
from typing import Annotated
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages, AnyMessage


def add_and_trim(old, new, max_len=8):
    # 确保 old 和 new 都是列表格式
    if not isinstance(old, list):
        old = [old] if old else []
    if not isinstance(new, list):
        new = [new] if new else []

    # 合并消息列表
    merged = old + new

    # 返回最后 max_len 条消息
    return merged[-max_len:] if len(merged) > max_len else merged


@dataclass
class AgentState(MessagesState):
    """
    State for the VM core analysis agent, extending MessagesState.
    """

    messages: Annotated[
        list[AnyMessage], add_and_trim
    ]  # TypedDict 继承只是合并 / 覆盖 字段声明，不保留父字段的 Annotated metadata
    question: Annotated[str, field(default="")]
    vmcore_path: Annotated[str, field(default="")]
    vmlinux_path: Annotated[str, field(default="")]
    analysis_steps: Annotated[int, field(default=0)]
    max_analysis_steps: Annotated[int, field(default=20)]
    agent_answer: Annotated[str, field(default="")]
