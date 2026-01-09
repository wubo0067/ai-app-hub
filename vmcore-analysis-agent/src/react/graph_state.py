from dataclasses import dataclass, field
from langgraph.graph import MessagesState


@dataclass
class AgentState(MessagesState):
    """
    State for the VM core analysis agent, extending MessagesState.
    """

    question: str = ""
    vmcore_path: str = ""
    vmlinux_path: str = ""
    analysis_steps: int = 0
    max_analysis_steps: int = 20
    agent_answer: str = ""
