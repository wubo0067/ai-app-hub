from typing import Annotated, TypedDict
import operator
import warnings

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change in a future version\..*",
    category=LangChainPendingDeprecationWarning,
)

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    vals: Annotated[list[str], operator.add]


def node_a(state):
    # 参数 question_a 会保存在 interrupt 对象中
    answer = interrupt("question_a")
    # node_a got answer: answer for question_a
    print("node_a got answer:", answer)
    return {"vals": [f"a:{answer}"]}


def node_b(state):
    answer = interrupt("question_b")
    print("node_b got answer:", answer)
    return {"vals": [f"b:{answer}"]}


graph = (
    StateGraph(State)
    .add_node("a", node_a)
    .add_node("b", node_b)
    .add_edge(START, "a")
    .add_edge(START, "b")
    .add_edge("a", END)
    .add_edge("b", END)
    .compile(checkpointer=InMemorySaver())
)

config = {"configurable": {"thread_id": "1"}}

# Step 1: invoke - both parallel nodes hit interrupt() and pause
interrupted_result = graph.invoke({"vals": []}, config)
print(interrupted_result)
"""
{
    'vals': [],
    '__interrupt__': [
        Interrupt(value='question_a', id='bd4f3183600f2c41dddafbf8f0f7be7b'),
        Interrupt(value='question_b', id='29963e3d3585f0cef025dd0f14323f55')
    ]
}
"""

if "__interrupt__" in interrupted_result:
    print("Interrupts received:")
    for interrupt_obj in interrupted_result["__interrupt__"]:
        # interrupt_obj.value 其实就是中断的提示
        print(f" - id: {interrupt_obj.id}, value: {interrupt_obj.value}")

# Step 2: resume all pending interrupts at once
# 给每个中断设置一个对应的 resume 值，这里我们简单地返回 "answer for {question}" 作为示例
resume_map = {
    i.id: f"answer for {i.value}" for i in interrupted_result["__interrupt__"]
}
# 得到 node_a 和 node_b 的结果
result = graph.invoke(Command(resume=resume_map), config)

print("Final state:", result)
# > Final state: {'vals': ['a:answer for question_a', 'b:answer for question_b']}
