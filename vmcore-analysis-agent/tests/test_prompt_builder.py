import json
import unittest

from langchain_core.messages import AIMessage, HumanMessage

from src.react.prompt_builder import (
    build_analysis_system_prompt,
    build_executor_state_section,
)
from src.react.prompt_layers import LAYER0_SYSTEM_PROMPT_TEMPLATE
from src.react.prompts import analysis_crash_prompt
from src.react.schema import GateEntry, Hypothesis


class PromptBuilderTests(unittest.TestCase):
    def test_executor_state_section_includes_managed_state(self) -> None:
        state = {
            "step_count": 12,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": [
                Hypothesis(
                    id="H1",
                    label="pointer_corruption",
                    rank=1,
                    status="leading",
                    evidence="latest provenance chain remains unresolved",
                )
            ],
            "managed_gates": {
                "register_provenance": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    evidence="awaiting last-writer trace",
                ),
            },
            "messages": [
                AIMessage(
                    content=json.dumps(
                        {
                            "step_id": 11,
                            "reasoning": "Need a disassembly next.",
                            "action": {
                                "command_name": "run_script",
                                "arguments": ["dis -rl ffffffff81000000"],
                            },
                            "is_conclusive": False,
                            "signature_class": "pointer_corruption",
                            "root_cause_class": None,
                            "partial_dump": "partial",
                        }
                    )
                )
            ],
        }

        section = build_executor_state_section(state)

        self.assertIn("Current Investigation State (Step 12)", section)
        self.assertIn("Signature class: pointer_corruption", section)
        self.assertIn("register_provenance=open", section)
        self.assertIn("run_script(dis -rl ffffffff81000000)", section)

    def test_dynamic_prompt_is_shorter_than_full_prompt_for_pointer_corruption(
        self,
    ) -> None:
        state = {
            "step_count": 8,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "register_provenance": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    evidence="awaiting provenance closure",
                ),
                "local_corruption_exclusion": GateEntry(
                    required_for=["pointer_corruption"],
                    status="blocked",
                    prerequisite="register_provenance",
                    evidence="awaiting prerequisite",
                ),
            },
            "messages": [HumanMessage(content="Initial Context")],
        }

        full_prompt = analysis_crash_prompt().format(VMCoreAnalysisStep_Schema="{}")
        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertLess(len(layered_prompt), len(full_prompt))
        self.assertIn("Pointer Corruption Playbook", layered_prompt)
        self.assertNotIn(
            "## 3.12 DMA Memory Corruption (Stray DMA Write)", layered_prompt
        )

    def test_layer0_prompt_comes_from_explicit_prompt_layers_template(self) -> None:
        state = {
            "step_count": 4,
            "current_signature_class": None,
            "current_root_cause_class": None,
            "current_partial_dump": "unknown",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [HumanMessage(content="Initial Context")],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("# PART 0: GLOBAL FORBIDDEN OPERATIONS", layered_prompt)
        self.assertIn("## 2.3 Analysis Flowchart (Layered Summary)", layered_prompt)
        self.assertIn("## 4.1 Disassembly", layered_prompt)
        self.assertIn(
            LAYER0_SYSTEM_PROMPT_TEMPLATE.splitlines()[0],
            layered_prompt,
        )

    def test_dma_fragment_is_injected_only_when_state_supports_it(self) -> None:
        state = {
            "step_count": 14,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "dma_corruption",
            "current_partial_dump": "full",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "local_corruption_exclusion": GateEntry(
                    required_for=["pointer_corruption"],
                    status="closed",
                    evidence="task_struct and stack validated",
                ),
                "external_corruption_gate": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    prerequisite="local_corruption_exclusion",
                    evidence="device-side overlap under investigation",
                ),
            },
            "messages": [
                HumanMessage(content="vmcore-dmesg includes iommu=pt and dma clues")
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("## 3.12 DMA Memory Corruption (Stray DMA Write)", layered_prompt)
        self.assertIn("Current Investigation State (Step 14)", layered_prompt)

    def test_driver_source_fragment_is_injected_when_function_pointer_cues_appear(
        self,
    ) -> None:
        state = {
            "step_count": 12,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "field_type_classification": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    evidence="awaiting source-level field typing",
                )
            },
            "messages": [
                HumanMessage(
                    content=(
                        "object dump shows function pointer ffffffffc051ad40, use mod -s and sym "
                        "to anchor _base_interrupt before guessing the type"
                    )
                )
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("## 3.13 Driver Source Correlation", layered_prompt)
        self.assertIn("Function-pointer anchor", layered_prompt)
        self.assertIn("Open-source cross-reference", layered_prompt)


if __name__ == "__main__":
    unittest.main()
