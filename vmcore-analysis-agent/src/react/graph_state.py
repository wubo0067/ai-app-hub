from dataclasses import dataclass, field
from typing import Annotated, Optional, TypedDict, Union, cast
from langgraph.graph import add_messages
from langgraph.graph.message import AnyMessage
from .src.utils.logging import logger


def add_and_trim_messages(
    left: list[AnyMessage], right: Union[list[AnyMessage], AnyMessage]
) -> list[AnyMessage]:
    """
    基于 LangGraph 的 add_messages 进行合并，但增加了窗口限制。
    """
    # 1. 先使用标准 add_messages 处理 ID 去重和合并
    merged = cast(list[AnyMessage], add_messages(left, right))  # type: ignore

    # 2. 保留最近的 N 条消息
    # 注意：实际场景中可能需要保留 SystemMessage (第一条)，这里做简单切片演示
    max_len = 20
    if len(merged) > max_len:
        logger.info(
            f"Trimming messages from {len(merged)} to {max_len} to maintain window size."
        )
        return merged[-max_len:]
    return merged


class AgentError(TypedDict):
    """
    Agent 执行过程中的错误信息。

    Attributes:
        message: 错误描述信息
        node: 发生错误的节点名称
        is_error: 是否为错误状态标记
    """

    message: str
    node: str
    is_error: bool


class AgentState(TypedDict):
    """
    VMCore 分析 Agent 的状态定义，扩展自 MessagesState。

    包含 vmcore 分析所需的路径、命令配置、分析步数控制及错误状态等信息。
    """

    # 核心路径配置
    vmcore_path: str
    vmlinux_path: str

    # 消息历史 (带自动修剪)
    messages: Annotated[list[AnyMessage], add_and_trim_messages]

    # 分析控制
    analysis_steps: int
    max_analysis_steps: int

    # 分析结果
    agent_answer: str
    # 错误状态
    error: Optional[AgentError]
