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
    def test_struct_dot_syntax_enforces_base_type_layout_cache(self) -> None:
        layout_cache = {
            "device": {
                "size": 0x40,
                "field_offsets": [0x0, 0x8, 0x10],
            }
        }
        # Offset 0x50 exceeds size 0x40 -> must be rejected even when using device.driver dot syntax
        err = validate_tool_call_request(
            "struct",
            {"command": "struct device.driver ffff888100000000"},
            observed_struct_offsets=[0x50],
            struct_layout_cache=layout_cache,
        )
        self.assertIsNotNone(err)
        self.assertIn("struct type device is too small", err or "")

        # Observed offset 0x8 is covered -> dot syntax is allowed
        ok = validate_tool_call_request(
            "struct",
            {"command": "struct device.driver,p ffff888100000000"},
            observed_struct_offsets=[0x8],
            struct_layout_cache=layout_cache,
        )
        self.assertIsNone(ok)

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


if __name__ == "__main__":
    unittest.main()
