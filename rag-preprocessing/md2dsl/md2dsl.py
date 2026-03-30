import os
import json
from langchain_openai import ChatOpenAI
from langchain.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import TypedDict, List, Tuple, Annotated, Literal, Union, Optional
from langchain_core.documents import Document
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field, ConfigDict
import operator
import re

extract_dsl_prompt = ChatPromptTemplate.from_template(
    """You are a senior Linux kernel diagnostics expert extracting diagnostic logic from documentation.

    # Task
    Extract ALL diagnostic commands, steps, and metadata from markdown into structured JSON.

    # Extraction Rules

    ## 1. Workflow Steps
    Extract ALL crash commands in order they appear:

    **For each command:**
    - **step_number**: Sequential (1, 2, 3...)
    - **thought**: Brief reason (5-15 words from context)
    - **action**: EXACT command (preserve flags, pipes, awk, grep)
    - **observation**: Key indicator to find (5-12 words)

    **Deduplication:** NEVER extract same command twice (e.g., "bt" only once). Exception: Different args = different commands ("bt" ≠ "bt -c 1" ≠ "bt -a").

    **Command Patterns:**
    - Prefixed: `crash> bt -a`
    - Inline: "run `spinlock_t <addr>`"
        - **VALIDATION RULE (MANDATORY):** Before extracting ANY command, check first character:
            * If starts with `<`, `>`, `|`, `&` → SKIP (shell operator, not crash command)
            * If first word NOT in whitelist → SKIP
            * ONLY extract if first word matches whitelist exactly
        - **ACTION FORMAT RULE:**
            * For lines starting with `crash>`, STRIP the `crash>` prefix and leading spaces; the remaining text MUST start with a whitelist keyword — otherwise SKIP the line.
            * For inline mentions (e.g., "run `spinlock_t <addr>`"), ensure the first token is a whitelist keyword or `struct` for structure ops — otherwise SKIP.
            * Never include the literal `crash>` prefix in the `action` field.

        - **STRUCT RULE (CRITICAL):** Use `struct` ONLY for C structure/field inspection:
            * `spinlock_t <addr>` → `struct spinlock_t <addr>`
            * `thread_info.flags <addr>` → `struct thread_info.flags <addr>`
            * Pattern `type.field` or `type <addr>` → use `struct`.
            * Disambiguation: If the first token is a whitelisted crash subcommand other than `struct` (e.g., waitq, task, timer, list, kmem), TREAT IT AS A COMMAND and DO NOT prepend `struct`.
            * Heuristic: Treat `X <addr>` as a C type only when `X` is NOT in the whitelist (except `struct` itself).
            * Auto-normalize: If the first token is NOT in the whitelist and matches a C identifier or `identifier.field(,field)*`, PREPEND `struct ` automatically so that the action becomes valid (e.g., `thread_info.flags <addr>` → `struct thread_info.flags <addr>`).

        - **WHITELIST (STRICT):** ONLY extract commands starting with: [alias, ascii, bpf, bt, btop, dev, dis, eval, exit, extend, files, foreach, fuser, gdb, help, ipcs, irq, kmem, list, log, mach, mod, mount, net, p, ps, pte, ptob, ptov, rd, repeat, runq, sbitmapq, search, set, sig, struct, swap, sym, sys, task, timer, tree, union, vm, vtop, waitq, whatis, wr]
            - Common subset (preferred): [bt, p, struct, foreach, dis, task, ps, rd]
            - Optional first-token regex (if supported): `^(alias|ascii|bpf|bt|btop|dev|dis|eval|exit|extend|files|foreach|fuser|gdb|help|ipcs|irq|kmem|list|log|mach|mod|mount|net|p|ps|pte|ptob|ptov|rd|repeat|runq|sbitmapq|search|set|sig|struct|swap|sym|sys|task|timer|tree|union|vm|vtop|waitq|whatis|wr)$`

    - **FORBIDDEN:** NEVER extract:
      * Extension commands: dmshow, scsishow, lsblk, lsmod
      * Shell redirects/operators: Any command starting with `<`, `>`, `|`, `&`, `&&`, `||`
      * Shell utilities: cat, echo, sed (unless after | from crash command)
      * ANY command where first word is NOT in whitelist

    - **Pipes:** Keep ENTIRE pipe chain intact ONLY when attached to valid crash command (e.g., "bt | grep" ✓, "< file | awk" ✗)

    **Examples (Light):**
    ✓ "crash> bt -a | grep exception" → `{{"action": "bt -a | grep exception"}}` (first word "bt" is in whitelist)
    ✓ "crash> waitq ffff9a05dae122a8" → `{{"action": "waitq ffff9a05dae122a8"}}` ("waitq" is a crash command, do NOT add `struct`)
    ✗ "crash> struct waitq ffff9a05dae122a8" → DO NOT EXTRACT (invalid: `struct` applied to a crash subcommand)
    ✗ "< tjiffies | paste - - | awk '{{...}}'" → DO NOT EXTRACT (starts with shell redirect; not a crash command)

    ## 2. Symptoms
    Extract 2-3 specific symptoms from "Issue" or panic section:
    - Stack trace symbols (e.g., "RIP: _spin_lock_irqsave")
    - Error messages (e.g., "Watchdog detected hard LOCKUP")
    - Panic strings

    ## 3. Root Cause Analysis
    Extract AS-IS from "Root Cause" section (NO summarizing):
    - One entry per distinct cause/point
    - Keep original wording verbatim

    # Quality Checks
    - Step count ≥ crash command count in document
    - **Every action MUST start with a whitelist keyword** (validate: first word in [alias, ascii, bpf, bt, ...])
    - Reject patterns like `struct <command> ...` where `<command>` is a known crash subcommand (e.g., `struct waitq ...`).
    - **No action can start with shell operators** (validate: first char NOT in [<, >, |, &])
    - **Actions are ONLY taken from `crash>` lines or validated inline mentions** (apply ACTION FORMAT RULE strictly)
    - Preserve exact syntax (addresses, filters, pipes)
    - Root cause uses original document wording

    # Output JSON Schema:
    {schema}

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
        description="The expert's logical reasoning (in English) explaining why this action is performed.",
    )
    action: str = Field(
        ...,
        description="The command execute in the crash utility, e.g., 'bt -a' or 'struct spinlock_t <address>'",
    )
    observation: str = Field(
        ...,
        description="The expected abnormal indicators or key metrics to look for in the action output.",
    )


class DiagnosisDSL(BaseModel):
    """
    A structured diagnostic methodology model for vmcore analysis.
    Designed to transform unstructured documents into a machine-executable DSL for ReAct Agents.
    """

    os: str = Field(
        ...,
        description="The operating system or kernel version this diagnostic flow is designed for. ex: 'Red Hat Enterprise Linux 8'",
    )
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

    root_cause_analysis: List[str] = Field(
        ...,
        description="A list of root cause analysis statements extracted from the document.",
    )

    model_config = ConfigDict(populate_by_name=True)  # 替换 Config 类


class LoggingCallbackHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        print(f"\n=== LLM 调用开始 ===")
        print(f"模型：{serialized.get('name', 'unknown')}")

    def on_llm_end(self, response, **kwargs):
        print(f"=== LLM 调用结束 ===")
        # 打印响应的详细信息
        if hasattr(response, "generations"):
            print(f"生成数：{len(response.generations)}")
            if response.generations and response.generations[0]:
                first_gen = response.generations[0][0]
                print(f"消息类型：{type(first_gen.message)}")
                if hasattr(first_gen.message, "content"):
                    content_preview = str(first_gen.message.content)[:200]
                    print(f"内容预览：{content_preview}...")
                if hasattr(first_gen.message, "additional_kwargs"):
                    print(f"额外参数：{first_gen.message.additional_kwargs.keys()}")
        print(f"响应生成完成\n")

    def on_chain_start(self, serialized, inputs, **kwargs):
        print(f"\n=== Chain 开始执行 ===")
        # 安全地处理不同类型的 inputs
        if isinstance(inputs, dict):
            print(f"输入键：{list(inputs.keys())}")
        else:
            print(f"输入类型：{type(inputs)}")

    def on_chain_end(self, outputs, **kwargs):
        print(f"=== Chain 执行结束 ===")
        print(f"输出类型：{type(outputs)}")
        # 如果输出不是预期的类型，打印详细信息
        if outputs is None:
            print("警告：输出为 None")
        elif isinstance(outputs, dict):
            print(f"输出键：{list(outputs.keys())}")
        print()


def main():
    # 创建 dsl 目录（如果不存在）
    os.makedirs("dsl", exist_ok=True)

    llm = ChatOpenAI(
        api_key=os.environ.get("DEEPSEEK_API_KEY"),  # type: ignore
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,  # 数据抽取/分析	1.0
    )

    # grep -ril "soft lockup" . \
    #   | xargs grep -L "hard lockup" \
    #   | xargs -I{} stat -c "%s %n" {} \
    #   | sort -nr \
    #   | head -n 6 \
    #   | awk '{print $2}' \
    #   | xargs -I{} cp {} /tmp/softlockup/

    # 从 ../data/md/ssl 目录获取所有 .md 文件
    out_dsl_dir = "../data/dsl/rcu_stall"
    md_dir = "../data/md/rcu_stall"
    md_list = [os.path.join(md_dir, f) for f in os.listdir(md_dir) if f.endswith(".md")]

    # dsl_list = []

    for md_path in md_list:
        # 判断 dsl 目录下是否已经存在对应的 json 文件，存在则跳过
        output_path = os.path.join(
            out_dsl_dir, os.path.basename(md_path).replace(".md", ".json")
        )
        if os.path.exists(output_path):
            print(f"跳过已存在的文件：{output_path}")
            # with open(output_path, "r", encoding="utf-8") as f:
            #     dsl_data = f.read()
            #     dsl_list.append(dsl_data)
            continue

        # 打开 md 目录下下 7086442.md 文件，读取内容
        print(f"Read from md:{md_path}")

        with open(md_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        extract_dsl = extract_dsl_prompt | llm.with_structured_output(
            DiagnosisDSL, method="json_mode"  # 改用 json_mode
        )
        dsl_schema = DiagnosisDSL.model_json_schema()
        response = extract_dsl.invoke(
            {
                "markdown_content": markdown_content,
                "schema": json.dumps(dsl_schema, indent=2),
            }
        )
        print(f"Extracted DSL from md:{md_path} by LLM")

        # 存储为字典对象，方便后续处理
        if isinstance(response, DiagnosisDSL):
            # 如果是 DiagnosisDSL 实例，使用 model_dump()
            dsl_dict = response.model_dump()
        elif isinstance(response, dict):
            # 如果已经是字典，直接使用
            dsl_dict = response
        else:
            # 其他情况，抛出错误
            raise TypeError(f"Expected DiagnosisDSL or dict, got {type(response)}")

        # dsl_list.append(dsl_dict)

        # 运行后处理规范化：
        WHITELIST = {
            "alias",
            "ascii",
            "bpf",
            "bt",
            "btop",
            "dev",
            "dis",
            "eval",
            "exit",
            "extend",
            "files",
            "foreach",
            "fuser",
            "gdb",
            "help",
            "ipcs",
            "irq",
            "kmem",
            "list",
            "log",
            "mach",
            "mod",
            "mount",
            "net",
            "p",
            "ps",
            "pte",
            "ptob",
            "ptov",
            "rd",
            "repeat",
            "runq",
            "sbitmapq",
            "search",
            "set",
            "sig",
            "struct",
            "swap",
            "sym",
            "sys",
            "task",
            "timer",
            "tree",
            "union",
            "vm",
            "vtop",
            "waitq",
            "whatis",
            "wr",
        }

        def _first_token(s: str) -> str:
            s = s.strip()
            # split on whitespace; keep pipes as part of rest
            return s.split()[0] if s else ""

        def _looks_like_struct_token(tok: str) -> bool:
            # C identifier or identifier.field[,field]*
            return bool(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_,]+)?", tok)
            )

        def normalize_action(act: str) -> str:
            if not act:
                return act
            s = act.strip()
            # Strip accidental leading 'crash>' if any
            if s.startswith("crash>"):
                s = s[len("crash>") :].lstrip()

            # If starts with shell operator, leave as-is (will be filtered by rules elsewhere)
            if s[:1] in {"<", ">", "|", "&"}:
                return s

            tok = _first_token(s)
            # If already starts with 'struct' but next token is a whitelisted command, drop 'struct '
            if tok == "struct":
                parts = s.split(maxsplit=2)
                if len(parts) >= 2 and parts[1] in WHITELIST and parts[1] != "struct":
                    # remove leading 'struct '
                    s = s[len("struct ") :]
                    tok = _first_token(s)
                else:
                    return s

            # If token is whitelisted, keep
            if tok in WHITELIST:
                return s

            # If token is not whitelisted but looks like a C struct/field, prepend 'struct '
            if _looks_like_struct_token(tok):
                s = f"struct {s}"
                return s

            return s

        if isinstance(dsl_dict, dict) and isinstance(dsl_dict.get("workflow"), list):
            for step in dsl_dict["workflow"]:
                if isinstance(step, dict) and isinstance(step.get("action"), str):
                    normalized = normalize_action(step["action"])
                    step["action"] = normalized

        # 保存为 JSON 文件
        dsl_data = json.dumps(dsl_dict, ensure_ascii=False, indent=2)
        # 将 dsl_data 写入文件
        # output_path = os.path.join(
        #     "../data/dsl", os.path.basename(md_path).replace(".md", ".json")
        # )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dsl_data)

        print(f"Saved extracted DSL to: {output_path}")

        # # 1. 提取用于检索的语义特征 (指纹)


if __name__ == "__main__":
    # 先设置环境变量
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = "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" # fmt: skip

    main()
