import os
from langchain_openai import ChatOpenAI
from langchain.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import TypedDict, List, Tuple, Annotated, Literal, Union, Optional
from pydantic import BaseModel, Field
import operator

extract_dsl_prompt = ChatPromptTemplate.from_template(
    """You are a senior Linux kernel diagnostics expert...

    # Task
    Analyze the provided Markdown document to extract diagnostic logic, specific 'crash' tool commands, ,
    and data patterns. Your goal is to generate a structured JSON that a ReAct Agent can use to perform step-by-step ,
    troubleshooting.

    # STRICT LANGUAGE RULE
    - The entire JSON response MUST be in English.
    - Translate all Chinese analysis from the source document into professional technical English.

    # Content Guidelines
    1. **Thought**: Explain the technical reasoning. Why check this specific data? What kernel subsystem is involved?
    2. **Action**: Must be a valid 'crash' utility command (e.g., 'bt', 'struct', 'rd', 'irq').
    3. **Observation**: Describe the specific abnormal indicators (e.g., 'owner field is NULL', 'value exceeds 0xFFFFF').
    4. **Symptoms**: List specific keywords or stack trace symbols that identify this issue.

    # Output Format Requirement
    - Output MUST be a valid JSON string.
    - DO NOT include markdown code blocks (e.g., no ```json).
    - Language: Please provide the content in **Chinese** (especially for 'thought' and 'scenario'), 、
        but keep technical terms and commands in English.

    # JSON Schema:
    {{
    "type": "object",
    "properties": {{
        "scenario": {{ "type": "string", "description": "Problem classification" }},
        "symptoms": {{ "type": "array", "items": {{ "type": "string" }}, "description": "Key patterns like 'panic', 'divide error', or specific function names" }},
        "workflow": {{
        "type": "array",
        "items": {{
            "type": "object",
            "properties": {{
            "step_number": {{ "type": "integer" }},
            "thought": {{ "type": "string", "description": "Logic/Reasoning in Chinese" }},
            "action": {{ "type": "string", "description": "The exact crash command to run" }},
            "observation": {{ "type": "string", "description": "What abnormal result confirms the theory" }}
            }},
            "required": ["step_number", "thought", "action", "observation"]
        }}
        }},
        "root_cause_analysis": {{
        "type": "array",
        "items": {{
            "type": "object",
            "properties": {{
            "phenomenon": {{ "type": "string" }},
            "possible_cause": {{ "type": "string" }}
            }}
        }}
        }}
    }},
    "required": ["scenario", "symptoms", "workflow", "root_cause_analysis"]
    }}

    # Input Content:
    {markdown_content}
"""
)


class DiagnosticStep(BaseModel):
    """
    Represents an atomic diagnostic step for a ReAct Agent.
    """

    step_number: int = Field(
        ..., description="The sequence number of the execution step."
    )
    thought: str = Field(
        ...,
        description="The expert's logical reasoning (in Chinese as requested) explaining why this action is performed.",
    )
    action: str = Field(
        ...,
        description="The specific command to execute in the crash utility, e.g., 'bt -a' or 'struct spinlock_t <address>'.",
    )
    observation: str = Field(
        ...,
        description="The expected abnormal indicators or key metrics to look for in the action output.",
    )


class RootCauseMapping(BaseModel):
    """
    The mapping between observed phenomena and their potential root causes.
    """

    phenomenon: str = Field(
        ...,
        description="A key abnormal phenomenon observed during the diagnostic process.",
    )
    possible_cause: str = Field(
        ...,
        description="The underlying logic or potential root cause corresponding to the phenomenon.",
    )


class DiagnosisDSL(BaseModel):
    """
    A structured diagnostic methodology model for vmcore analysis.
    Designed to transform unstructured documents into a machine-executable DSL for ReAct Agents.
    """

    scenario: str = Field(
        ...,
        description="The specific problem scenario this diagnostic flow applies to (e.g., 'Spinlock Deadlock').",
    )
    symptoms: List[str] = Field(
        ...,
        description="A list of key symptoms that trigger this diagnostic flow (e.g., specific panic keywords or symbols).",
    )

    workflow: List[DiagnosticStep] = Field(
        ...,
        description="The structured investigation workflow. The Agent will execute these steps sequentially.",
    )

    root_cause_analysis: List[RootCauseMapping] = Field(
        ...,
        description="The mapping table between extracted phenomena and their causes from the document.",
    )

    data_patterns: Optional[List[str]] = Field(
        None,
        description="Specific numerical patterns mentioned in the document, such as flag bits, offsets, or timeout thresholds.",
    )

    class Config:
        populate_by_name = True


def main():
    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,  # temperature 的作用是控制生成文本的随机性，值越低，生成的文本越确定和一致
    )

    # 打开 md 目录下下 7086442.md 文件，读取内容
    with open("md/7086442.md", "r", encoding="utf-8") as f:
        markdown_content = f.read()

    extract_dsl = extract_dsl_prompt | llm.with_structured_output(
        DiagnosisDSL, method="function_calling"
    )

    response = extract_dsl.invoke({"markdown_content": markdown_content})
    print("Extracted DSL:")
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    # 先设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = "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" # fmt: skip

    main()
