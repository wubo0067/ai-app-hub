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

from src.react.evidence import (
    evaluate_gate_closures,
    extract_evidence_facts,
    facts_support_goal,
    gate_completion_criteria,
)
from src.react.schema import GateEntry
from src.react.evidence import update_gate_evidence
from src.react.schema import VMCoreLLMAnalysisStep
from src.react.state_manager import project_managed_analysis_step


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

    def test_direct_tools_extract_facts_from_argument_only_payloads(self) -> None:
        rd_facts = extract_evidence_facts(
            "rd",
            {"command": "-x ffffffffc09e7a89 2"},
            "crash> rd -x ffffffffc09e7a89 2\n"
            "ffffffffc09e7a89: 63000000000a7325 6d6f72665f79706f",
        )
        dis_facts = extract_evidence_facts(
            "dis",
            {"command": "-l mxlog_vprintf 2"},
            "crash> dis -l mxlog_vprintf 2\n"
            "0xffffffffc08de97c <mxlog_vprintf+60>: mov (%r9),%rax",
        )

        self.assertIn("rd_word:0xffffffffc09e7a89=0x63000000000a7325", rd_facts)
        self.assertIn("dis_instruction:0xffffffffc08de97c=mov", dis_facts)

    def test_extracts_mapping_and_vmalloc_facts(self) -> None:
        vtop_facts = extract_evidence_facts(
            "vtop",
            {"command": "ffffb2709a1ac000"},
            "ffffb2709a1ac000  (not mapped)\nPTE: 118ea7d60 => 0",
        )
        kmem_facts = extract_evidence_facts(
            "kmem",
            {"command": "-v"},
            "ffff959a401549a0 ffff959a40003fc0 "
            "ffffb27080000000 - ffffb27080002000     8192",
        )

        self.assertIn("vtop_unmapped:0xffffb2709a1ac000", vtop_facts)
        self.assertIn("vtop_pte:0x0", vtop_facts)
        self.assertIn(
            "kmem_vmap_range:0xffffb27080000000-0xffffb27080002000=0x2000",
            kmem_facts,
        )

    def test_mapping_facts_support_lifetime_and_local_exclusion(self) -> None:
        facts = extract_evidence_facts(
            "vtop",
            {"command": "ffffb2709a1ac000"},
            "ffffb2709a1ac000  (not mapped)",
        )

        self.assertTrue(facts_support_goal(facts, {"gate_name": "object_lifetime"}))
        self.assertTrue(
            facts_support_goal(facts, {"gate_name": "local_corruption_exclusion"})
        )

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
        self.assertIn(
            "[evidence-delta] struct_field:irqaction.handler@0x0",
            updated["field_type_classification"].evidence,
        )
        self.assertEqual(updated["field_type_classification"].status, "open")

    def test_llm_close_is_rejected_without_completion_evidence(self) -> None:
        gates = {
            "register_provenance": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                evidence="LLM claimed the register chain is complete.",
            )
        }

        evaluated, transitions = evaluate_gate_closures(gates, [])

        self.assertEqual(evaluated["register_provenance"].status, "open")
        self.assertTrue(
            any(item["event"] == "llm_close_rejected" for item in transitions)
        )
        self.assertEqual(len(evaluated["register_provenance"].completion_criteria), 3)

    def test_evaluator_closes_gate_and_records_transition(self) -> None:
        gates = {
            "register_provenance": GateEntry(
                required_for=["pointer_corruption"], status="open"
            )
        }
        facts = {
            "rd_word:0xffff0000=0x10",
            "dis_instruction:0xffff1000=mov",
        }

        evaluated, transitions = evaluate_gate_closures(gates, facts, gates)

        self.assertEqual(evaluated["register_provenance"].status, "closed")
        self.assertEqual(
            evaluated["register_provenance"].completion_criteria,
            gate_completion_criteria("register_provenance"),
        )
        self.assertTrue(any(item["event"] == "gate_transition" for item in transitions))

    def test_state_manager_does_not_accept_llm_gate_closure(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 5,
                "reasoning": "The model claims register provenance is closed.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "partial_dump": "partial",
                "gates": {
                    "register_provenance": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "evidence": "model claim only",
                    }
                },
            }
        )

        _, updates = project_managed_analysis_step(
            llm_step,
            {"evidence_facts": [], "gate_transition_history": []},
            original_reasoning="The model claims register provenance is closed.",
        )

        self.assertEqual(updates["managed_gates"]["register_provenance"].status, "open")
        self.assertTrue(
            any(
                item["event"] == "llm_close_rejected"
                for item in updates["gate_transition_history"]
            )
        )


if __name__ == "__main__":
    unittest.main()
