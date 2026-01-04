import os
import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import TypedDict, List, Optional
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END


# --- 优化后的精简版 Prompt ---
update_knowledge_prompt = ChatPromptTemplate.from_template(
    """Role: Linux Kernel Diagnostic Architect (Hard LOCKUP specialist).
    Task: Merge NEW DSL case into CURRENT Knowledge Base (KB).

    # CORE RULES
    1. Init Cmds: Unique merge. Prioritize: bt -a, sys, log | grep -i lockup, ps -S.
    2. Matrix: Deduplicate semantically. Add new branches only if logic differs.
    3. Templating: NO hex addresses or specific CPUs. Use {{addr}}, {{cpu}}, {{pid}}, {{offset}}.
    4. Style: Use telegraphic, technical English. No filler words (e.g., "Check if", "This indicates").

    # FIELD CONSTRAINTS (STRICT)
    - trigger: Max 12 words. Specific symptom.
    - arg_hints: Max 10 words. Format: "key: source".
    - why: Max 15 words. Technical essence.
    - expect: Max 15 words. Key pattern/string.

    # EXAMPLES
    - Abstraction: "action": "spinlock_t {{lock_addr}}", "arg_hints": "lock_addr: from RDI or stack"
    - Conciseness: "trigger": "Stack contains native_queued_spin_lock_slowpath (contention)"
    - Conciseness: "why": "Recursive deadlock: current task already holds the lock"

    # INPUTS
    - Current KB: {current_knowledge_json}
    - New DSL: {new_dsl_content}

    # OUTPUT
    - Strictly follow JSON Schema.
    - Use single braces in JSON output (e.g., {{addr}}).
    - Schema: {schema}
    """
)


# 1. 简化后的 Branch 定义
class DiagnosticBranch(BaseModel):
    """
    Simplified diagnostic rule: Condition -> Action.
    """

    trigger: str = Field(
        ...,
        description="Concise symptom (max 12 words). E.g., 'Watchdog detected hard LOCKUP'.",
    )
    action: str = Field(
        ...,
        description="Command template with placeholders, e.g., 'struct spinlock {addr}'.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Short syntax (max 10 words). E.g., 'addr: from RDI register'.",
    )
    why: str = Field(
        ...,
        description="Brief technical reason (max 15 words).",
    )
    expect: str = Field(
        ...,
        description="Key pattern to look for (max 15 words).",
    )
    is_end: bool = Field(
        False,
        description="True if this is a root cause conclusion.",
    )


# 2. 简化后的 Dict 定义
class DiagnosticDict(BaseModel):
    """
    Compact diagnostic knowledge base for Hard LOCKUP scenarios.
    """

    summary: str = "diagnostic matrix for Linux kernel Hard LOCKUP scenarios"
    init_cmds: List[str] = Field(
        ...,
        description="Common initial commands for Hard LOCKUP diagnosis (e.g., 'bt -a', 'sys', 'log | grep -i lockup', 'ps -S').",
    )
    matrix: List[DiagnosticBranch] = Field(
        ..., description="The decision matrix for diagnosing Hard LOCKUP root causes."
    )


# 3. LangGraph State 定义
class AgentState(TypedDict):
    remaining_files: List[str]
    current_file_content: Optional[str]
    current_knowledge: Optional[DiagnosticDict]
    has_error: bool  # 添加错误标志位


# 5. Graph 节点函数
def load_next_file(state: AgentState):
    """从列表中取出一个文件并读取内容"""
    remaining = state["remaining_files"]
    if not remaining:
        return {"current_file_content": None}

    file_path = remaining[0]
    print(f"\n正在读取文件：{file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"current_file_content": content, "remaining_files": remaining[1:]}
    except Exception as e:
        print(f"读取文件失败：{e}")
        return {
            "current_file_content": "{}",
            "remaining_files": remaining[1:],
        }


def integrate_knowledge(state: AgentState):
    """调用 LLM 将新文件内容合并到知识库"""
    content = state["current_file_content"]
    current_kb = state["current_knowledge"]

    if current_kb is None:
        current_kb_json = "This is the first case. Initialize the knowledge base."
    else:
        current_kb_json = current_kb.model_dump_json()

    print("正在调用 LLM 进行知识整合...")

    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
    )

    dd_schema = DiagnosticDict.model_json_schema()

    chain = update_knowledge_prompt | llm.with_structured_output(
        DiagnosticDict, method="json_mode"
    )

    # 打印 current_kb_json 和 content 的长度以调试
    print(f"Current KB JSON length: {len(current_kb_json)}")
    print(f"New DSL Content length: {len(content) if content else 0}")

    try:
        new_kb = chain.invoke(
            {
                "current_knowledge_json": current_kb_json,
                "new_dsl_content": content,
                "schema": json.dumps(dd_schema, indent=2),
            }
        )
        return {"current_knowledge": new_kb, "has_error": False}
    except Exception as e:
        print(f"❌ LLM 调用失败：{e}")
        print(f"❌ 停止处理，保存当前进度...")
        # 返回错误标志，终止循环
        return {"current_knowledge": current_kb, "has_error": True}


def should_continue(state: AgentState):
    """判断是否还有文件需要处理"""
    # 检查是否有错误发生
    if state.get("has_error", False):
        print("⚠️ 检测到错误，停止处理")
        return "end"

    # 检查是否还有文件
    if state["current_file_content"] is None:
        return "end"

    return "integrate"


class LoggingCallbackHandler(BaseCallbackHandler):
    def on_chain_error(self, error, **kwargs):
        """监听 Chain 错误"""
        tags = kwargs.get("tags", [])
        metadata = kwargs.get("metadata", {})

        langgraph_node = metadata.get("langgraph_node")

        print(f"\n{'='*60}")
        print(f"🔴 节点错误")
        print(f"{'='*60}")
        print(f"错误类型：{type(error).__name__}")
        print(f"错误信息：{str(error)[:200]}")
        print()


# 6. 主函数
def main():
    os.makedirs("dsl", exist_ok=True)

    dsl_list = [
        "dsl/3379041.json",
        "dsl/3870151.json",
        "dsl/6348992.json",
        "dsl/6988986.json",
        "dsl/7019939.json",
        "dsl/7041099.json",
        "dsl/7086442.json",
    ]

    print(f"准备处理 {len(dsl_list)} 个 DSL 文件。")

    # 构建 LangGraph
    workflow = StateGraph(AgentState)

    workflow.add_node("load_file", load_next_file)
    workflow.add_node("integrate", integrate_knowledge)

    workflow.set_entry_point("load_file")

    workflow.add_conditional_edges(
        "load_file", should_continue, {"integrate": "integrate", "end": END}
    )

    workflow.add_edge("integrate", "load_file")

    app = workflow.compile()

    initial_state: AgentState = {
        "remaining_files": dsl_list,
        "current_knowledge": None,
        "current_file_content": "",
        "has_error": False,  # 初始化错误标志
    }

    print("\n开始执行 LangGraph 工作流...")

    final_state = None
    for event in app.stream(
        initial_state, config={"callbacks": [LoggingCallbackHandler()]}
    ):
        for node_name, node_output in event.items():
            print(f"\n{'='*60}")
            print(f"📊 节点 '{node_name}' 执行完成")
            print(f"{'='*60}")
            # 打印节点输出的摘要信息
            if isinstance(node_output, dict):
                if "has_error" in node_output and node_output["has_error"]:
                    print(f"⚠️  检测到错误，即将停止")
                if "remaining_files" in node_output:
                    remaining = node_output.get("remaining_files", [])
                    print(f"📁 剩余文件：{len(remaining)}")
                if "current_knowledge" in node_output:
                    kb = node_output.get("current_knowledge")
                    if kb and hasattr(kb, "matrix"):
                        print(f"📚 当前知识库条目：{len(kb.matrix)}")

            # 保存最终状态
            final_state = node_output
            print()

    final_kb = final_state.get("current_knowledge") if final_state else None

    if final_kb:
        print("\n整合完成！正在写入文件...")
        diagnostic_dict_json = final_kb.model_dump_json(indent=2)

        # 根据是否有错误，使用不同的文件名
        output_filename = (
            "integrate_diagnostic_knowledge_library_partial.json"
            if final_state and final_state.get("has_error")
            else "integrate_diagnostic_knowledge_library.json"
        )

        with open(
            os.path.join("dsl", output_filename),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(diagnostic_dict_json)
        print(f"文件已保存至：dsl/{output_filename}")
        print(f"最终 Summary: {final_kb.summary}")
        print(f"最终 Matrix 条目数：{len(final_kb.matrix)}")

        if final_state and final_state.get("has_error"):
            print(f"⚠️ 警告：由于错误，仅处理了部分文件")
    else:
        print("错误：未能生成知识库。")


if __name__ == "__main__":
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = "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"  # fmt: skip

    main()
