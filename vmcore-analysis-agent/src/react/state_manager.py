#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Any, Dict, List, Optional, cast

from .schema import (
    CrashSignatureClass,
    GateEntry,
    Hypothesis,
    PartialDumpStatus,
    RootCauseClass,
    VMCoreAnalysisStep,
    VMCoreLLMAnalysisStep,
)


def project_managed_analysis_step(
    llm_step: VMCoreLLMAnalysisStep,
    state: Dict[str, Any],
    *,
    original_reasoning: str,
) -> tuple[VMCoreAnalysisStep, Dict[str, Any]]:
    signature_class = cast(
        Optional[CrashSignatureClass],
        llm_step.signature_class or state.get("current_signature_class"),
    )
    root_cause_class = cast(
        Optional[RootCauseClass],
        llm_step.root_cause_class or state.get("current_root_cause_class"),
    )
    partial_dump = _resolve_partial_dump(
        llm_step.partial_dump, state, original_reasoning
    )
    active_hypotheses = _build_managed_hypotheses(
        signature_class,
        root_cause_class,
        state.get("managed_active_hypotheses"),
    )
    gates = _build_managed_gates(
        signature_class,
        state.get("managed_gates"),
    )

    step = VMCoreAnalysisStep.model_validate(
        {
            **llm_step.model_dump(),
            "signature_class": signature_class,
            "root_cause_class": root_cause_class,
            "partial_dump": partial_dump,
            "active_hypotheses": (
                [hyp.model_dump() for hyp in active_hypotheses]
                if active_hypotheses
                else None
            ),
            "gates": (
                {name: gate.model_dump() for name, gate in gates.items()}
                if gates
                else None
            ),
        }
    )

    managed_updates: Dict[str, Any] = {
        "current_signature_class": signature_class,
        "current_root_cause_class": root_cause_class,
        "current_partial_dump": partial_dump,
        "managed_active_hypotheses": active_hypotheses,
        "managed_gates": gates,
    }
    return step, managed_updates


def _resolve_partial_dump(
    partial_dump: PartialDumpStatus,
    state: Dict[str, Any],
    original_reasoning: str,
) -> PartialDumpStatus:
    if partial_dump != "unknown":
        return partial_dump

    previous = state.get("current_partial_dump")
    if previous in {"full", "partial"}:
        return cast(PartialDumpStatus, previous)

    lowered_reasoning = original_reasoning.lower()
    if "[partial dump]" in lowered_reasoning:
        return "partial"
    if "dump is complete" in lowered_reasoning:
        return "full"
    return "unknown"


def _build_managed_hypotheses(
    signature_class: Optional[CrashSignatureClass],
    root_cause_class: Optional[RootCauseClass],
    prior_hypotheses: Optional[List[Hypothesis]],
) -> Optional[List[Hypothesis]]:
    leading_label = (
        root_cause_class
        if root_cause_class and root_cause_class != "unknown"
        else signature_class
    )
    if leading_label is None or leading_label == "unknown":
        return prior_hypotheses

    managed: List[Hypothesis] = [
        Hypothesis(
            id="H1",
            label=leading_label,
            rank=1,
            status="leading",
            evidence=(
                "Managed by executor state from root_cause_class."
                if root_cause_class and root_cause_class != "unknown"
                else "Managed by executor state from signature_class."
            ),
        )
    ]

    for prior in prior_hypotheses or []:
        if prior.label == leading_label:
            continue
        managed.append(
            Hypothesis(
                id=f"H{len(managed) + 1}",
                label=prior.label,
                rank=len(managed) + 1,
                status="candidate" if prior.status == "leading" else prior.status,
                evidence=prior.evidence,
            )
        )
    return managed


def _build_managed_gates(
    signature_class: Optional[CrashSignatureClass],
    prior_gates: Optional[Dict[str, GateEntry]],
) -> Optional[Dict[str, GateEntry]]:
    """
    构建受管理的门控（gate）集合。

    该函数根据崩溃签名的类别，从预定义的门控配置中提取所需门控，
    并结合已有的历史门控状态，生成一个完整的受管理门控字典。

    门控（Gate）用于控制分析流程的执行条件，例如：
    - external_corruption_gate：外部破坏检测门控
    - local_corruption_exclusion：本地破坏排除门控
    - field_type_classification：字段类型分类门控

    Args:
        signature_class: 崩溃签名的类别，用于确定需要哪些门控。
                        若为 None 或 "unknown"，则直接返回历史门控。
        prior_gates: 已有的历史门控字典，包含之前分析阶段的状态。

    Returns:
        若签名类别有效且存在对应的门控配置，返回受管理门控字典；
        否则返回 None 或直接返回历史门控。

    Note:
        - 历史门控状态会被深拷贝，避免修改原始数据。
        - 新创建的门控会设置默认状态和证据信息。
        - external_corruption_gate 的状态依赖于 local_corruption_exclusion 的状态。
    """
    # 若签名类别无效，直接返回历史门控
    if signature_class is None or signature_class == "unknown":
        return prior_gates

    # 从全局配置中获取该类签名所需的所有门控名称列表
    required = VMCoreAnalysisStep._REQUIRED_GATES.get(signature_class, [])
    if not required:
        return None

    managed: Dict[str, GateEntry] = {}
    for gate_name in required:
        # 若历史门控中存在该门控，深拷贝其状态作为初始值
        previous = (prior_gates or {}).get(gate_name)
        if previous is not None:
            managed[gate_name] = GateEntry.model_validate(previous.model_dump())
            continue

        # 根据门控类型创建默认实例
        if gate_name == "external_corruption_gate":
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="blocked",  # 初始阻塞，等待本地破坏排除
                prerequisite="local_corruption_exclusion",
                evidence="Managed by executor state: waiting for local_corruption_exclusion to close.",
            )
        elif gate_name == "field_type_classification":
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="open",  # 初始开放，等待类型推断结果
                prerequisite=None,
                evidence=(
                    "Managed by executor state: awaiting source-level field typing via "
                    "function-pointer anchoring, source cross-reference, or defensible offset inference."
                ),
            )
        else:
            # 默认门控：开放状态，等待确定性的分析证据
            managed[gate_name] = GateEntry(
                required_for=[signature_class],
                status="open",
                prerequisite=None,
                evidence="Managed by executor state: awaiting deterministic evidence.",
            )

    # 处理 external_corruption_gate 与 local_corruption_exclusion 的依赖关系
    external_gate = managed.get("external_corruption_gate")
    local_gate = managed.get("local_corruption_exclusion")
    if external_gate is not None and external_gate.status not in ("closed", "n/a"):
        # 若 local_corruption_exclusion 尚未关闭，则 external_corruption_gate 保持阻塞
        if local_gate is None or local_gate.status not in ("closed", "n/a"):
            managed["external_corruption_gate"] = GateEntry(
                required_for=external_gate.required_for,
                status="blocked",
                prerequisite="local_corruption_exclusion",
                evidence=external_gate.evidence
                or "Managed by executor state: waiting for local_corruption_exclusion to close.",
            )
        # 若 local_corruption_exclusion 已关闭但 external_corruption_gate 仍被阻塞，则将其开放
        elif external_gate.status == "blocked":
            managed["external_corruption_gate"] = GateEntry(
                required_for=external_gate.required_for,
                status="open",  # 解除阻塞，转为开放等待证据
                prerequisite="local_corruption_exclusion",
                evidence="Managed by executor state: prerequisite satisfied; awaiting deterministic evidence.",
            )
    return managed
