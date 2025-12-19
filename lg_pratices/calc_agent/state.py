from langchain.messages import AnyMessage
from typing_extensions import TypedDict, Annotated
import operator


# 定义状态结构，这是个 schema
class MessagesState(TypedDict):
    """
    定义消息状态的类型字典结构。

    该类用于定义一个包含消息列表和 LLM 调用次数的状态结构，
    主要用于跟踪对话过程中的消息历史和模型调用统计。

    属性：
        messages: 消息列表，使用 Annotated 注解指定 list[AnyMessage] 类型，
                 并通过 operator.add 实现列表的合并操作
        llm_calls: 整数类型，记录 LLM 调用次数
    """

    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
