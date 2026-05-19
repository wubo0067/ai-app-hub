import unittest
from pathlib import Path
import sys
import types

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.schema import FinalDiagnosis, SuspectCode, VMCoreLLMAnalysisStep
from src.react.state_manager import project_managed_analysis_step


class StateManagerTests(unittest.TestCase):
    def test_projects_managed_fields_from_minimal_llm_step(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 2,
                "reasoning": "pointer corruption remains the strongest explanation.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["dis -rl ffffffffc051a3c4"],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        step, updates = project_managed_analysis_step(
            llm_step,
            {},
            original_reasoning="[PARTIAL DUMP] pointer corruption remains the strongest explanation.",
        )

        self.assertEqual(step.signature_class, "pointer_corruption")
        self.assertEqual(step.partial_dump, "partial")
        self.assertEqual(step.active_hypotheses[0].status, "leading")
        self.assertEqual(step.active_hypotheses[0].label, "pointer_corruption")
        self.assertEqual(step.gates["register_provenance"].status, "open")
        self.assertEqual(step.gates["external_corruption_gate"].status, "blocked")
        self.assertEqual(step.gates["field_type_classification"].status, "open")
        self.assertEqual(updates["current_signature_class"], "pointer_corruption")

    def test_reuses_prior_managed_state_when_llm_omits_signature(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 3,
                "reasoning": "No new crash signature was stated.",
                "action": None,
                "is_conclusive": False,
                "signature_class": None,
                "root_cause_class": None,
                "partial_dump": "unknown",
            }
        )

        prior_state = {
            "current_signature_class": "pointer_corruption",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
        }

        step, updates = project_managed_analysis_step(
            llm_step,
            prior_state,
            original_reasoning="No new crash signature was stated.",
        )

        self.assertEqual(step.signature_class, "pointer_corruption")
        self.assertEqual(step.partial_dump, "partial")
        self.assertEqual(updates["current_partial_dump"], "partial")

    def test_preserves_llm_gate_closures_in_managed_projection(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 12,
                "reasoning": "All required pointer-corruption gates are closed by concrete evidence.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "partial_dump": "partial",
                "gates": {
                    "register_provenance": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "evidence": "pt_regs and disassembly show the poisoned pointer origin.",
                    },
                    "object_lifetime": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "evidence": "kmem confirms the slab object is still allocated.",
                    },
                    "local_corruption_exclusion": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "evidence": "No local writer on the crash path reaches the corrupted fields.",
                    },
                    "external_corruption_gate": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "prerequisite": "local_corruption_exclusion",
                        "evidence": "Cross-driver evidence supports an external overwrite.",
                    },
                    "field_type_classification": {
                        "required_for": ["pointer_corruption"],
                        "status": "closed",
                        "evidence": "Source cross-reference identifies the corrupted field type.",
                    },
                },
                "final_diagnosis": FinalDiagnosis(
                    crash_type="kernel paging request",
                    panic_string="BUG: unable to handle kernel paging request",
                    faulting_instruction="RIP: foo+0x10",
                    root_cause="DMA overwrite corrupted a live irqaction object.",
                    detailed_analysis="All mandatory gates were closed with concrete crash evidence.",
                    suspect_code=SuspectCode(
                        file="drivers/scsi/mpt3sas/mpt3sas_base.c",
                        function="_base_process_reply_queue",
                        line="unknown",
                    ),
                    evidence=["sample evidence"],
                ),
            }
        )

        step, updates = project_managed_analysis_step(
            llm_step,
            {},
            original_reasoning="All required pointer-corruption gates are closed by concrete evidence.",
        )

        self.assertTrue(step.is_conclusive)
        self.assertEqual(step.gates["register_provenance"].status, "closed")
        self.assertEqual(step.gates["external_corruption_gate"].status, "closed")
        self.assertEqual(
            updates["managed_gates"]["field_type_classification"].status,
            "closed",
        )


if __name__ == "__main__":
    unittest.main()
