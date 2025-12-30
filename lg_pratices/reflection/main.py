import os
import langchain
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from langgraph.graph import END, StateGraph, START
from langgraph.checkpoint.memory import InMemorySaver
from typing import TypedDict, Annotated
import asyncio

langchain.debug = True

# prompt 是个消息列表
prompt = ChatPromptTemplate.from_messages(
    [
        SystemMessage(
            content="You are an essay assistant tasked with writing excellent 1-paragraph essays."
            " Generate the best essay possible for the user's request."
            " If the user provides critique, respond with a revised version of your previous attempts.",
        ),
        # 定义一个 messages 的占位符，在实际调用时会被替换为具体的消息内容，这里用来放用户的输入和之前的对话内容
        # 这种设计可以基于完整的对话历史来生成更好的回复
        MessagesPlaceholder(variable_name="messages"),
    ]
)

llm = ChatOpenAI(
    api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
    temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
)

# 这里没有使用结构化输出
# generate = prompt | (lambda x: print(f"格式化后的 prompt: {x}") or x) | llm
generate = prompt | llm

reflection_prompt = ChatPromptTemplate.from_messages(
    [
        SystemMessage(
            content="You are a teacher grading an essay submission. Generate critique and recommendations for the user's submission."
            " Provide detailed recommendations, including requests for length, depth, style, etc."
        ),
        MessagesPlaceholder(variable_name="messages"),
    ]
)

reflect = reflection_prompt | llm


def step_execute():
    essay = ""
    request = HumanMessage(
        content="Write an essay on why the little prince is relevant in modern childhood"
    )

    for chunk in generate.stream({"messages": [request]}):
        # 指定 print 函数输出后的结束字符。
        # 流式输出效果：AI 生成的内容会像打字机一样逐块显示，而不是每块内容都换行
        # 保持内容连续性：所有生成的文本块会连成完整的一行显示
        # 用户体验更好：可以看到内容逐步生成的过程，而不是分散在多行
        print(chunk.content, end="")
        essay += chunk.content

    print("\n\nNow reflecting on the essay...\n")

    reflection = ""
    for chunk in reflect.stream({"messages": [HumanMessage(content=essay)]}):
        print(chunk.content, end="")
        reflection += chunk.content


class State(TypedDict):
    messages: Annotated[list, add_messages]


async def generate_node(state: State) -> State:
    print(f"---> generate node msg: {state['messages']}")
    res = await generate.ainvoke({"messages": state["messages"]})
    print(f"<--- generate node res: {res.content}")
    return {"messages": [AIMessage(content=res.content)]}


async def reflect_node(state: State) -> State:
    print(f"---> reflect node msg: {state['messages']}")

    cls_map = {"ai": HumanMessage, "human": AIMessage}
    translated = {
        "messages": [HumanMessage(content=state["messages"][0].content)]
        + [cls_map[msg.type](content=msg.content) for msg in state["messages"][1:]]
    }

    res = await reflect.ainvoke(translated)

    print(f"<--- reflect node res: {res.content}")
    # 我们将此输出视为对生成器的人类反馈。
    return {"messages": [HumanMessage(content=res.content)]}


def should_continue(state: State):
    if len(state["messages"]) > 4:
        # 迭代 3 轮结束
        return END
    return "reflect"


async def main():
    print("Hello from reflection!")
    # 单步拆解的执行
    # step_execute()

    builder = StateGraph(State)
    builder.add_node("generate", generate_node)
    builder.add_node("reflect", reflect_node)
    builder.add_edge(START, "generate")
    builder.add_conditional_edges("generate", should_continue, ["reflect", END])
    builder.add_edge("reflect", "generate")

    memory = InMemorySaver()
    graph = builder.compile(checkpointer=memory)
    # 线程标识：thread_id 作为唯一标识符，用于在检查点存储器中区分不同的执行流程
    # 状态追踪：InMemorySaver 使用这个 ID 来保存和检索特定线程的状态
    # 多实例支持：允许多个并发的图执行实例，各自维护独立的状态
    config = {"configurable": {"thread_id": "1"}}

    async for event in graph.astream(
        {
            "messages": [
                HumanMessage(
                    content="Generate an essay on the topicality of The Little Prince and its message in modern life, Approximately 100 words."
                )
            ],
        },
        config,
    ):
        # print("<--- Event:")
        # print(event)
        # print("---> End Event\n")
        pass

    state = graph.get_state(config)
    ChatPromptTemplate.from_messages(state.values["messages"]).pretty_print()


if __name__ == "__main__":
    # 先设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = "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" # fmt: skip

    asyncio.run(main())
