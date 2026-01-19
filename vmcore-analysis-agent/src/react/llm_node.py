from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    command_name: str = Field(
        ..., description="The crash command (e.g., 'dis', 'rd', 'struct')."
    )
    arguments: List[str] = Field(default_factory=list, description="Command arguments.")


class VMCoreAnalysisStep(BaseModel):
    step_id: int = Field(..., description="Current step sequence number.")

    analysis_path: Literal["knowledge_base", "general_debugging"] = Field(
        ...,
        description="Specify if you are following a DKB pattern or using general kernel debugging experts logic.",
    )

    reasoning: str = Field(
        ...,
        description="Detailed thought process. If general_debugging, explain which kernel subsystem (Memory, FS, Scheduler) you are investigating.",
    )

    knowledge_base_hit: Optional[str] = Field(
        None, description="The 'trigger' name from DKB (if applicable)."
    )

    action: Optional[ToolCall] = Field(
        None,
        description="The next command to run. Should be None if is_conclusive is True.",
    )

    is_conclusive: bool = Field(False)
    final_diagnosis: Optional[str] = Field(
        None, description="Detailed final root cause and evidence."
    )
