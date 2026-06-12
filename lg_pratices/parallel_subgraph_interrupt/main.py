import operator
from typing import Annotated
import uuid

from pydantic import BaseModel, Field
from langgraph.types import Command, Interrupt, interrupt, Send
from langgraph.graph import START, END, StateGraph
from langgraph.checkpoint.memory import InMemorySaver


class ChildState(BaseModel):
    """State model used by the child subgraph.

    Fields in this model are used by LangGraph as the typed state container:
    - `prompt` is required input for the interrupt call.
    - `human_input` stores the latest single user response.
    - `human_inputs` is an accumulated list across updates.
    """

    # Prompt that will be shown to the human when the graph pauses via interrupt.
    prompt: str = Field(..., description="What is going to be asked to the user?")
    # Latest response returned when execution resumes after interrupt.
    human_input: str | None = Field(None, description="What the human said")
    dolphin_input: str | None = Field(None, description="What the dolphin said")
    # Accumulated responses. `operator.add` means list values are concatenated
    # when multiple updates merge into this field.
    # 这是 LangGraph 的**同名字段自动提升（state promotion）**机制。
    human_inputs: Annotated[list[str], operator.add] = Field(
        default_factory=list, description="All of my messages"
    )


def get_human_input(state: ChildState):
    """Pause execution and wait for human input.

    `interrupt(...)` yields control to the caller/runtime and resumes when a
    value is provided. The resumed value is then written back to state updates.
    """

    # Ask the human using the prompt stored in current state.
    human_input = interrupt(state.prompt)
    print(f"Asking human with prompt: {state.prompt} -> got response: {human_input}")

    # Return partial state updates:
    # - `human_input` replaces the latest single value.
    # - `human_inputs` appends one new item to the aggregated list field.
    return dict(
        human_input=human_input,  # update child state
        human_inputs=[human_input],  # update parent state
    )


def get_dolphin_input(state: ChildState):
    dolphin_input = interrupt(state.prompt)
    print(
        f"Asking dolphin with prompt: {state.prompt} -> got response: {dolphin_input}"
    )

    return dict(
        dolphin_input=dolphin_input,
        human_inputs=[
            dolphin_input
        ],  # update parent state with dolphin input for testing purposes
    )


# --- PARENT GRAPH ---
class ParentState(BaseModel):
    prompts: list[str] = Field(
        ..., description="What is going to be asked to the user?"
    )
    human_inputs: Annotated[list[str], operator.add] = Field(
        default_factory=list, description="All of my messages"
    )


def assign_workers(state: ParentState):
    # Send(节点名，初始状态) 是 LangGraph 的扇出指令。当一个条件边函数返回 Send 列表时，
    # LangGraph 会并发启动多个目标节点实例，每个实例拥有独立的 ChildState。
    # Send 会根据 state.prompts 数量自动生成多个子图实例，每个实例的 ChildState.prompt 分别对应 state.prompts 中的一个元素。
    return [
        Send(
            "child_graph",
            dict(
                prompt=prompt,
            ),
        )
        for prompt in state.prompts  # 这是一个 列表推导式，整体返回的是一个 Send 对象的列表
    ]


def cleanup(state: ParentState):
    assert len(state.human_inputs) == len(state.prompts) * 2


def main():
    # Placeholder entrypoint for local script execution.
    print("Hello from parallel-subgraph-interrupt!")

    # Build a minimal child graph with one node:

    # START -> get_human_input -> END
    child_graph_builder = StateGraph(ChildState)
    # Add the node with the processing function, then connect it to START and END.
    child_graph_builder.add_node("get_human_input", get_human_input)
    child_graph_builder.add_node("get_dolphin_input", get_dolphin_input)
    child_graph_builder.add_edge(START, "get_human_input")
    child_graph_builder.add_edge(START, "get_dolphin_input")
    # child_graph_builder.add_edge("get_human_input", END)

    # Compile the declarative graph definition into an executable graph object.
    child_graph = child_graph_builder.compile()

    parent_graph_builder = StateGraph(ParentState)
    # 当 child_graph 作为父图的一个节点运行时，LangGraph 在子图执行完成后，
    # 会把子图输出的 state 更新与父图 state 做字段名匹配：
    parent_graph_builder.add_node("child_graph", child_graph)
    parent_graph_builder.add_node("cleanup", cleanup)

    parent_graph_builder.add_conditional_edges(START, assign_workers, ["child_graph"])
    parent_graph_builder.add_edge("child_graph", "cleanup")
    parent_graph_builder.add_edge("cleanup", END)
    # compile() 不是“执行图”，而是“生成可执行图 + 绑定运行时能力”的步骤。
    parent_graph = parent_graph_builder.compile(checkpointer=InMemorySaver())

    thread_config = dict(
        configurable=dict(
            thread_id=str(uuid.uuid4()),
        )
    )
    current_input = dict(
        prompts=["a", "b"],
    )

    invokes = 0
    events: dict[int, list[dict]] = {}
    while invokes < 10:
        # reset interrupt
        invokes += 1
        events[invokes] = []
        # current_interrupts 每轮都被重置
        current_interrupts: list[Interrupt] = []

        # start / resume the graph
        # current_input = dict(prompts=["a", "b"]) 是一个普通 Python dict。
        # 调用 parent_graph.stream(input=current_input, ...) 时，
        # LangGraph 把这个 dict 的每个 key 写入对应的 channel ——
        # 即 prompts channel 存入 ["a", "b"]，human_inputs channel 保持默认值 []。
        print(f"\nInvokes {invokes}: Starting graph with input:", current_input)

        for event in parent_graph.stream(
            input=current_input,
            config=thread_config,
            stream_mode="updates",  # 这里用的是 stream_mode="updates"，所以 stream 每次产出的是“本步增量更新事件”。
        ):
            events[invokes].append(event)
            # handle the interrupt
            if "__interrupt__" in event:
                # 收集 event 中所有的"__interrupt__"事件，这些事件是由子图中的 interrupt() 产生的。每个 interrupt 都会生成一个 Interrupt 对象，包含了中断的值和唯一 ID。
                current_interrupts.extend(event["__interrupt__"])
                print(f"Invokes {invokes}: Received interrupts:", current_interrupts)
                # assume that it breaks here, because it is an interrupt

        # get human input and resume
        if len(current_interrupts) > 0:
            # we resume one at a time to preserve original test behavior,
            # but we could also resume all at once if we wanted
            # with a single dict mapping of interrupt ids to resume values
            # resume 就是 interrupt 函数返回值
            # 第 2 轮 Command(resume={A.id: "Resume #1"}) 发出后，图从 checkpoint 恢复，stream 返回：
            # child A 被 resume，继续执行完成 → {"child_graph": {"human_inputs": ["Resume #1"]}}
            # child B 还没被 resume，再次执行到 interrupt() 挂起 → {"__interrupt__": (Interrupt(value="b", ...),)}
            # child B 在每一轮 stream 中都会重新跑到 interrupt() 那一行然后挂起，
            # 所以每轮只要 B 还没被 resume，stream 就会再次抛出它的中断事件。这也是为什么 current_interrupts 每轮重置后，仍然能从本轮 stream 里重新收集到 B 的中断。
            resume = {current_interrupts[0].id: f"Resume #{invokes}"}
            print(f"Invokes {invokes}: Resuming with:", resume)
            # 用 resume 的值来构造 Command 对象，传递给图的 stream 方法，触发图的恢复执行。
            current_input = Command(resume=resume)

        # not more human input required, must be completed
        else:
            break
    else:
        assert False, "Detected infinite loop"

    assert invokes == 5
    assert len(events) == 5


if __name__ == "__main__":
    main()
