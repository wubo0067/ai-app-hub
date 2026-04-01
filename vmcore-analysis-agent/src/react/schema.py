#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# schema.py - VMCore 分析 Agent 数据模型定义
# Author: CalmWU
# Created: 2026-03-23

from typing import Any, ClassVar, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, Field, model_validator


class ToolCall(BaseModel):
    command_name: str = Field(
        ..., description="The crash command (e.g., 'dis', 'rd') or 'run_script'."
    )
    arguments: List[str] = Field(
        default_factory=list,
        description="Command arguments. For 'run_script', each string is a separate command line.",
    )

    @model_validator(mode="before")
    @classmethod
    def fix_malformed_action(cls, data: Any) -> Any:
        """修复 LLM 输出的常见格式错误"""
        if isinstance(data, dict):
            if "command_name" in data and "arguments" not in data:
                for key, value in list(data.items()):
                    if isinstance(value, list):
                        data["arguments"] = value
                        if key != "command_name":
                            del data[key]
                        break
                if "arguments" not in data:
                    data["arguments"] = []
        return data


class SuspectCode(BaseModel):
    """可疑代码位置"""

    file: str = Field(..., description="Source file path")
    function: str = Field(..., description="Function name")
    line: str = Field(..., description="Line number or 'unknown'")


class DriverSourceEvidence(BaseModel):
    """驱动源码层面的结构和字段推断证据。"""

    object_type: Optional[str] = Field(
        None,
        description="Inferred or confirmed struct type name, e.g. 'struct adapter_reply_queue'",
    )
    corrupted_field_name: Optional[str] = Field(
        None,
        description="Field name at the corrupted offset, e.g. 'reply_post_free_dma'",
    )
    corrupted_field_type: Optional[str] = Field(
        None,
        description="Declared C type of the corrupted field, e.g. 'dma_addr_t' vs 'void *'",
    )
    field_semantics: Optional[str] = Field(
        None,
        description="What the field should hold versus what the crash evidence shows it actually contains",
    )
    inference_method: Literal[
        "function_pointer_anchor",
        "symbol_lookup",
        "open_source_crossref",
        "apic_fingerprint",
        "list_head_selfref",
        "disassembly_offset_inference",
        "unknown",
    ] = Field("unknown", description="How the struct or field identity was determined")
    upstream_reference: Optional[str] = Field(
        None,
        description="Upstream commit, CVE, stable patch, or source-file reference if known",
    )


class FinalDiagnosis(BaseModel):
    """最终诊断结果的完整结构"""

    crash_type: str = Field(
        ...,
        description="Crash type (e.g., NULL pointer dereference, use-after-free, soft lockup)",
    )
    panic_string: str = Field(..., description="Exact panic string from dmesg")
    faulting_instruction: str = Field(
        ..., description="RIP address and disassembly of faulting instruction"
    )
    root_cause: str = Field(
        ..., description="1-2 sentence root cause explanation with evidence"
    )
    detailed_analysis: str = Field(
        ...,
        description="Multi-paragraph analysis with full evidence chain and kernel subsystem context",
    )
    suspect_code: SuspectCode = Field(..., description="Suspected source code location")
    evidence: List[str] = Field(
        ...,
        description="List of key evidence points (register values, memory contents, etc.)",
    )
    driver_source_evidence: Optional[DriverSourceEvidence] = Field(
        None,
        description="Source-level structural inference for third-party or driver-private crash objects",
    )
    corruption_mechanism: Optional[
        Literal[
            "field_type_misuse",
            "write_corruption",
            "race_condition",
            "missing_conversion",
            "reinit_path_bug",
            "unknown",
        ]
    ] = Field(
        None,
        description="Specific corruption mechanism once source-level field semantics are known",
    )


class VMCoreLLMAnalysisStep(BaseModel):
    """LLM 直接输出的最小结构，不包含 executor 管理的状态字段。"""

    step_id: int = Field(..., description="Current step sequence number.")
    reasoning: str = Field(
        ...,
        description=(
            "3-6 sentence structured analytic summary. Answer: "
            "(1) What did I just learn from the latest tool output? "
            "(2) How does this update the live hypotheses? "
            "(3) What is the ONE most diagnostic next action and why? "
            "Do NOT restate established facts. Do NOT produce free-form monologue."
        ),
    )
    action: Optional[ToolCall] = Field(
        None,
        description="The next command to run. Must be None when is_conclusive=True.",
    )
    is_conclusive: bool = Field(False)
    signature_class: Optional["CrashSignatureClass"] = Field(None)
    root_cause_class: Optional["RootCauseClass"] = Field(None)
    corruption_mechanism: Optional[
        Literal[
            "field_type_misuse",
            "write_corruption",
            "race_condition",
            "missing_conversion",
            "reinit_path_bug",
            "unknown",
        ]
    ] = Field(
        None,
        description=(
            "Optional finer-grained mechanism beneath root_cause_class. "
            "Use this for distinctions such as field_type_misuse versus write_corruption. "
            "Do not put these mechanism labels into root_cause_class."
        ),
    )
    partial_dump: "PartialDumpStatus" = Field("unknown")
    final_diagnosis: Optional[FinalDiagnosis] = Field(
        None, description="Populated only when is_conclusive=True."
    )
    fix_suggestion: Optional[str] = Field(
        None,
        description="Recommended fix or workaround.",
    )
    confidence: Optional[Literal["high", "medium", "low"]] = Field(
        None, description="Confidence level of the diagnosis."
    )
    additional_notes: Optional[str] = Field(
        None,
        description="Caveats, unresolved alternatives, or recommended follow-up actions.",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_crash_class(cls, data: Any) -> Any:
        if isinstance(data, dict) and "signature_class" not in data:
            legacy_value = data.get("crash_class")
            if legacy_value is not None:
                data["signature_class"] = legacy_value
        return data


CrashSignatureClass = Literal[
    "null_deref",
    "use_after_free",
    "pointer_corruption",
    "bug_on",
    "warn_on",
    "soft_lockup",
    "hard_lockup",
    "rcu_stall",
    "hung_task",
    "atomic_sleep",
    "divide_error",
    "invalid_opcode",
    "oom_panic",
    "mce",
    "general_protection_fault",
    "unknown",
]


# CrashSignatureClass: 从 panic 字符串直接可观测的早期路由标签。
# 不得用于表达 out_of_bounds / dma_corruption / race_condition 等需要深度推理才能得出的根因。
RootCauseClass = Literal[
    "null_deref",
    "use_after_free",
    "out_of_bounds",
    "double_free",
    "wild_pointer",
    "slab_corruption",
    "memory_corruption",
    "race_condition",
    "deadlock",
    "rcu_misuse",
    "atomic_sleep",
    "dma_corruption",
    "iommu_fault",
    "mce",
    "bug_on",
    "warn_on",
    "divide_error",
    "invalid_opcode",
    "oom",
    "oom_panic",
    "pointer_corruption",
    "unknown",
]


class Hypothesis(BaseModel):
    """
    分析过程中维护的一个候选根因假设。
    核心作用：让 agent 显式跟踪多个竞争假设的"当前站位"，避免隐式推理导致分析漂移。
    每步必须更新，只允许一个 leading 假设。
    """

    id: str = Field(..., description="Short identifier, e.g. 'H1', 'H2'")
    label: str = Field(
        ...,
        description="Concise hypothesis label, e.g. 'UAF', 'OOB_write', 'DMA_overwrite', 'null_deref'",
    )
    rank: Optional[int] = Field(
        None,
        description=(
            "Priority rank among active hypotheses (1=highest). "
            "Update every step as evidence shifts hypothesis standing. "
            "Optional — populate when multiple candidates compete."
        ),
    )
    status: Literal["leading", "candidate", "weakened", "ruled_out"] = Field(
        ...,
        description="Current standing. Only ONE hypothesis may be 'leading' at any step.",
    )
    evidence: Optional[str] = Field(
        None,
        description="One-sentence key evidence supporting or contradicting this hypothesis",
    )


class GateEntry(BaseModel):
    """
    is_conclusive=True 前必须完成的验证检查点。
    status 含义：
      open    — 尚未调查
      closed  — 已验证（evidence 字段必须填写具体工具输出，不得是泛泛总结）
      blocked — 前置 gate 尚未关闭（仅 external_corruption_gate 使用）
      n/a     — 确实不适用（evidence 字段必须说明原因）
    """

    required_for: List[CrashSignatureClass] = Field(
        ...,
        description="signature_class values that require this gate closed before is_conclusive=true",
    )
    status: Literal["open", "closed", "blocked", "n/a"] = Field(
        "open",
        description="open=pending, closed=verified, blocked=prereq not met, n/a=not applicable",
    )
    prerequisite: Optional[str] = Field(
        None,
        description="Gate name that must be closed before this gate can be worked on",
    )
    evidence: Optional[str] = Field(
        None,
        description="Specific tool output or observation that closed/blocked/n/a this gate",
    )


PartialDumpStatus = Literal[
    "full",
    "partial",
    "unknown",
]


class VMCoreAnalysisStep(BaseModel):
    step_id: int = Field(..., description="Current step sequence number.")

    _REQUIRED_GATES: ClassVar[Dict[str, List[str]]] = {
        "pointer_corruption": [
            "register_provenance",
            "object_lifetime",
            "local_corruption_exclusion",
            "external_corruption_gate",
            "field_type_classification",
        ],
        "null_deref": ["register_provenance"],
        "use_after_free": ["register_provenance", "object_lifetime"],
        "warn_on": ["warning_site", "warning_timeline"],
        "soft_lockup": ["stack_integrity", "lock_holder"],
        "hard_lockup": ["nmi_watchdog_evidence", "cpu_progress_state"],
        "rcu_stall": ["lock_holder", "rcu_stall_trace"],
        "hung_task": ["blocked_task_context", "wait_chain"],
        "mce": ["mce_log", "edac_evidence"],
        "atomic_sleep": ["stack_integrity"],
        "divide_error": ["divisor_validation"],
        "invalid_opcode": ["opcode_site"],
        "oom_panic": ["oom_context", "memory_pressure"],
        "general_protection_fault": ["register_provenance"],
        "bug_on": ["stack_integrity"],
    }

    _DEFAULT_ROOT_CAUSE_FROM_SIGNATURE: ClassVar[Dict[str, str]] = {
        "null_deref": "null_deref",
        "use_after_free": "use_after_free",
        "atomic_sleep": "atomic_sleep",
        "mce": "mce",
        "bug_on": "bug_on",
        "warn_on": "warn_on",
        "divide_error": "divide_error",
        "invalid_opcode": "invalid_opcode",
        "oom_panic": "oom_panic",
    }

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_crash_class(cls, data: Any) -> Any:
        """兼容旧版单字段 schema：crash_class -> signature_class。"""
        if isinstance(data, dict) and "signature_class" not in data:
            legacy_value = data.get("crash_class")
            if legacy_value is not None:
                data["signature_class"] = legacy_value
        return data

    reasoning: str = Field(
        ...,
        description=(
            "3-6 sentence structured analytic summary. Answer: "
            "(1) What did I just learn from the latest tool output? "
            "(2) How does this update the live hypotheses? "
            "(3) What is the ONE most diagnostic next action and why? "
            "Do NOT restate established facts. Do NOT produce free-form monologue."
        ),
    )

    action: Optional[ToolCall] = Field(
        None,
        description="The next command to run. Must be None when is_conclusive=True.",
    )

    is_conclusive: bool = Field(False)

    signature_class: Optional[CrashSignatureClass] = Field(
        None,
        description=(
            "Early crash signature from panic string. "
            "Null at step 1; concrete value required from step 2 onward. "
            "See §1.1a Crash Signature Decision Table. "
            "Do NOT use root-cause labels (dma_corruption, out_of_bounds, race_condition) here."
        ),
    )

    root_cause_class: Optional[RootCauseClass] = Field(
        None,
        description=(
            "Underlying root cause classification, distinct from signature_class. "
            "May be null during early investigation. "
            "Must be a concrete value (or 'unknown') when is_conclusive=True. "
            "Set to unknown only when evidence bounds the failure family but cannot isolate the mechanism."
        ),
    )

    corruption_mechanism: Optional[
        Literal[
            "field_type_misuse",
            "write_corruption",
            "race_condition",
            "missing_conversion",
            "reinit_path_bug",
            "unknown",
        ]
    ] = Field(
        None,
        description=(
            "Optional finer-grained corruption mechanism nested under root_cause_class. "
            "Examples: field_type_misuse, missing_conversion, write_corruption."
        ),
    )

    partial_dump: PartialDumpStatus = Field(
        "unknown",
        description=(
            "Vmcore completeness status. Set from sys output at step 2 and carry forward unchanged. "
            "When 'partial': NEVER retry rd/ptov/vtop on a VA that already returned empty output "
            "or seek-error in a prior step — treat it as permanently unreadable and move on. "
            "Record unreadable pages as 'page not in dump' evidence, do not spend more steps on them."
        ),
    )

    active_hypotheses: Optional[List[Hypothesis]] = Field(
        None,
        description=(
            "Ordered list of active hypotheses. Update EVERY step from step 2 onward. "
            "Only one hypothesis may have status='leading'. "
            "Ruled-out hypotheses should remain in the list with status='ruled_out' and evidence filled."
        ),
    )

    gates: Optional[Dict[str, GateEntry]] = Field(
        None,
        description=(
            "Evidence gates for mandatory checkpoints. "
            "Include ONLY gates whose required_for list contains the current signature_class. "
            "All required gates must reach status='closed' or 'n/a' before is_conclusive=true. "
            "See §1.1a Gate Catalog for closure standards."
        ),
    )

    final_diagnosis: Optional[FinalDiagnosis] = Field(
        None, description="Populated only when is_conclusive=True."
    )
    fix_suggestion: Optional[str] = Field(
        None,
        description="Recommended fix or workaround.",
    )
    confidence: Optional[Literal["high", "medium", "low"]] = Field(
        None, description="Confidence level of the diagnosis."
    )
    additional_notes: Optional[str] = Field(
        None,
        description="Caveats, unresolved alternatives, or recommended follow-up actions.",
    )

    @model_validator(mode="after")
    def validate_and_patch(self) -> "VMCoreAnalysisStep":
        """
        仅保留根因类默认映射；managed gates/hypotheses 由外部状态机维护。
        """
        if self.root_cause_class is None and self.is_conclusive:
            default_root_cause = self._DEFAULT_ROOT_CAUSE_FROM_SIGNATURE.get(
                self.signature_class or ""
            )
            self.root_cause_class = cast(
                RootCauseClass,
                default_root_cause or "unknown",
            )
        return self
