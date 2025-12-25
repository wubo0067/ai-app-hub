import os
from langchain_openai import ChatOpenAI
from langchain.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import TypedDict, List, Tuple, Annotated, Literal, Union
from pydantic import BaseModel, Field
import operator

extract_dsl_prompt = ChatPromptTemplate.from_template(
    """You are a RHEL kernel engineer that extracts the DSL from a given markdown file.
The extracted entries are as follows:
1. Trigger symptoms (what indicates a hard lock panic)
2. Diagnostic steps in order
3. Key crash commands used (crash utility)
4. Decision rules (IF condition → next action)
5. Final conclusions / root causes

Return ONLY structured JSON following this schema.
Schema:
{{
  "triggers": List[str],  # List of trigger symptoms
  "diagnostic_steps": List[str],  # Ordered list of diagnostic steps
  "crash_commands": List[str],  # List of key crash commands used
  "decision_rules": List[str],  # List of decision rules in IF-THEN format
  "conclusions": List[str]  # List of final conclusions or root causes
}}

Extract the DSL from the following markdown content:
{markdown_content}
"""
)


class ExtractedDSL(BaseModel):
    """Extracted DSL from markdown"""

    triggers: List[str] = Field(description="List of trigger symptoms")
    diagnostic_steps: List[str] = Field(description="Ordered list of diagnostic steps")
    crash_commands: List[str] = Field(description="List of key crash commands used")
    decision_rules: List[str] = Field(
        description="List of decision rules in IF-THEN format"
    )
    conclusions: List[str] = Field(
        description="List of final conclusions or root causes"
    )


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
        ExtractedDSL, method="function_calling"
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
