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
)

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


if __name__ == "__main__":
    unittest.main()
