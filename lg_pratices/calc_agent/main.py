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


def agent_main():
    # 创建状态图构建器
    agent_builder = StateGraph(MessagesState)

    # 添加节点到状态图
    agent_builder.add_node("llm_call", llm_call)
    agent_builder.add_node("tool_node", tool_node)

    # 添加决策逻辑以决定工作流的路径
    agent_builder.add_edge(START, "llm_call")
    agent_builder.add_conditional_edges(
        "llm_call",
        should_continue,
        ["tool_node", END],
    )
    agent_builder.add_edge("tool_node", "llm_call")

    agent = agent_builder.compile()

    # 替换原来的绘图逻辑
    try:
        # 获取 Mermaid 文本源码
        mermaid_code = agent.get_graph(xray=True).draw_mermaid()
        logger.info("\n--- Mermaid Graph Code ---")
        logger.info(mermaid_code)
        logger.info("---------------------------\n")
        logger.info("请将上方代码复制到 https://mermaid.live/ 查看流程图")
    except Exception as e:
        logger.error(f"获取 Mermaid 代码失败：{e}")

    messages = [
        HumanMessage(
            content="Add 2 and 4, multiply the result by 5, and then divide by 7. Give me the final value."
        )
    ]
    messages = agent.invoke(
        {"messages": messages}, config={"callbacks": [logging_callback_handler]}
    )
    for m in messages["messages"]:
        m.pretty_print()


if __name__ == "__main__":
    # 配置日志
    setup_logger(level="DEBUG", log_file="agent.log")

    agent_main()
