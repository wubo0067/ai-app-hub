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

from src.react.evidence import extract_evidence_facts, facts_support_goal
from src.react.schema import GateEntry
from src.react.evidence import update_gate_evidence


class EvidenceExtractionTests(unittest.TestCase):
    def test_extracts_facts_from_supported_commands(self) -> None:
        rd_facts = extract_evidence_facts(
            "rd",
            {"command": "rd -x ffff0000 2"},
            "ffff0000: 0000000000000010 0000000000000020  ........",
        )
        struct_facts = extract_evidence_facts(
            "struct",
            {"command": "struct irqaction ffff0000"},
            "struct irqaction {\n [0] handler\n [16] dev_id\n SIZE: 32\n}",
        )
        dis_facts = extract_evidence_facts(
            "dis",
            {"command": "dis -rl ffff0000"},
            "0xffff0000 <foo>: mov %rax,(%rbx)",
        )
        sym_facts = extract_evidence_facts(
            "sym",
            {"command": "sym -m nvidia"},
            "ffffffff81000000 T irq_handler",
        )

        self.assertIn("rd_word:0xffff0000=0x10", rd_facts)
        self.assertIn("struct_field:irqaction.handler@0x0", struct_facts)
        self.assertIn("struct_size:irqaction=0x20", struct_facts)
        self.assertIn("dis_instruction:0xffff0000=mov", dis_facts)
        self.assertIn("sym:irq_handler@0xffffffff81000000:T", sym_facts)

    def test_run_script_collects_each_supported_command(self) -> None:
        facts = extract_evidence_facts(
            "run_script",
            {
                "script": (
                    "rd -x ffff0000 1\n"
                    "struct foo ffff0000\n"
                    "dis -rl ffff0000\n"
                    "sym -m nvidia"
                )
            },
            "ffff0000: 0000000000000001\n"
            "struct foo {\n [0] value\n SIZE: 8\n}\n"
            "0xffff0000 <foo>: ret\n"
            "ffffffff81000000 T irq_handler\n",
        )

        self.assertTrue(any(fact.startswith("rd_word:") for fact in facts))
        self.assertTrue(any(fact.startswith("struct_") for fact in facts))
        self.assertTrue(any(fact.startswith("dis_") for fact in facts))
        self.assertTrue(any(fact.startswith("sym:") for fact in facts))

    def test_gate_progress_uses_fact_set_difference(self) -> None:
        prior = {"rd_word:0xffff0000=0x10"}
        current = prior | {"struct_field:irqaction.handler@0x0"}
        delta = current - prior
        goal = {"gate_name": "field_type_classification"}
        gates = {
            "field_type_classification": GateEntry(
                required_for=["pointer_corruption"], status="open"
            )
        }
        updated = update_gate_evidence(gates, delta)

        self.assertEqual(delta, {"struct_field:irqaction.handler@0x0"})
        self.assertTrue(facts_support_goal(delta, goal))
        self.assertIn("[evidence-delta] struct_field:irqaction.handler@0x0", updated["field_type_classification"].evidence)
        self.assertEqual(updated["field_type_classification"].status, "open")


if __name__ == "__main__":
    unittest.main()