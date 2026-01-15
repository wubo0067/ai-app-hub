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

# --- 重新设计的 Prompt：强调覆盖率而非简洁性 ---
incremental_merge_prompt = ChatPromptTemplate.from_template(
    """You are a Linux Kernel Diagnostic Architect creating a COMPREHENSIVE diagnostic manual.

# CRITICAL GOAL
Your PRIMARY objective is MAXIMUM COVERAGE - preserve EVERY unique diagnostic technique, command, and decision path from the source data. The knowledge base should be a COMPLETE reference manual, NOT a simplified summary.

# INTEGRATION PHILOSOPHY
1. **Preserve Detail Over Simplicity**: When in doubt, create a NEW branch rather than merge
2. **One Technique = One Branch**: Each distinct crash command or diagnostic technique gets its own branch
3. **Context Matters**: Even similar commands are DIFFERENT if they check different things (e.g., "bt -c {cpu}" for different purposes)
4. **No Information Loss**: If a source workflow has 50 steps, expect to create 40+ branches from it

# MERGE RULES (STRICT)
✅ MERGE ONLY IF:
- EXACT same command template AND same trigger AND same purpose
- Example: "log | grep LOCKUP" for initial detection (merge these)

❌ DO NOT MERGE IF:
- Command differs in any argument (e.g., "bt" vs "bt -c {cpu}" vs "bt -a")
- Same command but different diagnostic context (e.g., checking spinlock for deadlock vs checking spinlock for corruption)
- Different fields examined in structures (e.g., "px &zone->lock" vs "px &rq->lock")
- Different analysis paths (e.g., RCU stall path vs dentry cache path)

# PLACEHOLDER RULES
- Use {{{{cpu}}}}, {{{{addr}}}}, {{{{pid}}}}, {{{{offset}}}} in JSON output
- Replace ALL specific values: 0xffff88... → {{{{addr}}}}, cpu 3 → cpu {{{{cpu}}}}

# FIELD CONSTRAINTS
- trigger: Max 18 words, SPECIFIC enough to distinguish from similar branches
- action: Full command with all arguments and pipes
- arg_hints: Max 15 words, explain where to get each variable
- why: Max 20 words, explain WHAT this step diagnoses (not just "check X")
- expect: Max 20 words, specific output pattern or value range
- is_end: true ONLY for definitive root cause conclusion (should have action="N/A")

# TARGET METRICS
- **For 6 files with ~160 total steps**: Generate 80-120 branches
- **init_cmds**: 8-12 common first steps
- **Diagnostic branches** (is_end=false): 70-105
- **Root cause branches** (is_end=true): 10-15

# CURRENT KNOWLEDGE BASE
{current_kb_summary}

# NEW DIAGNOSTIC WORKFLOWS TO INTEGRATE
{new_case_summary}

# OUTPUT SCHEMA
{schema}

# QUALITY EXAMPLES

## Good Branch (Specific Command):
{{{{
  "trigger": "Need spinlock address from backtrace RDI register",
  "action": "bt | awk '$5~/RDI:/ {{{{print $6}}}}'",
  "arg_hints": "Extract from exception frame in backtrace output",
  "why": "RDI register typically holds first function argument (spinlock pointer)",
  "expect": "Hex address like 0xffffffff9b67a980",
  "is_end": false
}}}}

## Good Branch (Structure Field Check):
{{{{
  "trigger": "Verify zone lock causing memory allocation stall",
  "action": "px &((struct zone *){{{{addr}}}})-> lock",
  "arg_hints": "addr: zone address from 'kmem -z' output",
  "why": "Zone lock contention blocks page allocation and reclaim",
  "expect": "Spinlock address for comparison with stuck CPU RDI",
  "is_end": false
}}}}

## Good Root Cause:
{{{{
  "trigger": "Negative dentry explosion with dcache_lru_lock contention",
  "action": "N/A",
  "arg_hints": null,
  "why": "Billions of negative dentries cause slab shrinking storms during reclaim",
  "expect": "dentry_stat shows >1B negative dentries, dcache_lru_lock heavily contended",
  "is_end": true
}}}}

## BAD Example (Too Generic - AVOID THIS):
{{{{
  "trigger": "Check backtraces",  ❌ Too vague
  "action": "bt -a",  ❌ Missing context of what to look for
  "why": "See what tasks are doing",  ❌ Not specific enough
  "expect": "Task states",  ❌ Not actionable
  "is_end": false
}}}}
"""
)


# 数据结构定义
class DiagnosticBranch(BaseModel):
    """Simplified diagnostic rule: Condition -> Action."""

    trigger: str = Field(
        ...,
        description="Concise symptom (max 18 words). Be SPECIFIC to distinguish similar scenarios.",
    )
    action: str = Field(
        ...,
        description="Complete command template with arguments, e.g., 'px &((struct zone *){{addr}})->lock'.",
    )
    arg_hints: Optional[str] = Field(
        None,
        description="Parameter sources (max 15 words). E.g., 'addr: from kmem -z, cpu: from log'.",
    )
    why: str = Field(
        ...,
        description="Diagnostic purpose (max 20 words). Explain WHAT this checks for.",
    )
    expect: str = Field(
        ...,
        description="Expected output pattern (max 20 words). Be specific.",
    )
    is_end: bool = Field(
        False,
        description="True ONLY if this is a definitive root cause conclusion (action should be 'N/A').",
    )


class DiagnosticDict(BaseModel):
    """Comprehensive diagnostic knowledge base for Hard LOCKUP scenarios."""

    summary: str = (
        "Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios"
    )
    init_cmds: List[str] = Field(
        ...,
        description="Common initial diagnostic commands (8-12 commands).",
    )
    matrix: List[DiagnosticBranch] = Field(
        ..., description="Complete decision matrix with 80-120+ diagnostic branches."
    )


class LoggingCallbackHandler(BaseCallbackHandler):
    """Callback handler for logging LLM operations."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        logger.info(f"LLM 调用开始：{serialized.get('name', 'unknown')}")

    def on_llm_end(self, response, **kwargs):
        logger.info("LLM 调用结束")

    def on_llm_error(self, error, **kwargs):
        logger.error(f"LLM 错误：{error}", exc_info=True)

    def on_chain_error(self, error, **kwargs):
        logger.error(f"Chain 错误：{error}", exc_info=True)


def extract_full_workflow_details(dsl_content: str) -> Dict[str, Any]:
    """
    提取完整的 workflow 细节，不做过度精简。
    """
    try:
        dsl_data = json.loads(dsl_content)

        scenario = dsl_data.get("scenario", "Unknown scenario")
        symptoms = dsl_data.get("symptoms", [])
        workflow_steps = dsl_data.get("workflow", [])
        root_causes = dsl_data.get("root_cause_analysis", [])

        # 提取所有步骤的完整信息
        detailed_steps = []
        for step in workflow_steps:
            detailed_steps.append(
                {
                    "step_number": step.get("step_number", 0),
                    "thought": step.get("thought", ""),
                    "action": step.get("action", ""),
                    "observation": step.get("observation", "")[
                        :200
                    ],  # 保留更多观察信息
                }
            )

        summary = {
            "scenario": scenario,
            "symptoms": symptoms,  # 保留所有症状
            "total_workflow_steps": len(workflow_steps),
            "detailed_workflow": detailed_steps,  # 完整步骤序列
            "root_causes": root_causes,  # 保留所有根因分析
            "workflow_insights": {
                "command_types": set(),
                "key_structures_examined": set(),
                "key_addresses_checked": set(),
            },
        }

        # 分析命令模式
        for step in workflow_steps:
            action = step.get("action", "")
            if not action:
                continue

            # 识别命令类型
            cmd_first = action.split()[0] if action.split() else ""
            summary["workflow_insights"]["command_types"].add(cmd_first)

            # 识别结构体检查
            if "struct" in action:
                import re

                struct_matches = re.findall(r"struct\s+(\w+)", action)
                for s in struct_matches:
                    summary["workflow_insights"]["key_structures_examined"].add(s)

        # 转换 set 为 list
        summary["workflow_insights"]["command_types"] = list(
            summary["workflow_insights"]["command_types"]
        )
        summary["workflow_insights"]["key_structures_examined"] = list(
            summary["workflow_insights"]["key_structures_examined"]
        )
        summary["workflow_insights"]["key_addresses_checked"] = list(
            summary["workflow_insights"]["key_addresses_checked"]
        )

        logger.info(
            f"提取了 {len(detailed_steps)} 个完整步骤，{len(symptoms)} 个症状，{len(root_causes)} 个根因"
        )

        return summary

    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败：{e}")
        return {"error": "JSON parsing failed", "raw_preview": dsl_content[:500]}
    except Exception as e:
        logger.error(f"提取失败：{e}", exc_info=True)
        return {"error": str(e), "raw_preview": dsl_content[:500]}


def summarize_knowledge_base(kb: Optional[DiagnosticDict]) -> str:
    """
    生成知识库的详细摘要。
    """
    if kb is None:
        return "Empty knowledge base (initial state)"

    end_branches = [b for b in kb.matrix if b.is_end]
    diagnostic_branches = [b for b in kb.matrix if not b.is_end]

    # 统计命令类型
    command_types = {}
    for branch in diagnostic_branches:
        cmd = branch.action.split()[0] if branch.action.split() else "N/A"
        command_types[cmd] = command_types.get(cmd, 0) + 1

    summary = f"""
Current Knowledge Base Status:
═══════════════════════════════════════
Total Branches: {len(kb.matrix)}
  ├─ Diagnostic Steps: {len(diagnostic_branches)}
  └─ Root Causes: {len(end_branches)}

Init Commands: {len(kb.init_cmds)}

Command Type Distribution:
"""

    for cmd, count in sorted(command_types.items(), key=lambda x: x[1], reverse=True)[
        :10
    ]:
        summary += f"  - {cmd}: {count} branches\n"

    summary += f"\nRecent Branches (last 5):\n"
    for i, branch in enumerate(kb.matrix[-5:] if len(kb.matrix) >= 5 else kb.matrix, 1):
        end_marker = " [ROOT]" if branch.is_end else ""
        summary += f"  {i}. {branch.trigger[:70]}...{end_marker}\n"

    return summary


def estimate_token_count(content: Dict) -> int:
    """估算内容的 token 数量"""
    json_str = json.dumps(content, ensure_ascii=False)
    return len(json_str) // 4


def validate_knowledge_base(kb: DiagnosticDict) -> Dict[str, Any]:
    """验证知识库质量"""
    issues = []
    warnings = []

    # 检查 1: 分支数量
    total_branches = len(kb.matrix)
    if total_branches < 60:
        warnings.append(f"分支总数偏少 ({total_branches}), 建议 80-120 个以确保覆盖率")

    # 检查 2: 检测重复的 trigger
    triggers = [b.trigger for b in kb.matrix]
    duplicate_triggers = [t for t in set(triggers) if triggers.count(t) > 1]
    if duplicate_triggers:
        warnings.append(f"发现 {len(duplicate_triggers)} 个重复的 trigger")

    # 检查 3: 确保有足够的结束节点
    end_nodes = [b for b in kb.matrix if b.is_end]
    if len(end_nodes) < 5:
        issues.append(f"根因结论过少 ({len(end_nodes)}), 建议至少 5-10 个")

    # 检查 4: 诊断步骤数量
    diagnostic_nodes = [b for b in kb.matrix if not b.is_end]
    if len(diagnostic_nodes) < 50:
        warnings.append(f"诊断步骤偏少 ({len(diagnostic_nodes)}), 建议 70+ 个")

    # 检查 5: 检查占位符格式
    wrong_placeholder_pattern = re.compile(r"\{(?!\{)[a-z_]+\}(?!\})")

    for i, branch in enumerate(kb.matrix):
        # 检查错误的占位符格式
        if wrong_placeholder_pattern.search(branch.action):
            issues.append(f"分支 {i+1} 使用了错误的占位符：'{branch.action[:50]}...'")

        # 检查字段长度
        trigger_words = len(branch.trigger.split())
        if trigger_words > 18:
            warnings.append(f"分支 {i+1} trigger 过长 ({trigger_words} 词)")

    # 检查 6: init_cmds
    if len(kb.init_cmds) < 5:
        issues.append(f"init_cmds 过少 ({len(kb.init_cmds)}), 建议 8-12 个")

    # 检查 7: 命令多样性
    command_types = set()
    for branch in kb.matrix:
        if branch.action != "N/A":
            cmd = branch.action.split()[0] if branch.action.split() else ""
            command_types.add(cmd)

    if len(command_types) < 15:
        warnings.append(f"命令类型多样性不足 ({len(command_types)} 种), 建议 20+ 种")

    validation_result = {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "statistics": {
            "total_branches": len(kb.matrix),
            "diagnostic_steps": len(diagnostic_nodes),
            "root_causes": len(end_nodes),
            "init_cmds": len(kb.init_cmds),
            "unique_triggers": len(set(triggers)),
            "duplicate_triggers": len(duplicate_triggers),
            "command_diversity": len(command_types),
        },
    }

    return validation_result


def comprehensive_integrate(
    dsl_files: List[str],
    output_file: str = "comprehensive_diagnostic_knowledge_library.json",
) -> Optional[DiagnosticDict]:
    """
    一次性整合所有文件以获得最大覆盖率。
    """
    logger.info(f"开始全量整合 {len(dsl_files)} 个 DSL 文件")

    # 初始化 LLM
    llm = ChatOpenAI(
        api_key="sk-b5480f840a794c69a0af1732459f3ae4",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0,
    )

    # 创建整合链
    dd_schema = DiagnosticDict.model_json_schema()
    chain = incremental_merge_prompt | llm.with_structured_output(
        DiagnosticDict, method="json_mode"
    )

    # 读取所有文件并提取完整信息
    all_workflows = []
    total_steps = 0

    for file_path in dsl_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            workflow_info = extract_full_workflow_details(content)
            all_workflows.append(
                {"file": os.path.basename(file_path), "workflow": workflow_info}
            )
            total_steps += workflow_info.get("total_workflow_steps", 0)
            logger.info(
                f"  已读取：{os.path.basename(file_path)} ({workflow_info.get('total_workflow_steps', 0)} 步)"
            )
        except Exception as e:
            logger.error(f"  读取文件失败 {file_path}: {e}")

    logger.info(
        f"总计 {total_steps} 个诊断步骤待整合，目标生成 {int(total_steps * 0.6)}-{int(total_steps * 0.75)} 个分支"
    )

    # 准备完整的输入数据
    combined_data = {
        "total_source_files": len(all_workflows),
        "total_diagnostic_steps": total_steps,
        "expected_output_branches": f"{int(total_steps * 0.6)}-{int(total_steps * 0.75)}",
        "all_workflows": all_workflows,
    }

    combined_json = json.dumps(combined_data, ensure_ascii=False, indent=2)

    logger.info(f"准备调用 LLM 进行全量整合...")
    logger.info(
        f"  输入数据大小：~{len(combined_json)} 字符 (~{estimate_token_count(combined_data)} tokens)"
    )

    try:
        final_kb = chain.invoke(
            {
                "current_kb_summary": "Empty knowledge base (first integration)",
                "new_case_summary": combined_json,
                "schema": json.dumps(dd_schema, indent=2),
            },
            config={"callbacks": [LoggingCallbackHandler()]},
        )

        logger.info(f"LLM 整合成功！生成 {len(final_kb.matrix)} 个分支")

        # 保存结果
        output_path = os.path.join("dsl", output_file)
        diagnostic_dict_json = final_kb.model_dump_json(indent=2)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(diagnostic_dict_json)

        logger.info(f"知识库已保存至：{output_path}")

        return final_kb

    except Exception as e:
        logger.error(f"LLM 整合失败：{e}", exc_info=True)
        return None


def main():
    """主函数"""
    os.makedirs("dsl", exist_ok=True)

    dsl_files = [
        "dsl/3379041.json",
        "dsl/3870151.json",
        "dsl/6348992.json",
        "dsl/6988986.json",
        "dsl/5764681.json",
        "dsl/7041099.json",
    ]

    # 检查文件
    existing_files = []
    for file_path in dsl_files:
        if os.path.exists(file_path):
            existing_files.append(file_path)
        else:
            logger.warning(f"文件不存在：{file_path}")

    if not existing_files:
        logger.error("没有找到可用的 DSL 文件")
        return

    logger.info(f"找到 {len(existing_files)} 个 DSL 文件")

    # 执行全量整合
    result = comprehensive_integrate(
        dsl_files=existing_files,
        output_file="comprehensive_diagnostic_knowledge_library.json",
    )

    if result:
        logger.info("整合完成！")

        # 验证结果
        validation = validate_knowledge_base(result)

        print(f"\n{'='*80}")
        print("📊 整合结果分析")
        print(f"{'='*80}")
        print(f"知识库总结：{result.summary}")
        print(f"\n分支统计：")
        print(f"  ├─ 总分支数：{len(result.matrix)}")
        print(f"  ├─ 诊断步骤：{len([b for b in result.matrix if not b.is_end])}")
        print(f"  └─ 根因结论：{len([b for b in result.matrix if b.is_end])}")
        print(f"\n初始命令：{len(result.init_cmds)} 个")

        print(f"\n{'='*80}")
        print("✅ 质量评估")
        print(f"{'='*80}")
        print(f"状态：{'✓ 通过' if validation['valid'] else '⚠ 需要改进'}")
        print(f"命令多样性：{validation['statistics']['command_diversity']} 种不同命令")
        print(f"唯一触发器：{validation['statistics']['unique_triggers']} 个")

        if validation["issues"]:
            print(f"\n⚠️  关键问题 ({len(validation['issues'])} 个):")
            for issue in validation["issues"]:
                print(f"  - {issue}")

        if validation["warnings"]:
            print(f"\n⚡ 优化建议 ({len(validation['warnings'])} 个):")
            for warning in validation["warnings"][:8]:
                print(f"  - {warning}")

        print(f"\n{'='*80}")
        print("📋 初始命令清单")
        print(f"{'='*80}")
        for i, cmd in enumerate(result.init_cmds, 1):
            print(f"  {i:2d}. {cmd}")

        print(f"\n{'='*80}")
        print("🔍 诊断分支预览 (前 15 个)")
        print(f"{'='*80}")
        for i, branch in enumerate(result.matrix[:15], 1):
            end_marker = " [根因]" if branch.is_end else ""
            print(f"\n  {i:2d}. {branch.trigger}{end_marker}")
            print(
                f"      命令：{branch.action[:70]}{'...' if len(branch.action) > 70 else ''}"
            )
            print(f"      目的：{branch.why}")

    else:
        logger.error("整合失败：未能生成知识库")


if __name__ == "__main__":
    os.environ["TAVILY_API_KEY"] = "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"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGSMITH_API_KEY"] = (
        "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"
    )

    main()
