import unittest

from src.react.schema import VMCoreLLMAnalysisStep
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


if __name__ == "__main__":
    unittest.main()
