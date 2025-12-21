from typing import Literal
from state import MessagesState
from langchain_core.messages import AIMessage


def should_continue(state: MessagesState) -> Literal["tool_node", "__end__"]:

    # 获取 list[AnyMessage] 类型的消息列表
    messages = state["messages"]

    # 该函数首先通过 state["messages"][-1] 获取对话历史中的最后一条消息。
    # 这条消息通常是由 LLM 节点生成的 AIMessage
    last_message = messages[-1]

    # 函数通过检查该消息的 tool_calls 属性来判断模型的意图：如果该属性非空，
    # 意味着模型认为当前问题需要调用外部工具（如计算器或搜索 API）来获取更多信息，
    # 此时函数返回 "tool_node"，引导工作流进入工具执行阶段。
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tool_node"

    # 如果 last_message.tool_calls 为空，则表明模型已经生成了最终的文本答复，不再需要执行任何工具。
    # 在这种情况下，函数返回 "__end__"，这是一个 LangGraph 的内置常量，用于通知状态机停止迭代并结束整个工作流。

    # Otherwise, we stop (reply to the user)
    # 这种模式使得 Agent 能够进行多轮推理：模型调用工具 -> 得到结果 -> 再次回到模型 -> 模型决定是否还需要更多工具，
    # 直到最终输出答案。使用 Literal 类型提示则确保了返回值的类型安全，防止因拼写错误导致路由到不存在的节点。
    return "__end__"
