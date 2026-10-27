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
from src.react.state_manager import _build_evidence_goal, project_managed_analysis_step


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

        self.assertFalse(facts_support_goal(facts, {"gate_name": "object_lifetime"}))
        self.assertTrue(
            facts_support_goal(facts, {"gate_name": "local_corruption_exclusion"})
        )

    def test_kasan_uaf_report_without_address_is_telemetry_only(self) -> None:
        facts = extract_evidence_facts(
            "run_script",
            {"script": "log -m | grep -i KASAN"},
            "BUG: KASAN: use-after-free in example+0x10/0x20\n"
            "Freed by task 12:\n"
            "Allocated by task 13:\n",
        )

        self.assertIn("lifetime_signal:kasan_use_after_free_unbound", facts)
        self.assertFalse(facts_support_goal(facts, {"gate_name": "object_lifetime"}))

        gate = GateEntry(required_for=["use_after_free"], status="open")
        evaluated, _ = evaluate_gate_closures({"object_lifetime": gate}, facts)
        self.assertEqual(evaluated["object_lifetime"].status, "open")

    def test_object_lifetime_gate_requires_snapshot_or_temporal_evidence(self) -> None:
        gate = GateEntry(
            required_for=["pointer_corruption"],
            status="open",
        )
        observations = {
            "rd_word:0xffff0000=0x10",
            "struct_type:irqaction",
        }

        evaluated, _ = evaluate_gate_closures(
            {"object_lifetime": gate}, observations
        )
        self.assertEqual(evaluated["object_lifetime"].status, "open")

        evaluated, _ = evaluate_gate_closures(
            {"object_lifetime": gate},
            observations | {"kmem_slab_state:0xffff0000=allocated"},
        )
        self.assertEqual(evaluated["object_lifetime"].status, "closed")

        evaluated, _ = evaluate_gate_closures(
            {"object_lifetime": gate},
            observations | {"kmem_slab_state:0xffff0080=allocated"},
        )
        self.assertEqual(evaluated["object_lifetime"].status, "open")

        evaluated, _ = evaluate_gate_closures(
            {"object_lifetime": gate},
            observations | {"lifetime_proof:kasan_use_after_free"},
        )
        self.assertEqual(evaluated["object_lifetime"].status, "open")

    def test_page_not_present_lifetime_gate_requires_mapping_lifetime_evidence(self) -> None:
        gate = GateEntry(
            required_for=["page_not_present"],
            status="open",
        )
        facts = {
            "vtop_unmapped:0xffffb2709a1ac000",
            "rd_word:0xffffb2709a1ac000=0x1",
            "kmem_slab_state:0xffffb2709a1ac000=allocated",
        }

        evaluated, transitions = evaluate_gate_closures(
            {"object_lifetime": gate}, facts
        )

        self.assertEqual(evaluated["object_lifetime"].status, "open")
        self.assertIn(
            "the executor currently has no closure rule for this signature-specific gate; "
            "address-linked mapping teardown or lifetime evidence extraction is not implemented",
            evaluated["object_lifetime"].completion_criteria,
        )
        self.assertTrue(
            any(item["event"] == "weak_gate_closure" for item in transitions)
        )

    def test_extracts_current_kmem_slot_states(self) -> None:
        facts = extract_evidence_facts(
            "kmem",
            {"command": "kmem -S ffff0000"},
            "FREE / [ALLOCATED]\n"
            "ffff0000\n"
            "  [ffff0080]\n",
        )

        self.assertIn("kmem_slab_state:0xffff0000=free", facts)
        self.assertIn("kmem_slab_state:0xffff0080=allocated", facts)
        self.assertTrue(facts_support_goal(facts, {"gate_name": "object_lifetime"}))
        self.assertNotIn("lifetime_proof:kasan_use_after_free", facts)

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

    def test_llm_na_is_rejected_and_claim_is_audited(self) -> None:
        gate = GateEntry(
            required_for=["use_after_free"],
            status="n/a",
            evidence="The model says the gate does not apply.",
        )

        evaluated, transitions = evaluate_gate_closures(
            {"object_lifetime": gate}, []
        )

        self.assertEqual(evaluated["object_lifetime"].status, "open")
        self.assertIsNone(evaluated["object_lifetime"].closure_basis)
        rejected = [item for item in transitions if item["event"] == "llm_na_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            rejected[0]["requested_evidence"],
            "The model says the gate does not apply.",
        )

        legacy_prior = GateEntry(
            required_for=["use_after_free"],
            status="n/a",
            evidence="Unverified legacy claim.",
        )
        evaluated, _ = evaluate_gate_closures(
            {"object_lifetime": gate},
            [],
            {"object_lifetime": legacy_prior},
        )
        self.assertEqual(evaluated["object_lifetime"].status, "open")

        executor_prior = GateEntry(
            required_for=["use_after_free"],
            status="n/a",
            evidence="Executor applicability rule verified.",
            closure_basis="executor_na",
        )
        evaluated, transitions = evaluate_gate_closures(
            {"object_lifetime": gate},
            [],
            {"object_lifetime": executor_prior},
        )
        self.assertEqual(evaluated["object_lifetime"].status, "n/a")
        self.assertFalse(
            any(item["event"] == "llm_na_rejected" for item in transitions)
        )

    def test_unsupported_gate_facts_are_telemetry_not_closure(self) -> None:
        gate = GateEntry(required_for=["soft_lockup"], status="open")

        evaluated, transitions = evaluate_gate_closures(
            {"lock_holder": gate}, {"rd_word:0xffff0000=0x1"}
        )

        self.assertEqual(evaluated["lock_holder"].status, "open")
        self.assertTrue(
            any(item["event"] == "weak_gate_closure" for item in transitions)
        )

    def test_evidence_goal_skips_telemetry_only_and_optional_gates(self) -> None:
        telemetry_only = {
            "stack_integrity": GateEntry(
                required_for=["soft_lockup"], status="open"
            ),
            "lock_holder": GateEntry(required_for=["soft_lockup"], status="open"),
        }
        self.assertIsNone(_build_evidence_goal(telemetry_only, "soft_lockup"))

        optional_only = {
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
                required_for=["pointer_corruption"], status="open"
            ),
        }
        self.assertIsNone(_build_evidence_goal(optional_only, "pointer_corruption"))

    def test_external_gate_requires_an_external_source_fact(self) -> None:
        gates = {
            "local_corruption_exclusion": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            ),
            "external_corruption_gate": GateEntry(
                required_for=["pointer_corruption"], status="open"
            ),
        }

        evaluated, _ = evaluate_gate_closures(
            gates,
            {
                "rd_word:0xffff0000=0x1",
                "struct_type:device_private",
            },
            gates,
        )

        self.assertEqual(evaluated["external_corruption_gate"].status, "open")

    def test_llm_cannot_supply_executor_closure_basis(self) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 7,
                "reasoning": "The model claims the gate was verified.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "null_deref",
                "gates": {
                    "register_provenance": {
                        "required_for": ["null_deref"],
                        "status": "closed",
                        "closure_basis": "executor_evidence",
                    }
                },
            }
        )

        self.assertIsNone(step.gates["register_provenance"].closure_basis)

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

    def test_state_manager_does_not_accept_llm_na(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 5,
                "reasoning": "The model claims the lifetime gate is not applicable.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "use_after_free",
                "partial_dump": "partial",
                "gates": {
                    "object_lifetime": {
                        "required_for": ["use_after_free"],
                        "status": "n/a",
                        "evidence": "Model-only applicability claim.",
                    }
                },
            }
        )

        _, updates = project_managed_analysis_step(
            llm_step,
            {"evidence_facts": [], "gate_transition_history": []},
            original_reasoning="The model claims the lifetime gate is not applicable.",
        )

        self.assertEqual(updates["managed_gates"]["object_lifetime"].status, "open")
        self.assertIsNone(updates["managed_gates"]["object_lifetime"].evidence)
        self.assertIsNone(
            updates["managed_gates"]["object_lifetime"].closure_basis
        )
        self.assertTrue(
            any(
                item["event"] == "llm_na_rejected"
                for item in updates["gate_transition_history"]
            )
        )

    def test_unknown_signature_does_not_inherit_prior_gates(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 6,
                "reasoning": "The crash signature cannot be classified.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "unknown",
                "partial_dump": "partial",
            }
        )
        prior_gates = {
            "register_provenance": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                closure_basis="executor_evidence",
            )
        }

        _, updates = project_managed_analysis_step(
            llm_step,
            {
                "current_signature_class": "pointer_corruption",
                "managed_gates": prior_gates,
                "evidence_facts": [],
                "gate_transition_history": [],
            },
            original_reasoning="The crash signature cannot be classified.",
        )

        self.assertIsNone(updates["managed_gates"])

    def test_signature_change_does_not_reuse_prior_gate_closure(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 8,
                "reasoning": "The missing mapping is being classified separately.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "page_not_present",
                "partial_dump": "partial",
            }
        )
        prior_gates = {
            "object_lifetime": GateEntry(
                required_for=["pointer_corruption"],
                status="closed",
                evidence="Evidence for a different signature.",
                closure_basis="executor_evidence",
            )
        }

        _, updates = project_managed_analysis_step(
            llm_step,
            {
                "current_signature_class": "pointer_corruption",
                "managed_gates": prior_gates,
                "evidence_facts": [
                    "rd_word:0xffffb2709a1ac000=0x1",
                    "kmem_slab_state:0xffffb2709a1ac000=allocated",
                ],
                "gate_transition_history": [],
            },
            original_reasoning="The missing mapping is being classified separately.",
        )

        self.assertEqual(updates["managed_gates"]["object_lifetime"].status, "open")
        self.assertEqual(
            updates["managed_gates"]["object_lifetime"].required_for,
            ["page_not_present"],
        )


if __name__ == "__main__":
    unittest.main()
