import os
from langchain.tools import tool
from langchain_openai import ChatOpenAI


# Define tools
@tool
def multiply(a: int, b: int) -> int:
    """Multiply `a` and `b`.

    Args:
        a: First int
        b: Second int
    """
    return a * b


@tool
def add(a: int, b: int) -> int:
    """Adds `a` and `b`.

    Args:
        a: First int
        b: Second int
    """
    return a + b


@tool
def divide(a: int, b: int) -> float:
    """Divide `a` and `b`.

    Args:
        a: First int
        b: Second int
    """
    return a / b


tools = [add, multiply, divide]
tools_by_name = {tool.name: tool for tool in tools}


llm = ChatOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)
# 是将一组工具（如函数、类或特定的工具对象）与大语言模型（LLM）实例进行绑定
# 这是构建 Agent 或支持工具调用（Tool Calling）应用的关键步骤。
# 通过这一调用，模型能够感知到这些工具的存在，并了解它们的名称、描述以及所需的参数结构，
# 从而在处理用户输入时决定是否需要调用其中的某个工具来获取外部信息或执行特定操作。
model_with_tools = llm.bind_tools(tools)
