import json
import unittest
from pathlib import Path
import sys
import types

from langchain_core.messages import AIMessage, HumanMessage

langgraph_module = types.ModuleType("langgraph")
graph_module = types.ModuleType("langgraph.graph")
graph_module.MessagesState = dict
managed_module = types.ModuleType("langgraph.managed")
managed_module.IsLastStep = bool
langgraph_module.graph = graph_module
langgraph_module.managed = managed_module
sys.modules.setdefault("langgraph", langgraph_module)
sys.modules.setdefault("langgraph.graph", graph_module)
sys.modules.setdefault("langgraph.managed", managed_module)

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

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
        self.assertIn(
            "Unresolved mandatory gates: register_provenance=open",
            section,
        )
        self.assertIn(
            "Current gate objective: close register_provenance by tracing the bad register back to the exact source object and field/offset that produced the operand",
            section,
        )
        self.assertIn(
            "Action selection rule: if any mandatory gate remains open or blocked",
            section,
        )
        self.assertIn(
            "Reasoning gate contract: name target gate register_provenance, explain which concrete source object and field/offset must be identified next, and state how the next action will close the bad-register chain.",
            section,
        )
        self.assertIn("Commands already run (do not repeat):", section)
        self.assertIn("1. dis -rl ffffffff81000000", section)

    def test_executor_state_section_aggregates_command_families(self) -> None:
        state = {
            "step_count": 14,
            "current_signature_class": "stack_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [
                AIMessage(
                    content=json.dumps(
                        {
                            "step_id": 10,
                            "reasoning": "Inspect stack region.",
                            "action": {
                                "command_name": "rd",
                                "arguments": ["-x", "ffff8b817de179f0", "4"],
                            },
                            "is_conclusive": False,
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "step_id": 11,
                            "reasoning": "Inspect the next stack window.",
                            "action": {
                                "command_name": "rd",
                                "arguments": ["-x", "ffff8b817de179e0", "8"],
                            },
                            "is_conclusive": False,
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "step_id": 12,
                            "reasoning": "Check the disassembly around the canary test.",
                            "action": {
                                "command_name": "dis",
                                "arguments": ["-rl", "0xffffffffb4b1f419"],
                            },
                            "is_conclusive": False,
                        }
                    )
                ),
                AIMessage(
                    content=json.dumps(
                        {
                            "step_id": 13,
                            "reasoning": "Check one more stack window.",
                            "action": {
                                "command_name": "rd",
                                "arguments": ["-x", "ffff8b817de17a80", "12"],
                            },
                            "is_conclusive": False,
                        }
                    )
                ),
            ],
        }

        section = build_executor_state_section(state)

        self.assertIn(
            "Commands already run (do not repeat):",
            section,
        )
        self.assertIn("1. rd -x ffff8b817de179f0 4", section)
        self.assertIn("2. rd -x ffff8b817de179e0 8", section)
        self.assertIn("3. dis -rl 0xffffffffb4b1f419", section)
        self.assertIn("4. rd -x ffff8b817de17a80 12", section)

    def test_dynamic_prompt_for_pointer_corruption_excludes_unrelated_overlays(
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

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("Pointer Corruption Playbook", layered_prompt)
        self.assertNotIn(
            "## 3.12 DMA Memory Corruption (Stray DMA Write)", layered_prompt
        )
        self.assertNotIn("## Stack-Corruption Overlay", layered_prompt)
        self.assertNotIn("## 3.8b Stack Protector Fast Path", layered_prompt)

    def test_executor_state_section_advances_to_object_validation_with_open_gate(
        self,
    ) -> None:
        state = {
            "step_count": 12,
            "current_signature_class": "stack_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "local_corruption_exclusion": GateEntry(
                    required_for=["stack_corruption"],
                    status="open",
                    evidence="partial dump cannot fully exclude local overwrite",
                ),
            },
            "messages": [HumanMessage(content="stack frames remain incomplete")],
        }

        section = build_executor_state_section(state)

        self.assertIn(
            "Current stage: Stage 4-5: object validation and source exclusion "
            "(local_corruption_exclusion still pending)",
            section,
        )
        self.assertIn(
            "Current gate objective: advance local_corruption_exclusion toward closed or n/a with concrete evidence",
            section,
        )

    def test_executor_state_section_advances_to_convergence_with_open_gate(
        self,
    ) -> None:
        state = {
            "step_count": 18,
            "current_signature_class": "stack_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "local_corruption_exclusion": GateEntry(
                    required_for=["stack_corruption"],
                    status="open",
                    evidence="partial dump cannot fully exclude local overwrite",
                ),
            },
            "messages": [HumanMessage(content="stack frames remain incomplete")],
        }

        section = build_executor_state_section(state)

        self.assertIn(
            "Current stage: Stage 6: convergence and bounded conclusion "
            "(local_corruption_exclusion still pending)",
            section,
        )

    def test_executor_state_section_prioritizes_blocked_gate_prerequisite(self) -> None:
        state = {
            "step_count": 15,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "local_corruption_exclusion": GateEntry(
                    required_for=["pointer_corruption"],
                    status="closed",
                    evidence="local object shape validated",
                ),
                "external_corruption_gate": GateEntry(
                    required_for=["pointer_corruption"],
                    status="blocked",
                    prerequisite="field_type_classification",
                    evidence="awaiting field typing before external attribution",
                ),
                "field_type_classification": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    evidence="driver-private field type remains unknown",
                ),
            },
            "messages": [
                HumanMessage(content="driver-private object remains unresolved")
            ],
        }

        section = build_executor_state_section(state)

        self.assertIn(
            "Unresolved mandatory gates: external_corruption_gate=blocked, field_type_classification=open",
            section,
        )
        self.assertIn(
            "Current gate objective: unblock external_corruption_gate by advancing prerequisite field_type_classification",
            section,
        )
        self.assertIn(
            "Reasoning gate contract: name target gate external_corruption_gate, explain why the next action advances prerequisite field_type_classification, and state the expected evidence needed to unblock or advance the gate",
            section,
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
        self.assertIn("## 3.1 Disassembly", layered_prompt)
        self.assertIn("Next-action gate discipline:", layered_prompt)
        self.assertIn(
            "reasoning MUST explicitly name the target gate",
            layered_prompt,
        )
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
        self.assertIn("## Driver-Private Object Overlay", layered_prompt)

    def test_layered_prompt_injects_dynamic_enum_contract(self) -> None:
        state = {
            "step_count": 6,
            "current_signature_class": "stack_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [HumanMessage(content="Initial Context")],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("[ENUM CONTRACT]", layered_prompt)
        self.assertIn("'stack_corruption'", layered_prompt)
        self.assertIn("'stack_protector' -> 'stack_corruption'", layered_prompt)
        self.assertIn("'type_misuse' -> 'field_type_misuse'", layered_prompt)

    def test_last_step_prompt_allows_bounded_nonconclusive_exit(self) -> None:
        state = {
            "step_count": 24,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": {
                "external_corruption_gate": GateEntry(
                    required_for=["pointer_corruption"],
                    status="open",
                    evidence="module debuginfo is missing and DMA overlap remains unverified",
                ),
            },
            "messages": [
                HumanMessage(content="partial dump and missing debuginfo block closure")
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=True)

        self.assertIn("This is your LAST STEP", layered_prompt)
        self.assertIn(
            "If mandatory verification gaps remain, set is_conclusive to false",
            layered_prompt,
        )
        self.assertIn("leave final_diagnosis null", layered_prompt)
        self.assertIn(
            "you MAY emit gate updates for the specific required gates you are closing",
            layered_prompt,
        )
        self.assertNotIn(
            "Set is_conclusive to true and do NOT request any further tool calls",
            layered_prompt,
        )

    def test_stack_protector_runtime_prompt_injects_stack_overlay_and_fast_path(
        self,
    ) -> None:
        state = {
            "step_count": 8,
            "current_signature_class": "stack_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [
                HumanMessage(
                    content=(
                        "Kernel panic - not syncing: stack-protector: Kernel stack is corrupted in: "
                        "search_module_extables\n"
                        "stack corruption\n"
                        "zone_statistics\n"
                        "link_path_walk"
                    )
                )
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("## 3.8b Stack Protector Fast Path", layered_prompt)
        self.assertIn(
            "Call `resolve_stack_canary_slot <function>` as the DEFAULT and PREFERRED action",
            layered_prompt,
        )
        # stack_protector_canary playbook Blame Guardrails should be present
        self.assertIn(
            "Do not blame link_path_walk, zone_statistics, handle_mm_fault, or any interrupted-path frame",
            layered_prompt,
        )
        self.assertNotIn(
            "The corruption source must be sought WITHIN the exception handler call chain itself",
            layered_prompt,
        )
        # STACK_CORRUPTION_OVERLAY must be suppressed for stack_protector cases to avoid
        # priority conflicts between S1-S5 generic methodology and the canary fast path
        self.assertNotIn("## Stack-Corruption Overlay", layered_prompt)
        self.assertNotIn(
            "## 3.12 DMA Memory Corruption (Stray DMA Write)",
            layered_prompt,
        )
        self.assertNotIn("## Driver-Private Object Overlay", layered_prompt)

    def test_pointer_corruption_runtime_prompt_excludes_stack_overlay(self) -> None:
        state = {
            "step_count": 10,
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [
                HumanMessage(
                    content=(
                        "pointer corruption with function pointer anchor and mod -s cue in a third-party driver"
                    )
                )
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("Pointer Corruption Playbook", layered_prompt)
        self.assertIn("## Driver-Private Object Overlay", layered_prompt)
        self.assertNotIn("## Stack-Corruption Overlay", layered_prompt)
        self.assertNotIn(
            "When the panic string explicitly says stack-protector failure in function F",
            layered_prompt,
        )

    def test_null_deref_runtime_prompt_still_injects_driver_source_guidance(self) -> None:
        state = {
            "step_count": 7,
            "current_signature_class": "null_deref",
            "current_root_cause_class": "unknown",
            "current_partial_dump": "partial",
            "managed_active_hypotheses": None,
            "managed_gates": None,
            "messages": [
                HumanMessage(
                    content=(
                        "irq_desc ownership shows struct device.driver = mlx5_core_driver, "
                        "msi_desc points to a third-party device, and a dma write remains plausible"
                    )
                )
            ],
        }

        layered_prompt = build_analysis_system_prompt(state, is_last_step=False)

        self.assertIn("## 3.13 Driver Source Correlation", layered_prompt)
        self.assertIn("Mandatory module-symbol closure before blame", layered_prompt)
        self.assertIn("## Driver-Private Object Overlay", layered_prompt)


if __name__ == "__main__":
    unittest.main()
