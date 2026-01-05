import os
import json
import logging
import re
from typing import List, Optional, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langchain_core.callbacks import BaseCallbackHandler

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- 优化后的 Prompt：一对一映射 + 增量整合 ---
incremental_step_merge_prompt = ChatPromptTemplate.from_template(
    """You are a Linux Kernel Diagnostic Knowledge Architect.

# MISSION
Convert EVERY workflow step into a diagnostic branch and MERGE with existing knowledge base.

# CRITICAL INSTRUCTION: MERGE, DON'T REPLACE
- You MUST preserve ALL existing branches from current_kb
- You MUST add ALL new branches from new_steps
- The output MUST contain ALL branches from current_kb PLUS all new branches from new_steps
- DO NOT remove or replace any existing branches

# CONVERSION RULES (1:1 MAPPING)

For EACH new workflow step, create ONE branch:
- step.thought → branch.trigger (diagnostic condition/need)
- step.action → branch.action (with placeholders)
- step.observation → branch.expect (expected output)
- Add "why" explaining diagnostic purpose

# PLACEHOLDER REPLACEMENT
Replace in actions:
- Hex addresses: 0xffff88b5fffda000 → {{addr}}
- CPU numbers: cpu 32 → cpu {{cpu}}
- PIDs: process 11361 → process {{pid}}
- Offsets: +0x122 → +{{offset}}
- lsmod module names: e.g., ext4 → {{module}}
- IRQ numbers: e.g., 45 → {{irq}}
**Use SINGLE braces in JSON: {{cpu}}, {{addr}}, {{pid}}**

# FIELD CONSTRAINTS
- trigger: Max 20 words from step's thought
- action: Exact command with placeholders
- arg_hints: Max 15 words (parameter sources) or null
- why: Max 20 words (diagnostic purpose)
- expect: Max 20 words from step's observation
- is_end: **ALWAYS false** (no root cause branches)

# MERGE STRATEGY
- Keep ALL existing branches from current_kb
- Add ALL new branches from new_steps
- If similar branch exists → keep both (preserve variations)
- Update init_cmds with new common commands
- DO NOT over-deduplicate

# CURRENT KNOWLEDGE BASE (PRESERVE ALL)
{current_kb}

# NEW WORKFLOW STEPS TO CONVERT (ADD ALL)
{new_steps}

# OUTPUT SCHEMA
{schema}

# EXAMPLES

Input Step:
{{{{
  "thought": "Check specific MCS spinlock node",
  "action": "mcs_spinlock ffff914d3e11b8c0",
  "observation": "MCS node with next pointer and locked=1"
}}}}

Output Branch:
{{{{
  "trigger": "Need to examine specific MCS spinlock node details",
  "action": "mcs_spinlock {{addr}}",
  "arg_hints": "addr: from mcs_nodes per-CPU output",
  "why": "Verify MCS spinlock node state and queue linkage",
  "expect": "MCS node structure with next pointer and locked field",
  "is_end": false
}}}}

Input Step:
{{{{
  "thought": "List slab caches sorted by size",
  "action": "kmem -s | awk 'NR>1{{printf($1 \"\\t\" $NF \"\\t %8.3f MiB\\n\", $2*$4/2**20)}}' | sort -k3nr | column -t | head",
  "observation": "dentry cache using 787229 MiB"
}}}}

Output Branch:
{{{{
  "trigger": "Need to identify largest slab caches by memory usage",
  "action": "kmem -s | awk 'NR>1{{printf($1 \"\\t\" $NF \"\\t %8.3f MiB\\n\", $2*$4/2**20)}}' | sort -k3nr | column -t | head",
  "arg_hints": null,
  "why": "Find memory-consuming slab caches for reclaim analysis",
  "expect": "List of caches sorted by size in MiB, dentry typically largest",
  "is_end": false
}}}}

# IMPORTANT
- Preserve exact command syntax (pipes, awk, grep patterns)
- Each step = one unique branch (do NOT merge)
- Only replace specific values with placeholders
- OUTPUT MUST CONTAIN: ALL existing branches + ALL new branches
"""
)


class DiagnosticBranch(BaseModel):
    """Diagnostic rule with 1:1 mapping to workflow steps."""

    trigger: str = Field(
        ...,
        description="Diagnostic condition (max 20 words).",
    )
    action: str = Field(
        ...,
        description="Crash command with placeholders.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Parameter sources (max 15 words).",
    )
    why: str = Field(
        ...,
        description="Diagnostic purpose (max 20 words).",
    )
    expect: str = Field(
        ...,
        description="Expected output (max 20 words).",
    )
    is_end: bool = Field(
        False,
        description="Always false (no root cause branches).",
    )


class DiagnosticDict(BaseModel):
    """Diagnostic knowledge base."""

    summary: str = "Step-by-step diagnostic matrix for Linux kernel Hard LOCKUP"
    init_cmds: List[str] = Field(
        ...,
        description="Common initial commands (2-3).",
    )
    matrix: List[DiagnosticBranch] = Field(..., description="Diagnostic branches.")


class LoggingCallbackHandler(BaseCallbackHandler):
    """Logging callback."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        logger.info(f"LLM 调用开始")

    def on_llm_end(self, response, **kwargs):
        logger.info("LLM 调用结束")

    def on_llm_error(self, error, **kwargs):
        logger.error(f"❌ LLM 错误：{error}", exc_info=True)
        logger.error("程序终止：遇到 LLM 错误")
        raise RuntimeError(f"LLM 错误导致程序终止：{error}")

    def on_chain_error(self, error, **kwargs):
        logger.error(f"❌ Chain 错误：{error}", exc_info=True)
        logger.error("程序终止：遇到 Chain 错误")
        raise RuntimeError(f"Chain 错误导致程序终止：{error}")


def extract_workflow_steps_only(dsl_content: str) -> List[Dict[str, Any]]:
    """
    仅提取 workflow 步骤，忽略 root_cause_analysis。
    """
    try:
        dsl_data = json.loads(dsl_content)
        workflow_steps = dsl_data.get("workflow", [])

        steps = []
        for step in workflow_steps:
            steps.append(
                {
                    "thought": step.get("thought", ""),
                    "action": step.get("action", ""),
                    "observation": step.get("observation", "")[:250],
                }
            )

        logger.info(f"提取了 {len(steps)} 个工作流步骤")
        return steps

    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败：{e}")
        return []
    except Exception as e:
        logger.error(f"提取失败：{e}", exc_info=True)
        return []


def summarize_kb_for_merge(kb: Optional[DiagnosticDict]) -> str:
    """生成知识库摘要用于合并（严格控制 token 数量）"""
    if kb is None:
        return "Empty knowledge base (first batch - start fresh)"

    # 显示统计信息和示例，但不显示所有分支
    summary = f"""CURRENT KNOWLEDGE BASE STATUS (PRESERVE ALL):
- Total branches to preserve: {len(kb.matrix)}
- Init commands to preserve: {len(kb.init_cmds)}
- Summary: {kb.summary[:100]}...

EXAMPLE BRANCHES (for reference only):
"""
    # 显示前 3 个分支作为示例
    for i, branch in enumerate(kb.matrix[:3], 1):
        summary += f"{i}. Trigger: {branch.trigger[:50]}...\n"
        summary += f"   Action: {branch.action[:50]}...\n"

    summary += f"""
CRITICAL INSTRUCTION:
1. PRESERVE ALL {len(kb.matrix)} existing branches exactly as they are
2. ADD ALL new branches from new_steps
3. DO NOT remove, modify, or duplicate any existing branches
4. Final branch count should be {len(kb.matrix)} + (number of new steps)
"""

    return summary


def validate_knowledge_base(kb: DiagnosticDict) -> Dict[str, Any]:
    """验证知识库"""
    issues = []
    warnings = []

    total_branches = len(kb.matrix)

    if total_branches < 100:
        warnings.append(f"分支数偏少 ({total_branches}), 目标 150+")

    # 检查占位符格式
    wrong_pattern = re.compile(r"\{(?!\{)[a-z_]+\}(?!\})")
    placeholder_issues = sum(1 for b in kb.matrix if wrong_pattern.search(b.action))
    if placeholder_issues > 0:
        issues.append(f"{placeholder_issues} 个分支占位符格式错误")

    # 检查 init_cmds
    if len(kb.init_cmds) < 6:
        warnings.append(f"init_cmds 偏少 ({len(kb.init_cmds)})")

    # 命令多样性
    cmd_types = set()
    for b in kb.matrix:
        if b.action != "N/A":
            cmd = b.action.split()[0] if b.action.split() else ""
            cmd_types.add(cmd)

    if len(cmd_types) < 20:
        warnings.append(f"命令类型不足 ({len(cmd_types)} 种)")

    # 复杂命令
    complex = sum(
        1 for b in kb.matrix if any(kw in b.action for kw in ["awk", "sed", "paste"])
    )
    if complex < 10:
        warnings.append(f"复杂命令较少 ({complex})")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "statistics": {
            "total_branches": total_branches,
            "init_cmds": len(kb.init_cmds),
            "command_diversity": len(cmd_types),
            "complex_commands": complex,
        },
    }


def batch_step_by_step_integrate(
    dsl_files: List[str],
    batch_size: int = 1,  # 减少到每次处理 1 个文件，避免 token 超限
    output_file: str = "step_by_step_diagnostic_knowledge_library.json",
) -> Optional[DiagnosticDict]:
    """
    分批增量整合，避免 token 限制。
    """
    logger.info(f"开始分批整合 {len(dsl_files)} 个文件，批次大小：{batch_size}")

    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
        max_tokens=4000,  # 减少最大输出 token
    )

    dd_schema = DiagnosticDict.model_json_schema()
    chain = incremental_step_merge_prompt | llm.with_structured_output(
        DiagnosticDict, method="json_mode"
    )

    current_kb = None
    batches = [
        dsl_files[i : i + batch_size] for i in range(0, len(dsl_files), batch_size)
    ]

    for batch_num, batch_files in enumerate(batches, 1):
        logger.info(f"\n处理批次 {batch_num}/{len(batches)}: {len(batch_files)} 个文件")

        # 提取本批次的所有步骤
        batch_steps = []
        for file_path in batch_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                steps = extract_workflow_steps_only(content)
                if steps:
                    # 限制每个文件的步骤数量，避免 token 过多
                    max_steps_per_file = 15
                    if len(steps) > max_steps_per_file:
                        logger.info(
                            f"  {os.path.basename(file_path)}: {len(steps)} 步 → 限制为 {max_steps_per_file} 步"
                        )
                        steps = steps[:max_steps_per_file]
                    batch_steps.extend(steps)
                    logger.info(f"  {os.path.basename(file_path)}: {len(steps)} 步")
            except Exception as e:
                logger.error(f"  读取失败 {file_path}: {e}")

        if not batch_steps:
            logger.warning(f"批次 {batch_num} 无有效步骤")
            continue

        logger.info(f"  批次总步骤：{len(batch_steps)}")

        # 准备输入 - 限制步骤数量
        max_total_steps = 20
        if len(batch_steps) > max_total_steps:
            logger.info(f"  步骤过多 ({len(batch_steps)})，限制为 {max_total_steps}")
            batch_steps = batch_steps[:max_total_steps]

        # 简化步骤数据，减少 token 使用
        simplified_steps = []
        for step in batch_steps:
            simplified_steps.append(
                {
                    "thought": (
                        step["thought"][:100]
                        if len(step["thought"]) > 100
                        else step["thought"]
                    ),
                    "action": (
                        step["action"][:150]
                        if len(step["action"]) > 150
                        else step["action"]
                    ),
                    "observation": (
                        step["observation"][:80]
                        if len(step["observation"]) > 80
                        else step["observation"]
                    ),
                }
            )

        steps_json = json.dumps(
            simplified_steps, ensure_ascii=False, separators=(",", ":")
        )  # 紧凑格式
        kb_summary = summarize_kb_for_merge(current_kb)

        try:
            logger.info(f"  调用 LLM 整合...")

            new_kb = chain.invoke(
                {
                    "current_kb": kb_summary,
                    "new_steps": steps_json,
                    "schema": json.dumps(dd_schema, indent=2),
                },
                config={"callbacks": [LoggingCallbackHandler()]},
            )

            current_kb = new_kb
            logger.info(
                f"  ✓ 批次 {batch_num} 完成，当前分支：{len(current_kb.matrix)}"
            )

        except RuntimeError as e:
            # LoggingCallbackHandler 抛出的错误
            logger.error(f"  ✗ 批次 {batch_num} 遇到致命错误，程序终止")
            logger.error(f"  错误详情：{e}")
            return None  # 立即终止

        except Exception as e:
            # 其他错误也终止
            logger.error(f"  ✗ 批次 {batch_num} 遇到异常：{e}")
            logger.error(f"  异常详情：{e}", exc_info=True)
            return None  # 立即终止

    # 保存结果
    if current_kb:
        try:
            output_path = os.path.join("dsl", output_file)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(current_kb.model_dump_json(indent=2))

            logger.info(f"\n知识库已保存：{output_path}")
            return current_kb
        except Exception as e:
            logger.error(f"保存失败：{e}")

    return None


def main():
    """主函数"""
    os.makedirs("dsl", exist_ok=True)

    dsl_files = [
        "dsl/3379041.json",
        "dsl/3870151.json",
        "dsl/6348992.json",
        # "dsl/6988986.json",
        # "dsl/5764681.json",
        # "dsl/7041099.json",
    ]

    existing_files = [f for f in dsl_files if os.path.exists(f)]

    if not existing_files:
        logger.error("没有找到 DSL 文件")
        return

    logger.info(f"找到 {len(existing_files)} 个文件")

    # 执行分批整合
    result = batch_step_by_step_integrate(
        dsl_files=existing_files,
        batch_size=1,  # 减少到每批 1 个文件，避免 token 超限
        output_file="step_by_step_diagnostic_knowledge_library.json",
    )

    if result:
        validation = validate_knowledge_base(result)

        print(f"\n{'='*80}")
        print("📊 整合结果")
        print(f"{'='*80}")
        print(f"总分支：{validation['statistics']['total_branches']}")
        print(f"初始命令：{validation['statistics']['init_cmds']}")
        print(f"命令类型：{validation['statistics']['command_diversity']}")
        print(f"复杂命令：{validation['statistics']['complex_commands']}")

        print(f"\n{'='*80}")
        print("✅ 质量评估")
        print(f"{'='*80}")
        print(f"状态：{'✓ 通过' if validation['valid'] else '⚠ 需改进'}")

        if validation["issues"]:
            print(f"\n问题 ({len(validation['issues'])}):")
            for issue in validation["issues"]:
                print(f"  - {issue}")

        if validation["warnings"]:
            print(f"\n建议 ({len(validation['warnings'])}):")
            for warning in validation["warnings"]:
                print(f"  - {warning}")

        print(f"\n{'='*80}")
        print("📋 初始命令")
        print(f"{'='*80}")
        for i, cmd in enumerate(result.init_cmds, 1):
            print(f"  {i:2d}. {cmd}")

        print(f"\n{'='*80}")
        print("🔍 复杂命令示例")
        print(f"{'='*80}")
        complex = [b for b in result.matrix if "awk" in b.action or "sed" in b.action]
        for i, b in enumerate(complex[:5], 1):
            print(f"\n{i}. {b.trigger}")
            print(f"   {b.action[:80]}{'...' if len(b.action) > 80 else ''}")

        print(f"\n{'='*80}")
        print(
            f"覆盖率：{validation['statistics']['total_branches']}/164 = {validation['statistics']['total_branches']/164*100:.1f}%"
        )
        print(f"{'='*80}")

    else:
        logger.error("❌ 整合失败或被终止")
        print("请检查日志了解错误详情")


if __name__ == "__main__":
    os.environ["LANGSMITH_TRACING"] = "false"
    main()
