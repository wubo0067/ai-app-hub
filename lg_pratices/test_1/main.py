from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict


class State(TypedDict):
    text: str


def node_a(state: State) -> dict:
    return {"text": state["text"] + "A"}


def node_b(state: State) -> dict:
    return {"text": state["text"] + "B"}


def main():
    graph = StateGraph(State)
    graph.add_node("node_a", node_a)
    graph.add_node("node_b", node_b)
    graph.add_edge(START, "node_a")
    graph.add_edge("node_a", "node_b")

    print(graph.compile().invoke({"text": ""}))


if __name__ == "__main__":
    main()
