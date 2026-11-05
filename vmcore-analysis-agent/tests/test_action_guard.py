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

from src.react.action_guard import _validate_command_line, validate_tool_call_request


class KmemCommandGuardTests(unittest.TestCase):
    def test_kmem_v_requires_concrete_grep_filter(self) -> None:
        self.assertIsNotNone(_validate_command_line("kmem", allow_bt_a=False))
        self.assertIsNotNone(_validate_command_line("kmem -v", allow_bt_a=False))
        self.assertIsNotNone(_validate_command_line("kmem -v | grep", allow_bt_a=False))
        self.assertIsNotNone(
            _validate_command_line("kmem -v | grep -i", allow_bt_a=False)
        )
        self.assertIsNotNone(
            _validate_command_line("kmem -v | head -20", allow_bt_a=False)
        )
        # pipe 后无任何内容，不应 IndexError
        self.assertIsNotNone(_validate_command_line("kmem -v |", allow_bt_a=False))

    def test_kmem_v_accepts_filtered_and_targeted_forms(self) -> None:
        self.assertIsNone(
            _validate_command_line("kmem -v | grep -i 9a1ac", allow_bt_a=False)
        )
        self.assertIsNone(_validate_command_line("kmem -S ffff0000", allow_bt_a=False))


class StructDotSyntaxGuardTests(unittest.TestCase):
    def test_struct_instance_query_ignores_unrelated_global_offsets(self) -> None:
        layout_cache = {
            "device": {
                "size": 0x40,
                "field_offsets": [0x0, 0x8, 0x10],
            },
            "irqaction": {
                "size": 0x80,
                "field_offsets": [0x0, 0x18, 0x20, 0x50],
            },
        }
        # 0x50 来自别的 crash-path 基址寄存器，不能据此拒绝 device 实例查询。
        ok = validate_tool_call_request(
            "struct",
            {"command": "struct device.driver ffff888100000000"},
            observed_struct_offsets=[0x50],
            struct_layout_cache=layout_cache,
        )
        self.assertIsNone(ok)

        # show_interrupts 同时出现 irq_desc 的 0x1b0 与 irqaction 的 0x50；
        # 全局最大偏移 0x1b0 不能把合法的 irqaction 查询误杀。
        ok = validate_tool_call_request(
            "struct",
            {"command": "struct irqaction ff292187ae124a80"},
            observed_struct_offsets=[0x18, 0x50, 0x1b0],
            struct_layout_cache=layout_cache,
        )
        self.assertIsNone(ok)

    def test_struct_layout_and_instance_in_one_call_still_rejected(self) -> None:
        err = validate_tool_call_request(
            "run_script",
            {"script": "struct -o device\nstruct device ffff888100000000"},
            observed_struct_offsets=[0x8],
            struct_layout_cache={},
        )
        self.assertIsNotNone(err)
        self.assertIn("cannot be combined with first-time struct -o", err or "")

    def test_struct_minus_o_rejects_dot_syntax(self) -> None:
        err = _validate_command_line("struct -o device.driver", allow_bt_a=False)
        self.assertIsNotNone(err)
        self.assertIn("struct -o <type>", err or "")

    def test_bt_f_rejects_numeric_pid_or_frame_and_accepts_hex_task_addr(self) -> None:
        err = _validate_command_line("bt -f 1234", allow_bt_a=False)
        self.assertIsNotNone(err)
        self.assertIn("not a numeric PID or frame number", err or "")
        self.assertIsNone(
            _validate_command_line("bt -f ffff888100000000", allow_bt_a=False)
        )

    def test_bounded_search_options_are_not_address_arithmetic(self) -> None:
        # search -s/-e 是合法的 bounded scan；不能把 -e 当成减法。
        self.assertIsNone(
            _validate_command_line(
                "search -s ff292187ae124000 -e ff292187ae125fff "
                "ffffffffc051f1e0",
                allow_bt_a=False,
            )
        )
        self.assertIsNone(
            _validate_command_line(
                "search -s 0x100 -e 0x200 0xdead", allow_bt_a=False
            )
        )

    def test_inline_address_arithmetic_is_still_rejected(self) -> None:
        err = _validate_command_line(
            "rd -x ff292187ae124a80+0x10 8", allow_bt_a=False
        )
        self.assertIsNotNone(err)
        self.assertIn("address arithmetic must be resolved", err or "")


if __name__ == "__main__":
    unittest.main()
