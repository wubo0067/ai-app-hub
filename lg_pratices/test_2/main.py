from langgraph.graph import StateGraph, START, END
from state import MessagesState
from tool_node import tool_node
from llm_node import llm_call
from logic import should_continue
from IPython.display import Image, display
from langchain.messages import HumanMessage
from langchain_core.runnables.graph import MermaidDrawMethod


def main():
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
        print("\n--- Mermaid Graph Code ---")
        print(mermaid_code)
        print("---------------------------\n")
        print("请将上方代码复制到 https://mermaid.live/ 查看流程图")
    except Exception as e:
        print(f"获取 Mermaid 代码失败：{e}")

    messages = [HumanMessage(content="Add 3 and 4.")]
    messages = agent.invoke({"messages": messages})
    for m in messages["messages"]:
        m.pretty_print()


if __name__ == "__main__":
    main()
