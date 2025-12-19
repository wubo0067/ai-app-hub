from langchain.messages import ToolMessage
from tools import tools_by_name
from log import logger

# 这段代码定义了一个名为 tool_node 的函数，它是 LangGraph 状态机中的一个核心节点，专门负责执行大语言模型（LLM）请求的工具调用。
# 在 AI Agent 的工作流中，当模型决定不直接回答问题而是调用外部工具（如计算器、搜索 API 等）时，该函数会被触发。
# 它通过访问 state["messages"][-1] 获取对话历史中的最后一条消息，这条消息通常是一个包含 tool_calls 列表的 AIMessage。


def tool_node(state: dict):

    result = []
    for tool_call in state["messages"][-1].tool_calls:
        # 根据工具调用的名称从 tools_by_name 字典中检索相应的工具对象
        tool = tools_by_name[tool_call["name"]]

        # 调用该工具并传入所需的参数
        logger.info(
            f"Invoking tool: {tool_call['name']} with args: {tool_call['args']}"
        )
        observation = tool.invoke(tool_call["args"])

        # 将工具调用的结果封装为 ToolMessage 并添加到结果列表中
        logger.info(f"Tool result: {observation}")
        result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
    return {"messages": result}
