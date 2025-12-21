from langgraph.graph import StateGraph, START, END
from state import MessagesState
from tool_node import tool_node
from llm_node import llm_call
from logic import should_continue
from IPython.display import Image, display
from langchain.messages import HumanMessage
from langchain_core.runnables.graph import MermaidDrawMethod
from logging_callback import logging_callback_handler
from log import setup_logger, logger


def build_agent():
    """构建 agent 的函数"""
    # StateGraph 用于创建有状态的工作流图
    # State = 流程中传递的数据
    agent_builder = StateGraph(MessagesState)

    # 添加节点到状态图，Node = 流程中的步骤（如果调用 LLM, 执行工具)
    agent_builder.add_node("llm_call", llm_call)  # type: ignore
    agent_builder.add_node("tool_node", tool_node)  # type: ignore

    # 添加决策逻辑以决定工作流的路径，Edge = 步骤之间的连接线
    agent_builder.add_edge(START, "llm_call")
    # 动态路径，条件边
    agent_builder.add_conditional_edges(
        "llm_call",  # 从 llm_call 节点开始
        should_continue,  # 使用 should_continue 函数决定路径
        ["tool_node", END],  # 可能的目标节点，tool_node 或 END
    )
    agent_builder.add_edge("tool_node", "llm_call")

    return agent_builder.compile()


def agent_main():
    # 构建 agent
    agent = build_agent()

    # 使用 draw_mermaid_png 绘制状态图
    try:
        # 方法 1：显示图片（适用于 Jupyter Notebook）
        display(Image(agent.get_graph(xray=True).draw_mermaid_png()))

        # 方法 2：保存为文件
        png_data = agent.get_graph(xray=True).draw_mermaid_png(
            output_file_path="agent_graph.png",
            draw_method=MermaidDrawMethod.API,  # 或 MermaidDrawMethod.PYPPETEER
        )
        logger.info("状态图已保存到 agent_graph.png")
    except Exception as e:
        logger.error(f"绘制状态图失败：{e}")
        logger.info("如果使用 API 方法失败，可以尝试获取 Mermaid 代码：")
        mermaid_code = agent.get_graph(xray=True).draw_mermaid()
        logger.info(f"\n{mermaid_code}")

    messages = [
        # 人的消息开始
        HumanMessage(
            content="Add 2 and 4, multiply the result by 5, and then divide by 7. Give me the final value."
        )
    ]
    messages = agent.invoke(
        {"messages": messages}, config={"callbacks": [logging_callback_handler]}  # type: ignore
    )
    for m in messages["messages"]:
        m.pretty_print()


# langgraph-cli 入口函数
def get_app():
    """langgraph dev 会调用这个函数来获取 graph"""
    return build_agent()


if __name__ == "__main__":
    # 配置日志
    setup_logger(level="DEBUG", log_file="agent.log")

    agent_main()
