from langchain.messages import SystemMessage
from tools import model_with_tools


# The model node is used to call the LLM and decide whether to call a tool or not.


def llm_call(state: dict) -> dict:
    """LLM decides whether to call a tool or not"""

    return {
        "messages": [
            model_with_tools.invoke(
                [
                    # 是 LangChain 框架中用于定义“系统提示词”（System Prompt）的核心类。
                    # 它的主要作用是为大语言模型（LLM）设定初始角色、行为准则和任务背景。在与聊天模型交互时，
                    # 系统消息通常作为消息序列的第一条内容发送，用于在模型处理用户具体请求之前，先确立其“人格”或工作模式。
                    SystemMessage(
                        content="You are a helpful assistant tasked with performing arithmetic on a set of inputs."
                    )
                ]
                + state["messages"]
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }
