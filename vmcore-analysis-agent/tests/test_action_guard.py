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

from src.react.action_guard import _validate_command_line


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


if __name__ == "__main__":
    unittest.main()
