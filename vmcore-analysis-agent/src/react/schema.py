#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# schema.py - VMCore 分析 Agent 数据模型定义
# Author: CalmWU
# Created: 2026-03-23

from typing import Any, ClassVar, Dict, List, Literal, Optional, cast, get_args

from pydantic import BaseModel, Field, model_validator

_CORRUPTION_MECHANISM_ALIASES = {
    "type_misuse": "field_type_misuse",
    "dma_type_misuse": "field_type_misuse",
    "overwrite": "write_corruption",
    "write_overwrite": "write_corruption",
    "reinit_bug": "reinit_path_bug",
}

_ROOT_CAUSE_LIKE_MECHANISMS = {
    "out_of_bounds",
    "double_free",
    "wild_pointer",
    "slab_corruption",
    "memory_corruption",
    "pointer_corruption",
    "use_after_free",
    "null_deref",
    "dma_corruption",
    "stack_corruption",
}

_ROOT_CAUSE_CLASS_ALIASES = {
    "corruption": "memory_corruption",
    "memory_error": "memory_corruption",
    "address_corruption": "wild_pointer",
    "invalid_pointer": "wild_pointer",
    "stack_protector": "stack_corruption",
    "stack_smash": "stack_corruption",
    "kernel_stack_corruption": "stack_corruption",
}

_ROOT_CAUSE_FROM_MECHANISM = {
    "field_type_misuse": "dma_corruption",
    "missing_conversion": "dma_corruption",
    "write_corruption": "memory_corruption",
    "reinit_path_bug": "race_condition",
    "race_condition": "race_condition",
    "unknown": "unknown",
}


CorruptionMechanism = Literal[
    "field_type_misuse",
    "write_corruption",
    "race_condition",
    "missing_conversion",
    "reinit_path_bug",
    "unknown",
]


def get_signature_class_aliases() -> dict[str, str]:
    """返回 signature_class 的兼容别名映射。"""
    return dict(_SIGNATURE_CLASS_ALIASES)


def get_root_cause_class_aliases() -> dict[str, str]:
    """返回 root_cause_class 的兼容别名映射。"""
    return dict(_ROOT_CAUSE_CLASS_ALIASES)


def get_corruption_mechanism_aliases() -> dict[str, str]:
    """返回 corruption_mechanism 的兼容别名映射。"""
    return dict(_CORRUPTION_MECHANISM_ALIASES)


def get_root_cause_from_mechanism_mapping() -> dict[str, str]:
    """返回 mechanism 到 root_cause_class 的标准映射。"""
    return dict(_ROOT_CAUSE_FROM_MECHANISM)


def get_root_cause_like_mechanisms() -> set[str]:
    """返回若误填到 corruption_mechanism 中应回灌到 root_cause_class 的值集合。"""
    return set(_ROOT_CAUSE_LIKE_MECHANISMS)


def get_signature_class_values() -> tuple[str, ...]:
    """返回 signature_class 的 canonical 枚举值。"""
    return cast(tuple[str, ...], get_args(CrashSignatureClass))


def get_signature_class_value_set() -> set[str]:
    """返回 signature_class 的 canonical 枚举值集合。"""
    return set(get_signature_class_values())


def get_root_cause_class_values() -> tuple[str, ...]:
    """返回 root_cause_class 的 canonical 枚举值。"""
    return cast(tuple[str, ...], get_args(RootCauseClass))


def get_root_cause_class_value_set() -> set[str]:
    """返回 root_cause_class 的 canonical 枚举值集合。"""
    return set(get_root_cause_class_values())


def get_corruption_mechanism_values() -> tuple[str, ...]:
    """返回 corruption_mechanism 的 canonical 枚举值。"""
    return cast(tuple[str, ...], get_args(CorruptionMechanism))


def get_corruption_mechanism_value_set() -> set[str]:
    """返回 corruption_mechanism 的 canonical 枚举值集合。"""
    return set(get_corruption_mechanism_values())


def get_partial_dump_values() -> tuple[str, ...]:
    """返回 partial_dump 的 canonical 枚举值。"""
    return cast(tuple[str, ...], get_args(PartialDumpStatus))


_SIGNATURE_CLASS_ALIASES: dict[str, str] = {
    "stack_protector": "stack_corruption",
    "stack_smash": "stack_corruption",
    "kernel_stack_corruption": "stack_corruption",
    "gp_fault": "general_protection_fault",
    "gpf": "general_protection_fault",
    "null_pointer": "null_deref",
    "nullptr": "null_deref",
    "uaf": "use_after_free",
    "oob": "pointer_corruption",
    "machine_check": "mce",
    "oom": "oom_panic",
}


def _coerce_signature_class(data: Any) -> Any:
    """对 signature_class 做有边界的容错归一化，防止 LLM 未知值中断管线。"""
    if not isinstance(data, dict):
        return data

    raw = data.get("signature_class")
    if not isinstance(raw, str):
        return data

    normalized = _SIGNATURE_CLASS_ALIASES.get(raw, raw)
    if normalized in get_signature_class_value_set():
        data["signature_class"] = normalized
    else:
        data["signature_class"] = "unknown"
    return data


def _coerce_root_cause_class(data: Any) -> Any:
    """对 root_cause_class 做有边界的容错归一化，避免近似标签中断管线。"""
    if not isinstance(data, dict):
        return data

    raw = data.get("root_cause_class")
    if not isinstance(raw, str):
        return data

    normalized = _ROOT_CAUSE_CLASS_ALIASES.get(raw, raw)

    if normalized in _ROOT_CAUSE_FROM_MECHANISM:
        if data.get("corruption_mechanism") in {None, "unknown"}:
            data["corruption_mechanism"] = normalized
        data["root_cause_class"] = _ROOT_CAUSE_FROM_MECHANISM[normalized]
        return data

    if normalized in get_root_cause_class_value_set():
        data["root_cause_class"] = normalized
    else:
        data["root_cause_class"] = "unknown"

    return data


def _coerce_corruption_mechanism(
    data: Any,
    *,
    root_cause_field: str | None = None,
) -> Any:
    """对 corruption_mechanism 做有边界的容错归一化。"""
    if not isinstance(data, dict):
        return data

    raw_value = data.get("corruption_mechanism")
    if not isinstance(raw_value, str):
        return data

    normalized = _CORRUPTION_MECHANISM_ALIASES.get(raw_value, raw_value)

    if normalized in get_corruption_mechanism_value_set():
        data["corruption_mechanism"] = normalized
        return data

    if normalized in _ROOT_CAUSE_LIKE_MECHANISMS and root_cause_field:
        current_root_cause = data.get(root_cause_field)
        if current_root_cause in {
            None,
            "unknown",
            "memory_corruption",
            "pointer_corruption",
        }:
            data[root_cause_field] = normalized

    data["corruption_mechanism"] = "unknown"
    return data


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
    corruption_mechanism: Optional[CorruptionMechanism] = Field(
        None,
        description=(
            "Specific corruption mechanism once source-level field semantics are known. "
            "This field is narrower than root_cause_class; do not put out_of_bounds, "
            "double_free, wild_pointer, or dma_corruption here."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_corruption_mechanism(cls, data: Any) -> Any:
        """
        对 corruption_mechanism 字段进行标准化处理的验证器方法。

        该方法用于对 LLM 输出的 corruption_mechanism 字段进行容错归一化处理，
        将可能存在的别名或不规范值转换为预定义的合法值之一，防止因 LLM
        输出格式不一致而导致的模型验证失败。

        处理逻辑:
        1. 如果 corruption_mechanism 是已知的别名，则将其转换为标准值
        2. 如果 corruption_mechanism 属于 root_cause 类型但不在允许值范围内，
           则将其移动到合适的 root_cause_class 字段，并将 corruption_mechanism 设置为 "unknown"
        3. 如果值完全未知且无法映射，则设置为 "unknown" 以确保流程继续运行

        Args:
            data: 输入的数据字典，应包含 corruption_mechanism 字段

        Returns:
            经过标准化处理的数据字典，确保 corruption_mechanism 字段符合预期的枚举值
        """
        return _coerce_corruption_mechanism(data)


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
    corruption_mechanism: Optional[CorruptionMechanism] = Field(
        None,
        description=(
            "Optional finer-grained mechanism beneath root_cause_class. "
            "Use this for distinctions such as field_type_misuse versus write_corruption. "
            "Do not put these mechanism labels into root_cause_class, and do not put "
            "root-cause families such as out_of_bounds into corruption_mechanism."
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
        data = _coerce_signature_class(data)
        data = _coerce_root_cause_class(data)
        return _coerce_corruption_mechanism(data, root_cause_field="root_cause_class")


# =============================================================================
# 崩溃分类系统 - 双层分类架构
# =============================================================================

# CrashSignatureClass: 崩溃签名类别（表层现象分类）
#
# 用途：
# - 从 panic 字符串直接可观测的早期路由标签
# - 用于快速确定分析路径和选择对应的分析剧本（playbook）
# - 决定需要完成哪些验证检查点（gates）才能得出结论
#
# 特点：
# - 基于直接可观测的现象（如 panic 消息、错误类型）
# - 在分析早期阶段（通常第 2 步）就需要确定
# - 主要用于分类和路由，而不是深入的根因分析
# - 不得用于表达需要深度推理才能得出的根因（如 out_of_bounds、dma_corruption、race_condition）
CrashSignatureClass = Literal[
    "null_deref",  # 空指针解引用 - unable to handle kernel NULL pointer dereference
    "use_after_free",  # 使用已释放内存 - paging request at non-NULL address 或 KASAN 报告
    "pointer_corruption",  # 指针损坏 - 通用指针损坏场景，需要进一步调查具体机制
    "bug_on",  # 内核 BUG 断言 - BUG_ON 触发的崩溃
    "warn_on",  # 内核 WARN 断言 - WARN_ON 触发的警告/崩溃
    "soft_lockup",  # 软锁死 - soft lockup detected
    "hard_lockup",  # 硬锁死 - NMI watchdog hard lockup
    "rcu_stall",  # RCU 停滞 - rcu_sched self-detected stall
    "hung_task",  # 任务挂起 - task blocked for more than 120 seconds
    "atomic_sleep",  # 原子上下文中睡眠 - BUG: scheduling while atomic
    "divide_error",  # 除零错误 - divide error
    "invalid_opcode",  # 无效操作码 - invalid opcode
    "oom_panic",  # 内存耗尽恐慌 - Out of memory panic
    "mce",  # 机器检查异常 - Machine Check Exception / Hardware Error
    "general_protection_fault",  # 通用保护故障 - x86 #13 或等效保护域故障
    "stack_corruption",  # 栈损坏 - stack-protector / kernel stack is corrupted
    "unknown",  # 未知类型 - 无法分类的崩溃类型
]


# RootCauseClass: 根本原因类别（深层机制分类）
#
# 用途：
# - 表示经过深入调查后确定的根本原因机制
# - 在分析结束时提供最终诊断依据
# - 为生成修复建议提供基础
#
# 特点：
# - 需要深度推理和工具验证才能确定
# - 在分析后期阶段才逐步明确
# - 反映真实的故障机制，而不仅仅是表面现象
# - 包含所有 CrashSignatureClass 的类别（因为某些情况下签名就是根因）
# - 额外包含需要深度分析才能确定的机制类别
RootCauseClass = Literal[
    "null_deref",  # 空指针解引用 - 直接的 NULL 解引用或小偏移成员访问
    "use_after_free",  # 使用已释放内存 - 典型的 UAF 场景
    "out_of_bounds",  # 数组越界 - 堆/栈缓冲区越界写入
    "double_free",  # 重复释放 - 同一内存被多次释放
    "wild_pointer",  # 野指针 - 未初始化或已损坏的指针
    "slab_corruption",  # Slab 内存池损坏 - slab metadata 或对象损坏
    "memory_corruption",  # 内存损坏 - 通用内存损坏，机制不明
    "race_condition",  # 竞态条件 - 多线程/多 CPU 竞争导致的状态不一致
    "deadlock",  # 死锁 - 循环等待资源导致的阻塞
    "rcu_misuse",  # RCU 误用 - RCU API 使用不当
    "atomic_sleep",  # 原子上下文中睡眠 - 在不可调度上下文中调用睡眠函数
    "dma_corruption",  # DMA 损坏 - 设备 DMA 操作导致的内存损坏
    "iommu_fault",  # IOMMU 故障 - IOMMU 映射或权限错误
    "mce",  # 机器检查异常 - 硬件错误导致的崩溃
    "bug_on",  # 内核 BUG 断言 - BUG_ON 条件触发
    "warn_on",  # 内核 WARN 断言 - WARN_ON 条件触发
    "divide_error",  # 除零错误 - 除法操作中除数为零
    "invalid_opcode",  # 无效操作码 - 执行了无效的 CPU 指令
    "oom",  # 内存耗尽 - OOM killer 触发但未导致 panic
    "oom_panic",  # 内存耗尽恐慌 - OOM 导致系统 panic
    "pointer_corruption",  # 指针损坏 - 通用指针损坏，机制待确定
    "stack_corruption",  # 栈损坏 - 已能确认栈被破坏，但尚未定位更深层机制
    "unknown",  # 未知根因 - 证据不足以确定具体机制
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

    corruption_mechanism: Optional[CorruptionMechanism] = Field(
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
