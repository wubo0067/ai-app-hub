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

from langchain_core.messages import HumanMessage, ToolMessage

from src.react.nodes import (
    CONVERGENCE_GUARD_STREAK_THRESHOLD,
    _convergence_guard_error,
    _gates_all_closed,
    _only_read_only_value_probes,
)
from src.react.output_parser import (
    _mine_faulting_instruction,
    _mine_suspect_function,
    apply_executor_consistency_audit,
    apply_fallback_conclusion_synthesis,
)
from src.react.schema import GateEntry, VMCoreAnalysisStep

CLOSED_GATES = {
    name: GateEntry(
        status="closed",
        evidence="rd+dis+struct",
        required_for=["pointer_corruption"],
    )
    for name in (
        "register_provenance",
        "object_lifetime",
        "local_corruption_exclusion",
    )
}
OPEN_GATES = {
    **CLOSED_GATES,
    "object_lifetime": GateEntry(
        status="open", evidence="pending", required_for=["pointer_corruption"]
    ),
}

# run D 的实际形态：所有动作都被重编码成 run_script，脚本里是 crash 命令行。
RUN_D_PROBE = ["rd -8 ff292187ae124a80 16", "struct irqaction ff292187ae124a80"]

CRASH_DIS_OUTPUT = """crash> dis -rl show_interrupts
0xffffffff81a24240 <show_interrupts+640>: mov 0x20(%rdi),%rax
0xffffffff81a24244 <show_interrupts+644>: test %rax,%rax
crash> bt
RIP: 0010:show_interrupts+0x240/0x5a0
RIP: ffffffff81a24240  RSP: ff52052b5eecbe10
Kernel panic - not syncing: Fatal exception
"""


def _converged_state(**overrides: object) -> dict:
    state = {
        "report_language": "eng",
        "current_root_cause_class": "pointer_corruption",
        "managed_gates": CLOSED_GATES,
        "no_progress_streak": CONVERGENCE_GUARD_STREAK_THRESHOLD,
        "evidence_facts": ["rd_word:0xff292187ae124a80=0x6b6b6b6b6b6b6b6b"],
        "messages": [HumanMessage(content="analyze"), ToolMessage(content=CRASH_DIS_OUTPUT, tool_call_id="t1", name="run_script")],
    }
    state.update(overrides)
    return state


class ConvergenceGuardTests(unittest.TestCase):
    """L2：结论已成立时掐断只读取值类探测，避免收尾轮被探测占掉。"""

    def test_blocks_read_only_probe_when_converged(self) -> None:
        reason = _convergence_guard_error(_converged_state(), RUN_D_PROBE)
        self.assertIsNotNone(reason)
        self.assertIn("pointer_corruption", reason)
        self.assertIn("Emit the final JSON conclusion now", reason)

    def test_allows_probe_below_streak_threshold(self) -> None:
        state = _converged_state(no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD - 1)
        self.assertIsNone(_convergence_guard_error(state, RUN_D_PROBE))

    def test_allows_probe_without_root_cause_class(self) -> None:
        state = _converged_state(current_root_cause_class=None)
        self.assertIsNone(_convergence_guard_error(state, RUN_D_PROBE))

    def test_allows_probe_with_open_gate(self) -> None:
        state = _converged_state(managed_gates=OPEN_GATES)
        self.assertIsNone(_convergence_guard_error(state, RUN_D_PROBE))

    def test_allows_probe_with_unregistered_gates(self) -> None:
        """门控尚未注册（None/{}）不等于"全部关闭"，不得掐断取证。"""
        for unregistered in (None, {}):
            with self.subTest(managed_gates=unregistered):
                state = _converged_state(managed_gates=unregistered)
                self.assertIsNone(_convergence_guard_error(state, RUN_D_PROBE))

    def test_allows_evidence_changing_commands(self) -> None:
        """非取值类命令（加载符号、回溯）仍可执行，收尾轮报告需要它们。"""
        for lines in (
            ["mod -s mpt3sas"],
            ["bt"],
            ["sys"],
            ["rd ff292187ae124a80 4", "mod -s mpt3sas"],
        ):
            with self.subTest(lines=lines):
                self.assertIsNone(_convergence_guard_error(_converged_state(), lines))

    def test_allows_empty_command_lines(self) -> None:
        self.assertIsNone(_convergence_guard_error(_converged_state(), []))
        self.assertIsNone(_convergence_guard_error(_converged_state(), ["# comment"]))

    def test_probe_classification_ignores_crash_prompt_and_case(self) -> None:
        self.assertTrue(
            _only_read_only_value_probes(["crash> RD 0x1 4", "  STRUCT irqaction 0x2  "])
        )
        self.assertFalse(_only_read_only_value_probes(["quit"]))


class GateClosureHelperTests(unittest.TestCase):
    def test_unregistered_gates_are_not_closed(self) -> None:
        for unregistered in (None, {}):
            with self.subTest(gates=unregistered):
                self.assertFalse(_gates_all_closed(unregistered))

    def test_open_gate_blocks(self) -> None:
        self.assertFalse(_gates_all_closed(OPEN_GATES))

    def test_dict_shaped_gates_are_validated(self) -> None:
        """state 里的门控可能是序列化后的 dict，而非 GateEntry 实例。"""
        as_dict = {
            name: {"status": "closed", "required_for": ["pointer_corruption"]}
            for name in CLOSED_GATES
        }
        self.assertTrue(_gates_all_closed(as_dict))


class FallbackSynthesisTests(unittest.TestCase):
    """L3：收口轮模型未输出终止结论时，执行器合成有界结论而非空转。"""

    def _step(self, **overrides: object) -> VMCoreAnalysisStep:
        payload: dict[str, object] = {
            "step_id": 12,
            "reasoning": "All mandatory gates closed but the model kept probing.",
            "action": None,
            "is_conclusive": False,
            "signature_class": "pointer_corruption",
            "root_cause_class": "pointer_corruption",
            "corruption_mechanism": "write_corruption",
            "gates": CLOSED_GATES,
            "confidence": "low",
        }
        payload.update(overrides)
        return VMCoreAnalysisStep.model_validate(payload)

    def test_synthesizes_conclusion_from_closed_gates(self) -> None:
        out = apply_fallback_conclusion_synthesis(self._step(), _converged_state())
        self.assertTrue(out.is_conclusive)
        self.assertIsNone(out.action)
        self.assertEqual(out.confidence, "low")
        diagnosis = out.final_diagnosis
        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.suspect_code.function, "show_interrupts")
        self.assertIn("show_interrupts+640", diagnosis.faulting_instruction)
        self.assertTrue(any("gate register_provenance" in item for item in diagnosis.evidence))
        self.assertIn("Executor fallback", out.additional_notes)

    def test_synthesized_result_survives_message_revalidation(self) -> None:
        """报告侧重新校验序列化 JSON，合成结果必须自洽否则又被降级。"""
        out = apply_fallback_conclusion_synthesis(self._step(), _converged_state())
        revalidated = VMCoreAnalysisStep.model_validate_json(out.model_dump_json())
        self.assertTrue(revalidated.is_conclusive)
        self.assertIsNotNone(revalidated.final_diagnosis)

    def test_skipped_when_gate_unresolved(self) -> None:
        out = apply_fallback_conclusion_synthesis(
            self._step(gates=OPEN_GATES), _converged_state()
        )
        self.assertFalse(out.is_conclusive)
        self.assertIsNone(out.final_diagnosis)

    def test_skipped_without_root_cause_class(self) -> None:
        out = apply_fallback_conclusion_synthesis(
            self._step(root_cause_class=None), _converged_state()
        )
        self.assertFalse(out.is_conclusive)
        self.assertIsNone(out.final_diagnosis)

    def test_idempotent_when_already_conclusive(self) -> None:
        once = apply_fallback_conclusion_synthesis(self._step(), _converged_state())
        self.assertTrue(once.is_conclusive)
        self.assertIs(apply_fallback_conclusion_synthesis(once, _converged_state()), once)

    def test_chinese_report_language(self) -> None:
        out = apply_fallback_conclusion_synthesis(
            self._step(), _converged_state(report_language="zh")
        )
        self.assertIn("执行器补全", out.additional_notes)


class DisassemblyMiningTests(unittest.TestCase):
    """crash `dis` 的符号偏移是十进制且不带 0x，不能只靠 RIP 正则。"""

    def test_mine_faulting_instruction_from_dis_symbol_line(self) -> None:
        mined = _mine_faulting_instruction(CRASH_DIS_OUTPUT)
        self.assertEqual(
            mined, "0xffffffff81a24240 <show_interrupts+640>: mov 0x20(%rdi),%rax"
        )

    def test_mine_suspect_function_prefers_instruction_symbol(self) -> None:
        instruction = _mine_faulting_instruction(CRASH_DIS_OUTPUT)
        self.assertEqual(
            _mine_suspect_function(CRASH_DIS_OUTPUT, instruction, {}), "show_interrupts"
        )

    def test_mine_suspect_function_falls_back_to_evidence_fact(self) -> None:
        self.assertEqual(
            _mine_suspect_function("", "", {"evidence_facts": ["dis_symbol:foo_bar+0x10"]}),
            "foo_bar",
        )

    def test_mine_faulting_instruction_without_rip(self) -> None:
        self.assertEqual(_mine_faulting_instruction("no rip here"), "")


class SlabLifetimeAuditTests(unittest.TestCase):
    """当前 slab 状态和字节形态不能单独判定对象的历史生命周期。"""

    DENSE_DUMP = "\n".join(
        ["crash> rd -8 ff292187ae124a80 16"]
        + [f"  {i:016x}" for i in range(1, 9)]
        + ["0000000000000000"] * 8
    )

    def _step(self):
        from src.react.schema import VMCoreLLMAnalysisStep

        return VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 5,
                "reasoning": (
                    "The slot has a dense non-zero prefix and zero tail; "
                    "stale-pointer UAF with reuse remains possible."
                ),
                "action": {"command_name": "rd", "arguments": ["ff292187ae124a80", "16"]},
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "use_after_free",
                "partial_dump": "partial",
                "confidence": "low",
            }
        )

    def test_allocated_slot_and_prefix_shape_do_not_demote_uaf(self) -> None:
        state = {
            "messages": [
                ToolMessage(
                    content=(
                        "crash> kmem -S ff292187ae124a80\n"
                        "FREE / [ALLOCATED]\n[ff292187ae124a80]"
                    ),
                    tool_call_id="t1",
                    name="kmem",
                ),
                ToolMessage(
                    content=self.DENSE_DUMP,
                    tool_call_id="t2",
                    name="rd",
                ),
            ]
        }

        out = apply_executor_consistency_audit(self._step(), state)

        self.assertEqual(out.root_cause_class, "use_after_free")
        self.assertNotIn("live-slot overwrite", out.reasoning)
        self.assertNotIn("prefix-overwrite signature", out.reasoning)
        self.assertNotIn("Executor audit:", out.reasoning)
        self.assertIsNone(out.additional_notes)


if __name__ == "__main__":
    unittest.main()
