from pydantic import BaseModel, Field
from typing import List, Optional


class DiagnosticBranch(BaseModel):
    """Diagnostic rule: Condition -> Action."""

    trigger: str = Field(
        ...,
        description="Concise symptom description (max 18 words). Must be SPECIFIC enough to distinguish from similar branches.",
    )
    action: str = Field(
        ...,
        description="Complete command template with placeholders. Use {{cpu}}, {{addr}}, {{pid}}, {{offset}}, {{modname}} etc. for variables.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Parameter sources (max 15 words). Example: 'cpu: from lockup message, addr: from RDI register'.",
    )
    why: str = Field(
        ...,
        description="Diagnostic purpose (max 20 words). Explain WHAT this step checks for.",
    )
    expect: str = Field(
        ...,
        description="Expected output pattern (max 20 words). Be specific about what to look for.",
    )
    is_end: bool = Field(
        False,
        description="True if this is a definitive diagnostic conclusion. For root cause analysis, set to true.",
    )


class DiagnosticKnowledgeBase(BaseModel):
    """Comprehensive diagnostic knowledge base for Hard LOCKUP vmcore analysis."""

    summary: str = Field(
        "Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios",
        description="Fixed summary for the diagnostic knowledge base.",
    )
    init_cmds: List[str] = Field(
        ...,
        description="Common initial diagnostic commands (2-3 commands). Should be generic commands for initial investigation.",
    )
    matrix: List[DiagnosticBranch] = Field(
        ...,
        description="Complete diagnostic decision matrix covering all workflow thoughts from DSL files.",
    )
