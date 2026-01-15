import os
import json
import logging
import re
from typing import List, Dict, Any, Set
from pydantic import BaseModel, Field
from collections import defaultdict

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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
    arg_hints: str = Field(
        "",
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

    summary: str = (
        "Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios"
    )
    init_cmds: List[str] = Field(
        ...,
        description="Common initial commands.",
    )
    matrix: List[DiagnosticBranch] = Field(..., description="Diagnostic branches.")


def extract_all_steps(dsl_files: List[str]) -> List[Dict[str, Any]]:
    """提取所有 DSL 文件中的所有步骤"""
    all_steps = []

    for file_path in dsl_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            dsl_data = json.loads(content)
            workflow_steps = dsl_data.get("workflow", [])

            for step in workflow_steps:
                all_steps.append(
                    {
                        "thought": step.get("thought", ""),
                        "action": step.get("action", ""),
                        "observation": step.get("observation", ""),
                        "source_file": os.path.basename(file_path),
                    }
                )

            logger.info(
                f"从 {os.path.basename(file_path)} 提取了 {len(workflow_steps)} 个步骤"
            )

        except Exception as e:
            logger.error(f"读取失败 {file_path}: {e}")

    logger.info(f"总共提取了 {len(all_steps)} 个步骤")
    return all_steps


def normalize_command(cmd: str) -> str:
    """标准化命令，将具体值替换为占位符"""
    # 替换十六进制地址
    cmd = re.sub(r"0x[0-9a-fA-F]{8,}", "{addr}", cmd)
    # 替换 CPU 编号
    cmd = re.sub(r"cpu\s+\d+", "cpu {cpu}", cmd)
    cmd = re.sub(r"CPU\s*#?\d+", "CPU {cpu}", cmd)
    # 替换 PID
    cmd = re.sub(r"PID\s+\d+", "PID {pid}", cmd)
    cmd = re.sub(r"process\s+\d+", "process {pid}", cmd)
    # 替换偏移量
    cmd = re.sub(r"\+0x[0-9a-fA-F]+", "+{offset}", cmd)
    cmd = re.sub(r"-\s*0x[0-9a-fA-F]+", "-{offset}", cmd)

    return cmd


def step_to_branch(step: Dict[str, Any]) -> DiagnosticBranch:
    """将步骤转换为诊断分支"""
    thought = step["thought"]
    action = step["action"]
    observation = step["observation"]

    # 标准化命令
    normalized_action = normalize_command(action)

    # 生成 arg_hints
    arg_hints = ""
    if "{addr}" in normalized_action and "0x" in action:
        arg_hints = "addr: from register or memory address"
    elif "{cpu}" in normalized_action and ("cpu" in action.lower() or "CPU" in action):
        arg_hints = "cpu: from backtrace or system context"
    elif "{pid}" in normalized_action and (
        "pid" in action.lower() or "process" in action
    ):
        arg_hints = "pid: from process listing or task structure"

    # 生成 why
    why = thought[:100] if len(thought) > 100 else thought

    # 生成 expect
    expect = observation[:100] if len(observation) > 100 else observation

    return DiagnosticBranch(
        trigger=thought[:150],
        action=normalized_action,
        arg_hints=arg_hints,
        why=why[:100],
        expect=expect[:100],
        is_end=False,
    )


def deduplicate_branches(branches: List[DiagnosticBranch]) -> List[DiagnosticBranch]:
    """去重分支，基于 action 和 trigger 的相似性"""
    unique_branches = []
    seen = set()

    for branch in branches:
        # 创建唯一标识：action + 前 50 个字符的 trigger
        key = f"{branch.action}|{branch.trigger[:50]}"

        if key not in seen:
            seen.add(key)
            unique_branches.append(branch)
        else:
            # 如果已经存在，检查是否需要更新（比如更详细的 trigger）
            for existing in unique_branches:
                if f"{existing.action}|{existing.trigger[:50]}" == key:
                    # 如果新的 trigger 更长，更新为更详细的版本
                    if len(branch.trigger) > len(existing.trigger):
                        existing.trigger = branch.trigger
                    break

    return unique_branches


def extract_common_commands(branches: List[DiagnosticBranch]) -> List[str]:
    """提取常见的初始命令"""
    cmd_counts = defaultdict(int)

    for branch in branches:
        cmd = branch.action.split()[0] if branch.action.split() else ""
        if cmd and len(cmd) > 1:  # 过滤掉太短的命令
            cmd_counts[cmd] += 1

    # 选择出现频率最高的命令
    common_cmds = sorted(cmd_counts.items(), key=lambda x: x[1], reverse=True)

    # 返回前 5 个最常见的命令
    return [cmd for cmd, count in common_cmds[:5]]


def create_diagnostic_knowledge(dsl_files: List[str]) -> DiagnosticDict:
    """创建诊断知识库"""
    logger.info(f"开始处理 {len(dsl_files)} 个 DSL 文件")

    # 提取所有步骤
    all_steps = extract_all_steps(dsl_files)

    # 转换为分支
    all_branches = [step_to_branch(step) for step in all_steps]

    # 去重
    unique_branches = deduplicate_branches(all_branches)

    # 提取常见命令
    common_cmds = extract_common_commands(unique_branches)

    # 确保有一些基本命令
    basic_cmds = ["sys", "log", "bt", "ps", "kmem"]
    for cmd in basic_cmds:
        if cmd not in common_cmds:
            common_cmds.append(cmd)

    # 限制初始命令数量
    init_cmds = common_cmds[:8]

    logger.info(f"生成 {len(unique_branches)} 个唯一分支")
    logger.info(f"提取 {len(init_cmds)} 个初始命令")

    return DiagnosticDict(
        summary="Comprehensive diagnostic matrix for Linux kernel Hard LOCKUP scenarios",
        init_cmds=init_cmds,
        matrix=unique_branches,
    )


def validate_knowledge_base(kb: DiagnosticDict) -> Dict[str, Any]:
    """验证知识库"""
    issues = []
    warnings = []

    total_branches = len(kb.matrix)

    if total_branches < 50:
        warnings.append(f"分支数偏少 ({total_branches}), 建议 100+")

    # 检查占位符格式
    wrong_pattern = re.compile(r"\{(?!\{)[a-z_]+\}(?!\})")
    placeholder_issues = sum(1 for b in kb.matrix if wrong_pattern.search(b.action))
    if placeholder_issues > 0:
        issues.append(f"{placeholder_issues} 个分支占位符格式错误")

    # 检查 init_cmds
    if len(kb.init_cmds) < 5:
        warnings.append(f"init_cmds 偏少 ({len(kb.init_cmds)})")

    # 命令多样性
    cmd_types = set()
    for b in kb.matrix:
        if b.action != "N/A":
            cmd = b.action.split()[0] if b.action.split() else ""
            cmd_types.add(cmd)

    if len(cmd_types) < 15:
        warnings.append(f"命令类型不足 ({len(cmd_types)} 种)")

    # 复杂命令
    complex_cmds = sum(
        1
        for b in kb.matrix
        if any(kw in b.action for kw in ["awk", "sed", "paste", "grep", "|"])
    )
    if complex_cmds < 20:
        warnings.append(f"复杂命令较少 ({complex_cmds})")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "statistics": {
            "total_branches": total_branches,
            "init_cmds": len(kb.init_cmds),
            "command_diversity": len(cmd_types),
            "complex_commands": complex_cmds,
        },
    }


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

    existing_files = [f for f in dsl_files if os.path.exists(f)]

    if not existing_files:
        logger.error("没有找到 DSL 文件")
        return

    logger.info(f"找到 {len(existing_files)} 个文件")

    # 创建诊断知识库
    knowledge_base = create_diagnostic_knowledge(existing_files)

    # 保存结果
    output_path = os.path.join("dsl", "simple_diagnostic_knowledge_library.json")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(knowledge_base.model_dump_json(indent=2))

    logger.info(f"知识库已保存：{output_path}")

    # 验证
    validation = validate_knowledge_base(knowledge_base)

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
    for i, cmd in enumerate(knowledge_base.init_cmds, 1):
        print(f"  {i:2d}. {cmd}")

    print(f"\n{'='*80}")
    print("🔍 复杂命令示例")
    print(f"{'='*80}")
    complex = [
        b
        for b in knowledge_base.matrix
        if "awk" in b.action or "sed" in b.action or "|" in b.action
    ]
    for i, b in enumerate(complex[:5], 1):
        print(f"\n{i}. {b.trigger[:80]}{'...' if len(b.trigger) > 80 else ''}")
        print(f"   {b.action[:80]}{'...' if len(b.action) > 80 else ''}")

    print(f"\n{'='*80}")
    print(
        f"覆盖率：{validation['statistics']['total_branches']}/164 = {validation['statistics']['total_branches']/164*100:.1f}%"
    )
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
