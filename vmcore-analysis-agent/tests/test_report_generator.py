import sys
import types
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.report_generator import (
    generate_gate_audit_report,
    generate_markdown_report,
)
from src.react.evidence import gate_completion_criteria
from src.react.schema import GateEntry


def _base_state(**overrides):
    state = {
        "vmcore_path": "/var/crash/127.0.0.1/vmcore",
        "vmlinux_path": "/usr/lib/debug/vmlinux",
        "vmcore_dmesg_path": "/var/crash/127.0.0.1/vmcore-dmesg.txt",
        "messages": [],
        "managed_gates": {},
        "gate_transition_history": [],
    }
    state.update(overrides)
    return state


def _gates():
    return {
        "register_provenance": GateEntry(
            required_for=["null_deref"],
            status="closed",
            closure_basis="executor_evidence",
            evidence="rd ffff0000 -> 0x0",
            completion_criteria=["known value source"],
        ),
        "object_lifetime": GateEntry(
            required_for=["use_after_free"],
            status="open",
            completion_criteria=["allocation and free observed"],
        ),
    }


class ReportGateSeparationTests(unittest.TestCase):
    def test_report_has_no_gate_audit_section(self) -> None:
        report = generate_markdown_report(
            _base_state(managed_gates=_gates(), gate_transition_history=[])
        )
        self.assertNotIn("## Gate 审计", report)
        self.assertNotIn("完成条件", report)

    def test_report_contains_verification_summary_zh(self) -> None:
        report = generate_markdown_report(
            _base_state(report_language="zh", managed_gates=_gates())
        )
        self.assertIn("## 验证状态", report)
        self.assertIn("故障操作数的来源链", report)
        self.assertIn("✅ 已确认", report)
        self.assertIn("⚠️ 待确认", report)
        self.assertIn("结论适用范围", report)

    def test_report_contains_verification_summary_eng(self) -> None:
        report = generate_markdown_report(
            _base_state(report_language="eng", managed_gates=_gates())
        )
        self.assertIn("## Verification Status", report)
        self.assertIn("Source chain of the faulting operand", report)
        self.assertIn("✅ Verified", report)
        self.assertIn("Scope of the conclusion", report)

    def test_verification_summary_all_confirmed(self) -> None:
        gates = {
            "register_provenance": GateEntry(
                required_for=["null_deref"],
                status="closed",
                closure_basis="executor_evidence",
            )
        }
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="null_deref",
                managed_gates=gates,
            )
        )
        self.assertNotIn("结论适用范围", report)
        self.assertIn("全部关键验证环节均已获得确定性证据支持", report)

    def test_verification_summary_accepts_serialized_gates(self) -> None:
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="soft_lockup",
                managed_gates={
                    "stack_integrity": {
                        "required_for": ["soft_lockup"],
                        "status": "open",
                        "closure_basis": None,
                    },
                    "lock_holder": {
                        "required_for": ["soft_lockup"],
                        "status": "open",
                        "closure_basis": None,
                    },
                },
            )
        )
        self.assertIn("stack_integrity", report)
        self.assertIn("规则能力限制", report)
        self.assertIn("本次分析无法形成定论", report)

    def test_lifetime_summary_limits_allocated_snapshot_claim(self) -> None:
        gates = {
            "object_lifetime": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            )
        }
        report = generate_markdown_report(
            _base_state(report_language="zh", managed_gates=gates)
        )

        self.assertIn("目标对象状态与生命周期证据", report)
        self.assertIn("不能排除旧对象释放后槽位被复用（UAF）", report)

        english_report = generate_markdown_report(
            _base_state(report_language="eng", managed_gates=gates)
        )
        self.assertIn("Object-state and lifetime evidence", english_report)
        self.assertIn("does not rule out reuse", english_report)

    def test_verification_summary_without_gates(self) -> None:
        report = generate_markdown_report(_base_state(report_language="zh"))
        self.assertIn("## 验证状态", report)
        self.assertIn("尚未分类或没有已注册的门控策略", report)
        self.assertIn("不能据此视为验证通过", report)

    def test_verification_summary_reports_uninitialized_required_gates(self) -> None:
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="null_deref",
                managed_gates={},
            )
        )
        self.assertIn("必需门控状态尚未初始化", report)
        self.assertIn("不能据此视为验证通过", report)

    def test_report_discloses_required_gate_without_closure_rule(self) -> None:
        gates = {
            "stack_integrity": GateEntry(
                required_for=["soft_lockup"], status="open"
            ),
            "lock_holder": GateEntry(
                required_for=["soft_lockup"], status="open"
            ),
        }
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="soft_lockup",
                managed_gates=gates,
            )
        )
        self.assertIn("规则能力限制", report)
        self.assertIn("执行器闭合规则尚未实现", report)
        self.assertIn("本次分析无法形成定论", report)

    def test_n_a_status_label_says_reserved(self) -> None:
        gates = {
            "register_provenance": GateEntry(
                required_for=["null_deref"],
                status="n/a",
                closure_basis="executor_na",
            )
        }
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="null_deref",
                managed_gates=gates,
            )
        )
        self.assertIn("预留（当前未启用）", report)

    def test_optional_gate_does_not_make_required_verification_pending(self) -> None:
        gates = {
            "register_provenance": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            ),
            "object_lifetime": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            ),
            "local_corruption_exclusion": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            ),
            "external_corruption_gate": GateEntry(
                required_for=["pointer_corruption"],
                status="open",
            ),
        }
        report = generate_markdown_report(
            _base_state(
                report_language="zh",
                current_signature_class="pointer_corruption",
                managed_gates=gates,
            )
        )
        self.assertIn("全部关键验证环节均已获得确定性证据支持", report)
        self.assertNotIn("结论适用范围", report)


class GateAuditReportTests(unittest.TestCase):
    def test_audit_report_lists_gates_and_transitions(self) -> None:
        history = [
            {
                "gate_name": "register_provenance",
                "from_status": "open",
                "to_status": "closed",
                "event": "gate_transition",
                "reason": "evidence matched",
            }
        ]
        audit = generate_gate_audit_report(
            _base_state(
                current_signature_class="null_deref",
                managed_gates=_gates(),
                gate_transition_history=history,
            )
        )
        self.assertIn("# Gate 审计记录", audit)
        self.assertIn("register_provenance", audit)
        self.assertIn("- **状态**: closed", audit)
        self.assertIn("完成条件", audit)
        self.assertIn("策略角色**: 必需门控", audit)
        self.assertIn("策略角色**: 可选门控（不阻止定论）", audit)
        self.assertIn("rd ffff0000 -> 0x0", audit)
        self.assertIn("## Gate 状态转换记录", audit)
        self.assertIn("evidence matched", audit)

    def test_audit_completion_criteria_disclose_unimplemented_extractors(self) -> None:
        gates = {
            "object_lifetime": GateEntry(
                required_for=["page_not_present"],
                status="open",
                completion_criteria=gate_completion_criteria(
                    "object_lifetime", ["page_not_present"]
                ),
            ),
            "external_corruption_gate": GateEntry(
                required_for=["pointer_corruption"],
                status="open",
                completion_criteria=gate_completion_criteria(
                    "external_corruption_gate", ["pointer_corruption"]
                ),
            ),
        }
        audit = generate_gate_audit_report(
            _base_state(managed_gates=gates, gate_transition_history=[])
        )
        self.assertIn("lifetime evidence extraction is not implemented", audit)
        self.assertIn("extraction of external_source facts is not implemented", audit)

    def test_audit_report_preserves_rejected_na_request_provenance(self) -> None:
        audit = generate_gate_audit_report(
            _base_state(
                managed_gates={
                    "object_lifetime": GateEntry(
                        required_for=["use_after_free"], status="open"
                    )
                },
                gate_transition_history=[
                    {
                        "gate_name": "object_lifetime",
                        "from_status": "open",
                        "to_status": "open",
                        "event": "llm_na_rejected",
                        "reason": "not-applicable status requires an executor-owned rule",
                        "requested_status": "n/a",
                        "requested_evidence": "Model-only claim.",
                    }
                ],
            )
        )
        self.assertNotIn("审核证据", audit)
        self.assertIn("llm_na_rejected", audit)
        self.assertIn("LLM requested status: n/a", audit)
        self.assertIn("Model-only claim.", audit)

    def test_audit_report_empty_without_gates(self) -> None:
        self.assertEqual(generate_gate_audit_report(_base_state()), "")


if __name__ == "__main__":
    unittest.main()