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

from src.react.consistency import (
    MemoryRead,
    detect_value_conflicts,
    format_conflict_fact,
    parse_conflict_fact,
    parse_memory_reads,
    parse_struct_layouts,
)

# crash `struct -o irq_desc` 的真实形态：偏移为十进制，字段名在声明之后，
# 布局块以 `}` 结束，`SIZE:` 行紧随其后。
IRQ_DESC_LAYOUT = """struct irq_desc {
  [0] struct irq_common_data irq_common_data;
  [112] struct irqaction *action;
  [120] unsigned int status_use_accessors;
  [128] unsigned int depth;
  SIZE: 448
}
"""

IRQACTION_LAYOUT = """struct irqaction {
  [0] irq_handler_t handler;
  [8] void *dev_id;
  [16] void *percpu_dev_id;
  [24] struct irqaction *next;
  [32] irq_handler_t thread_fn;
  [40] struct task_struct *thread;
  [48] struct irqaction *secondary;
  [56] unsigned int irq;
  [60] unsigned int flags;
  [80] const char *name;
  SIZE: 128
}
"""


class StructLayoutParsingTests(unittest.TestCase):
    def test_declaration_form_with_size_after_close(self) -> None:
        layouts = parse_struct_layouts(IRQ_DESC_LAYOUT)
        self.assertEqual(layouts["irq_desc"]["size"], 448)
        fields = {f["name"]: f for f in layouts["irq_desc"]["fields"]}
        self.assertEqual(fields["action"]["offset"], 112)
        self.assertTrue(fields["action"]["pointer"])
        self.assertFalse(fields["depth"]["pointer"])

    def test_legacy_name_only_form_with_size_before_close(self) -> None:
        """简化格式（`[0] handler` + `SIZE` 在 `}` 之前）同样被支持。"""
        layouts = parse_struct_layouts(
            "struct irqaction {\n [0] handler\n [16] dev_id\n SIZE: 32\n}"
        )
        self.assertEqual(layouts["irqaction"]["size"], 32)
        self.assertEqual(
            [f["name"] for f in layouts["irqaction"]["fields"]], ["handler", "dev_id"]
        )

    def test_function_pointer_field_name_comes_from_parens(self) -> None:
        layouts = parse_struct_layouts(
            "struct irq_chip {\n"
            "  [80] unsigned int (*irq_set_type)(struct irq_data *, unsigned int);\n"
            "  SIZE: 128\n"
            "}\n"
        )
        fields = layouts["irq_chip"]["fields"]
        self.assertEqual(fields[0]["name"], "irq_set_type")
        self.assertTrue(fields[0]["pointer"])

    def test_instance_dump_is_ignored(self) -> None:
        """`struct task_struct <addr>` 的 `field = value,` 转储不是布局，必须忽略。"""
        layouts = parse_struct_layouts(
            "struct task_struct ffff912345678000\n"
            "  pid = 1,\n"
            "  comm = \"systemd\",\n"
        )
        self.assertEqual(layouts, {})

    def test_void_pointer_cookie_does_not_count_as_pointer(self) -> None:
        layouts = parse_struct_layouts(
            "struct holder {\n"
            "  [0] void *cookie;\n"
            "  [8] struct foo *ptr;\n"
            "  [16] int (*fn)(void);\n"
            "  SIZE: 32\n"
            "}\n"
        )
        fields = {f["name"]: f for f in layouts["holder"]["fields"]}
        self.assertTrue(fields["cookie"]["void_pointer"])
        self.assertFalse(fields["ptr"]["void_pointer"])

    def test_best_block_wins_when_type_appears_twice(self) -> None:
        layouts = parse_struct_layouts(
            "struct foo {\n  [0] int a;\n  SIZE: 16\n}\n"
            "struct foo {\n  [0] int a;\n  [8] int b;\n  SIZE: 16\n}\n"
        )
        self.assertEqual(len(layouts["foo"]["fields"]), 2)


class MemoryReadParsingTests(unittest.TestCase):
    def test_contiguous_lines_are_coalesced(self) -> None:
        """crash 每行 2 个字，必须合并为一次快照才能与 SIZE 比较。"""
        reads = parse_memory_reads(
            "ff292187ae124a80:  0010000904060001 0000000000000000\n"
            "ff292187ae124a90:  0f00000500000000 0000000000000012\n"
            "ff292187ae124aa0:  000000000000005c 0000000000000000\n"
        )
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0].address, 0xFF292187AE124A80)
        self.assertEqual(len(reads[0].words), 6)

    def test_address_discontinuity_splits_snapshots(self) -> None:
        """两次独立 rd 即使紧邻也不能被拼接成一个对象。"""
        reads = parse_memory_reads(
            "ffff0000:  0000000000000010 0000000000000020\n"
            "ffff1000:  0000000000000030 0000000000000040\n"
        )
        self.assertEqual([r.address for r in reads], [0xFFFF0000, 0xFFFF1000])

    def test_trailing_ascii_column_is_not_parsed_as_words(self) -> None:
        reads = parse_memory_reads(
            "ff2921879ce79000:  0000000000000020 0000000000000000  ........\n"
        )
        self.assertEqual(reads[0].words, (0x20, 0x0))


class ValueConflictDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layouts = parse_struct_layouts(IRQ_DESC_LAYOUT + IRQACTION_LAYOUT)

    def test_offending_object_is_detected(self) -> None:
        """真实故障现场：irqaction 的 next@0x18=0x12 与 thread_fn@0x20=0x5c
        都不是合法指针，内容与 irqaction 类型不相容。"""
        reads = parse_memory_reads(
            "ff292187ae124a80:  0010000904060001 0000000000000000\n"
            "ff292187ae124a90:  0f00000500000000 0000000000000012\n"
            "ff292187ae124aa0:  000000000000005c 0000000000000000\n"
            "ff292187ae124ab0:  0000000000000000 0000000000000000\n"
            "ff292187ae124ac0:  0000000000000000 0000000000000000\n"
            "ff292187ae124ad0:  0000000000000000 0000000000000000\n"
            "ff292187ae124ae0:  0000000000000000 0000000000000000\n"
            "ff292187ae124af0:  0000000000000000 0000000000000000\n"
        )
        conflicts = detect_value_conflicts(reads, self.layouts)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("conflict:object_does_not_match_type:irqaction@0xff292187ae124a80", conflicts[0])
        self.assertIn("next@0x18=0x12", conflicts[0])
        self.assertIn("thread_fn@0x20=0x5c", conflicts[0])

    def test_single_offending_pointer_is_not_enough(self) -> None:
        """单个小值字段不足以判定类型不符（可能是合法的小整数）。"""
        reads = [MemoryRead(address=0xFF00, words=(0x0, 0x0, 0x0, 0x12, 0xFFFF0000, 0x0))]
        self.assertEqual(detect_value_conflicts(reads, self.layouts), [])

    def test_read_shorter_than_layout_is_skipped(self) -> None:
        """读取未覆盖完整对象时不能判定，避免把邻接对象的内容算进来。"""
        reads = [MemoryRead(address=0xFF00, words=(0x0, 0x0))]
        self.assertEqual(detect_value_conflicts(reads, self.layouts), [])

    def test_layout_without_size_is_skipped(self) -> None:
        layouts = {"irqaction": {"size": None, "fields": []}}
        reads = [MemoryRead(address=0xFF00, words=(0x0, 0x0, 0x0, 0x12, 0x5C, 0x0))]
        self.assertEqual(detect_value_conflicts(reads, layouts), [])


class ConflictFactRenderingTests(unittest.TestCase):
    FACT = (
        "conflict:object_does_not_match_type:irqaction@0xff292187ae124a80:"
        "next@0x18=0x12,thread_fn@0x20=0x5c"
    )

    def test_parse_round_trip(self) -> None:
        parsed = parse_conflict_fact(self.FACT)
        self.assertEqual(parsed, ("irqaction", 0xFF292187AE124A80))

    def test_parse_rejects_other_facts(self) -> None:
        self.assertIsNone(parse_conflict_fact("oob:chain:0xff00->0x12"))
        self.assertIsNone(parse_conflict_fact("conflict:mismatch:rw"))

    def test_format_is_human_readable(self) -> None:
        rendered = format_conflict_fact(self.FACT)
        assert rendered is not None
        self.assertIn("irqaction", rendered)
        self.assertIn("0xff292187ae124a80", rendered)
        self.assertIn("next@0x18=0x12", rendered)

    def test_conflict_fact_does_not_close_a_gate(self) -> None:
        """矛盾事实是"待澄清的观察"，不能用于闭合门控。"""
        from src.react.evidence import evaluate_gate_closures
        from src.react.schema import GateEntry

        gates = {
            "register_provenance": GateEntry(
                required_for=["pointer_corruption"], status="open"
            )
        }
        facts = {self.FACT}

        evaluated, _ = evaluate_gate_closures(gates, facts)

        self.assertEqual(evaluated["register_provenance"].status, "open")


if __name__ == "__main__":
    unittest.main()
