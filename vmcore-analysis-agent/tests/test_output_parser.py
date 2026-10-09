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

from src.react.output_parser import (
    _detect_corrupted_base_null_deref,
    _parse_kernel_frame_registers,
    apply_value_conflict_audit,
)

CONFLICT_FACT = (
    "conflict:object_does_not_match_type:irqaction@0xff292187ae124a80:"
    "next@0x18=0x12,thread_fn@0x20=0x5c"
)


def _make_conclusive_step(**overrides: object):
    from src.react.schema import VMCoreLLMAnalysisStep

    payload: dict[str, object] = {
        "step_id": 22,
        "reasoning": "The irq_desc.action object looks like a valid irqaction.",
        "action": None,
        "is_conclusive": True,
        "signature_class": "pointer_corruption",
        "root_cause_class": "wild_pointer",
        "partial_dump": "partial",
        "confidence": "high",
        "final_diagnosis": {
            "crash_type": "NULL pointer dereference",
            "panic_string": "BUG: unable to handle kernel NULL pointer dereference",
            "faulting_instruction": "ffffffffbc3798a0",
            "root_cause": "A wild pointer in the irqaction chain.",
            "detailed_analysis": "The handler field is corrupted.",
            "suspect_code": {
                "file": "kernel/irq/manage.c",
                "function": "setup_irq",
                "line": "unknown",
            },
            "evidence": ["irq_desc.action points at the handler payload"],
        },
        "fix_suggestion": "Validate the irqaction pointer before use.",
    }
    payload.update(overrides)
    return VMCoreLLMAnalysisStep.model_validate(payload)

# crash `bt` 风格：RIP 为裸内核地址（失败日志 new 5.txt 的实际格式）。
CRASH_BT_FRAME = """
[exception RIP: show_interrupts+576]
RIP: ffffffffbc3798a0  RSP: ff52052b5eecbe10  RFLAGS: 00010006
RAX: 0000000000000000  RBX: ff2922871e6c1c00  RCX: 00100a00ffffff04
RDX: ff292288830d2000  RSI: 0000000000000000  RDI: ff292288830d086a
RBP: 0000000000000012   R8: 0000000000002000   R9: 0000000000000000
R10: ff292288830d2000  R11: ff292288830d0859  R12: ffffffffbd50e585
R13: 00000000000000c0  R14: ff2921879ce79000  R15: ff292187ae124a80
ORIG_RAX: ffffffffffffffff  CS: 0010  SS: 0018
"""

# dmesg Oops 风格：RIP 为段前缀 + 符号（``RIP: 0010:sym+off``），该行本身
# 无法被寄存器正则匹配；随后紧跟用户态帧，其 RIP 带 0x 前缀（``0033:0x7f...``）
# 同样无法被寄存器正则匹配。修复前：起点行匹配失败导致整帧解析为 0 个寄存器；
# 且用户态帧边界漏检，其小整数寄存器（RAX/RBX=0x6）混入内核帧，与故障地址
# 凑出多个假候选，升级被误判为"证据不足"而放弃。
DMESG_FRAME_WITH_USER_FRAME = """
BUG: unable to handle kernel NULL pointer dereference at 0000000000000062
RIP: 0010:show_interrupts+0x240/0x5a0
RSP: 0018:ffffae024a1c3d98  EFLAGS: 00010286
RAX: 0000000000000000 RBX: ffff912345678000 RCX: 0000000000000000
RDX: 0000000000000000 RSI: 0000000000000000 RDI: ffff912345671000
RBP: 0000000000000012 R08: 0000000000002000 R09: 0000000000000000
R10: 0000000000000000 R11: 0000000000000000 R12: ffffffffa1234567
RIP: 0033:0x7f8a12345678  RSP: 002b:00007ffd12340000 EFLAGS: 00000246
RAX: 0000000000000006 RBX: 0000000000000006 RCX: 00007f8a12345678
"""


class KernelFrameRegisterParsingTests(unittest.TestCase):
    def test_crash_bt_bare_hex_rip(self) -> None:
        regs = _parse_kernel_frame_registers(CRASH_BT_FRAME)
        self.assertEqual(regs.get("RBP"), [0x12])
        self.assertIn("R15", regs)

    def test_dmesg_symbol_rip_still_collects_registers(self) -> None:
        """回归：RIP 为 ``0010:sym+off`` 时起点行无法匹配寄存器，
        修复前整帧解析为 0 个寄存器，损坏基址升级被静默禁用。"""
        regs = _parse_kernel_frame_registers(DMESG_FRAME_WITH_USER_FRAME)
        self.assertEqual(regs.get("RBP"), [0x12])
        self.assertGreater(len(regs), 10)

    def test_user_frame_excluded_despite_unparseable_rip(self) -> None:
        """回归：用户态帧 ``RIP: 0033:0x7f...`` 无法被寄存器正则匹配，
        修复前帧边界漏检，用户态小整数寄存器混入内核帧。"""
        regs = _parse_kernel_frame_registers(DMESG_FRAME_WITH_USER_FRAME)
        self.assertNotIn(0x6, regs.get("RBX", []))
        self.assertNotIn(0x6, regs.get("RAX", []))

    def test_dmesg_format_escalates_corrupted_base(self) -> None:
        det = _detect_corrupted_base_null_deref(DMESG_FRAME_WITH_USER_FRAME)
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det["register"], "RBP")
        self.assertEqual(det["register_value"], 0x12)
        self.assertEqual(det["fault_addr"], 0x62)
        self.assertEqual(det["offset"], 0x50)

    def test_unparseable_frame_returns_empty(self) -> None:
        """定位到内核帧但完全解析不出寄存器时返回空字典（调用方放弃升级）。"""
        text = (
            "BUG: unable to handle kernel NULL pointer dereference at 0000000000000062\n"
            "RIP: 0010:show_interrupts+0x240/0x5a0\n"
            "Code: 48 8b 95 50 00 00 00 48 89 d8 <48> 8b 92 50 00 00 00\n"
        )
        self.assertEqual(_parse_kernel_frame_registers(text), {})


class ValueConflictAuditTests(unittest.TestCase):
    """[阶段 5] 取值级矛盾审计：已读内存与结构体布局不相容时的降级与提示注入。"""

    def test_downgrades_conclusive_step(self) -> None:
        step = _make_conclusive_step()
        audited = apply_value_conflict_audit(
            step, {"value_conflicts": [CONFLICT_FACT]}, log_prefix="test"
        )

        self.assertFalse(audited.is_conclusive)
        self.assertIsNone(audited.final_diagnosis)
        self.assertIsNone(audited.fix_suggestion)
        self.assertEqual(audited.root_cause_class, "unknown")
        self.assertEqual(audited.confidence, "low")
        self.assertIn("value-level contradiction", audited.reasoning)
        self.assertIn("next@0x18=0x12", audited.additional_notes)

    def test_noop_when_no_conflicts(self) -> None:
        step = _make_conclusive_step()
        audited = apply_value_conflict_audit(step, {"value_conflicts": []}, log_prefix="test")

        self.assertTrue(audited.is_conclusive)
        self.assertIsNotNone(audited.final_diagnosis)
        self.assertEqual(audited.root_cause_class, "wild_pointer")
        self.assertEqual(audited.confidence, "high")

    def test_idempotent_when_model_already_referenced_the_object(self) -> None:
        """模型已自行写出该对象基址时不再注入审计说明（避免重复唠叨）。"""
        step = _make_conclusive_step(
            reasoning="irq_desc.action at 0xff292187ae124a80 does not look like an irqaction."
        )
        audited = apply_value_conflict_audit(
            step, {"value_conflicts": [CONFLICT_FACT]}, log_prefix="test"
        )

        self.assertTrue(audited.is_conclusive)
        self.assertEqual(audited.confidence, "high")
        self.assertNotIn("value-level contradiction", audited.reasoning)


if __name__ == "__main__":
    unittest.main()
