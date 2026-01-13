from dataclasses import dataclass, field
from typing import Annotated, Optional, TypedDict
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


class AgentError(TypedDict):
    message: str
    node: str
    is_error: bool


@dataclass
class AgentState(MessagesState):
    """
    State for the VM core analysis agent, extending MessagesState.
    """

    default_crash_cmd: Annotated[
        list[str],
        field(
            default=[
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
        ),
    ]
    messages: Annotated[
        list[AnyMessage], add_and_trim
    ]  # TypedDict 继承只是合并 / 覆盖 字段声明，不保留父字段的 Annotated metadata
    vmcore_path: Annotated[str, field(default="")]
    vmlinux_path: Annotated[str, field(default="")]
    analysis_steps: Annotated[int, field(default=0)]
    max_analysis_steps: Annotated[int, field(default=20)]
    agent_answer: Annotated[str, field(default="")]

    error: Annotated[Optional[AgentError], field(default=None)]
