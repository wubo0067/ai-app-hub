import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import HumanMessage, ToolMessage

from src.react.output_parser import (
    apply_executor_consistency_audit,
    build_tool_calls,
    repair_structured_output,
    render_action_arguments,
)
from src.react.schema import FinalDiagnosis, SuspectCode, VMCoreLLMAnalysisStep


class OutputParserAuditTests(unittest.TestCase):
    def test_promotes_standalone_mcp_tool_out_of_run_script(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 3,
                "reasoning": "The next diagnostic action is to resolve the canary slot using the tool.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["resolve_stack_canary_slot search_module_extables"],
                },
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        with patch(
            "src.react.output_parser.get_registered_tool_provider",
            return_value=SimpleNamespace(package_name="stack_canary"),
        ):
            audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertEqual(audited.action.command_name, "resolve_stack_canary_slot")
        self.assertEqual(audited.action.arguments, ["search_module_extables"])
        self.assertIn(
            "run_script wrapped a standalone MCP tool call",
            audited.additional_notes,
        )

    def test_keeps_real_crash_run_script_unchanged(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 4,
                "reasoning": "Bundle two crash commands in one session.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["sym ffffffffb4b1f419\ndis -rl ffffffffb4b1f419"],
                },
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertEqual(audited.action.command_name, "run_script")
        self.assertEqual(
            audited.action.arguments,
            ["sym ffffffffb4b1f419\ndis -rl ffffffffb4b1f419"],
        )

    def test_corrects_gpf_signature_for_oops_0000_kernel_paging_request(self) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "BUG: unable to handle kernel paging request at 000000e500080008\n"
                        "Oops: 0000 [#1] SMP NOPTI\n"
                        "RIP: 0010:ffffffffc051a3c4\n"
                    )
                )
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 2,
                "reasoning": "The crash should be treated as a protection fault first.",
                "action": {
                    "command_name": "dis",
                    "arguments": ["-rl", "ffffffffc051a3c4"],
                },
                "is_conclusive": False,
                "signature_class": "general_protection_fault",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.signature_class, "pointer_corruption")
        self.assertIn(
            "corrected from general_protection_fault to pointer_corruption",
            audited.reasoning,
        )
        self.assertIn("page-fault context", audited.additional_notes)

    def test_normalizes_final_diagnosis_page_fault_wording(self) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "BUG: unable to handle kernel paging request at 000000e500080008\n"
                        "Oops: 0000 [#1] SMP NOPTI\n"
                        "RIP: 0010:ffffffffc051a3c4\n"
                    )
                )
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 12,
                "reasoning": "The evidence chain has converged.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "wild_pointer",
                "partial_dump": "partial",
                "confidence": "high",
                "final_diagnosis": FinalDiagnosis(
                    crash_type="general protection fault",
                    panic_string="BUG: unable to handle kernel paging request at 000000e500080008",
                    faulting_instruction="movzbl (%rcx,%rax,1),%eax",
                    root_cause=(
                        "A wild pointer led to a general protection fault in interrupt context."
                    ),
                    detailed_analysis=(
                        "The register provenance points to a corrupted queue pointer, and the final "
                        "failure manifests as a general protection fault during queue processing."
                    ),
                    suspect_code=SuspectCode(
                        file="drivers/scsi/mpt3sas/mpt3sas_base.c",
                        function="_base_process_reply_queue",
                        line="unknown",
                    ),
                    evidence=[
                        "Oops: 0000",
                        "BUG: unable to handle kernel paging request",
                    ],
                ),
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(
            audited.final_diagnosis.crash_type,
            "kernel paging request",
        )
        self.assertIn("page fault", audited.final_diagnosis.root_cause.lower())
        self.assertNotIn(
            "general protection fault",
            audited.final_diagnosis.detailed_analysis.lower(),
        )
        self.assertIn(
            "page-fault context wording corrected in final_diagnosis.crash_type",
            audited.additional_notes,
        )

    def test_downgrades_reverse_slab_oob_claim(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 18,
                "reasoning": (
                    "In kmalloc slab, object 0xff1148f4a2171f00 performed OOB overwrite "
                    "into previous slot 0xff1148f4a2171e80 at lower address."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "out_of_bounds",
                "partial_dump": "partial",
                "confidence": "high",
                "final_diagnosis": FinalDiagnosis(
                    crash_type="kernel paging request",
                    panic_string="BUG: unable to handle kernel paging request",
                    faulting_instruction="mov (%rax),%rbx",
                    root_cause=(
                        "mpt3sas reverse-direction slab OOB from 0xff1148f4a2171f00 "
                        "to 0xff1148f4a2171e80 overwrote irqaction."
                    ),
                    detailed_analysis="Address adjacency proves reverse overwrite.",
                    suspect_code=SuspectCode(
                        file="drivers/scsi/mpt3sas/mpt3sas_base.c",
                        function="_base_interrupt",
                        line="unknown",
                    ),
                    evidence=["slab OOB", "0xff1148f4a2171f00 -> 0xff1148f4a2171e80"],
                ),
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertFalse(audited.is_conclusive)
        self.assertIsNone(audited.final_diagnosis)
        self.assertEqual(audited.root_cause_class, "unknown")
        self.assertEqual(audited.confidence, "low")
        self.assertIn("reverse slab OOB causality claim", audited.additional_notes)

    def test_keeps_non_reverse_slab_oob_claim_unchanged(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 19,
                "reasoning": (
                    "In kmalloc slab, object 0xff1148f4a2171e80 overflowed into adjacent "
                    "slot 0xff1148f4a2171f00 at higher address."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "out_of_bounds",
                "partial_dump": "partial",
                "confidence": "high",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertTrue(audited.is_conclusive)
        self.assertEqual(audited.root_cause_class, "out_of_bounds")
        self.assertEqual(audited.confidence, "high")

    def test_downgrades_dma_conclusion_without_minimum_evidence_gate(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 20,
                "reasoning": (
                    "No software buffer overflow from adjacent objects can explain the presence "
                    "of a precise mpt3sas log-info code deep within the slab page. DMA range "
                    "overlap proof is missing, but stray DMA is still the most likely root cause."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "partial_dump": "partial",
                "confidence": "medium",
                "final_diagnosis": FinalDiagnosis(
                    crash_type="pointer_corruption",
                    panic_string="BUG: unable to handle kernel paging request",
                    faulting_instruction="mov 0x50(%rbp),%rdx",
                    root_cause="Stray DMA write from mpt3sas corrupted irqaction.",
                    detailed_analysis=(
                        "A matching log_info value appears in the slab page, but no DMA-range "
                        "overlap or IOMMU evidence was verified."
                    ),
                    suspect_code=SuspectCode(
                        file="drivers/scsi/mpt3sas/mpt3sas_base.c",
                        function="unknown",
                        line="unknown",
                    ),
                    evidence=["0x30030109 appears in adjacent slab slot"],
                ),
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertFalse(audited.is_conclusive)
        self.assertIsNone(audited.final_diagnosis)
        self.assertEqual(audited.root_cause_class, "unknown")
        self.assertEqual(audited.confidence, "low")
        self.assertIn("DMA promotion gate not satisfied", audited.additional_notes)
        self.assertIn("possible hypothesis", audited.reasoning)

    def test_keeps_dma_conclusion_with_two_evidence_families(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 21,
                "reasoning": (
                    "vtop confirmed physical-page overlap with reply_frames_dma, and an IOMMU "
                    "fault log tied the remapping error to the same controller."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "partial_dump": "partial",
                "confidence": "high",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state={})

        self.assertTrue(audited.is_conclusive)
        self.assertEqual(audited.root_cause_class, "dma_corruption")
        self.assertEqual(audited.confidence, "high")

    def test_downgrades_uaf_claim_when_kmem_slot_is_allocated(self) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "crash> kmem -S ff1148f4a2171e80\n"
                        "CACHE             OBJSIZE  ALLOCATED     TOTAL  SLABS  SSIZE  NAME\n"
                        "ff1148f440004c40      128      78879    211008   3297     8k  kmalloc-128\n"
                        "  FREE / [ALLOCATED]\n"
                        "  [ff1148f4a2171e80]\n"
                    )
                )
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 22,
                "reasoning": (
                    "This is consistent with a use-after-free: the original irqaction was "
                    "freed and its memory reused for another kmalloc-128 allocation. The slab "
                    "shows the page fully allocated, but the pointer in irq_desc->action is stale."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "use_after_free",
                "partial_dump": "partial",
                "confidence": "high",
                "final_diagnosis": FinalDiagnosis(
                    crash_type="kernel paging request",
                    panic_string="BUG: unable to handle kernel paging request",
                    faulting_instruction="mov 0x50(%rbp),%rdx",
                    root_cause="A freed irqaction was reused and later dereferenced.",
                    detailed_analysis="Allocated slot was treated as stale UAF without free evidence.",
                    suspect_code=SuspectCode(
                        file="kernel/irq/proc.c",
                        function="show_interrupts",
                        line="unknown",
                    ),
                    evidence=["kmem -S ff1148f4a2171e80", "FREE / [ALLOCATED]"],
                ),
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertFalse(audited.is_conclusive)
        self.assertIsNone(audited.final_diagnosis)
        self.assertEqual(audited.root_cause_class, "pointer_corruption")
        self.assertEqual(audited.corruption_mechanism, "write_corruption")
        self.assertEqual(audited.confidence, "low")
        self.assertIn(
            "candidate slab slot is currently allocated", audited.additional_notes
        )

    def test_keeps_allocated_slot_uaf_when_lifetime_evidence_exists(self) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "crash> kmem -S ff1148f4a2171e80\n"
                        "  FREE / [ALLOCATED]\n"
                        "  [ff1148f4a2171e80]\n"
                    )
                )
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 23,
                "reasoning": (
                    "The slot is currently allocated, but alloc/free-stack evidence shows the "
                    "pointer survived free and now references a replacement object instance after reuse; "
                    "the retained stale reference confirms use-after-free with reallocation."
                ),
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "use_after_free",
                "partial_dump": "partial",
                "confidence": "high",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertTrue(audited.is_conclusive)
        self.assertEqual(audited.root_cause_class, "use_after_free")
        self.assertEqual(audited.confidence, "high")

    def test_prefix_overwrite_pattern_steers_mechanism_away_from_uaf(self) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "crash> kmem -S ff1148f4a2171e80\n"
                        "  FREE / [ALLOCATED]\n"
                        "  [ff1148f4a2171e80]\n"
                    )
                ),
                ToolMessage(
                    content=(
                        "crash> rd -x ff1148f4a2171e80 16\n"
                        "ff1148f4a2171e80:  0000000004060001 0000000000000000\n"
                        "ff1148f4a2171e90:  0100070000000000 0000800c00000010\n"
                        "ff1148f4a2171ea0:  0008002004690002 0001000000010000\n"
                        "ff1148f4a2171eb0:  0020ffe000080002 0000000200000003\n"
                        "ff1148f4a2171ec0:  0000000000000000 0000000000000000\n"
                        "ff1148f4a2171ed0:  0000000000000000 0000000000000000\n"
                        "ff1148f4a2171ee0:  0000000000000000 0000000000000000\n"
                        "ff1148f4a2171ef0:  0000000000000000 0000000000000000\n"
                    ),
                    tool_call_id="call_9",
                    name="rd",
                ),
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 24,
                "reasoning": (
                    "This still looks like use-after-free or an adjacent buffer overflow, because the "
                    "irqaction does not match the traversal context."
                ),
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "unknown",
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.root_cause_class, "pointer_corruption")
        self.assertEqual(audited.corruption_mechanism, "write_corruption")
        self.assertIn("prefix-overwrite signature", audited.additional_notes)

    def test_does_not_retrigger_allocated_uaf_audit_for_hedged_candidate_list(
        self,
    ) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "crash> kmem -S ff1148f4a2171e80\n"
                        "  FREE / [ALLOCATED]\n"
                        "  [ff1148f4a2171e80]\n"
                    )
                )
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 26,
                "reasoning": (
                    "Executor audit: kmem -S context shows the candidate slab slot is currently allocated, "
                    "but the analysis still asserted freed/stale-pointer UAF or free-then-reuse without separate "
                    "positive lifetime evidence. ALLOCATED rules out a simple freed-object explanation; absent "
                    "independent lifetime evidence, treat this as live-slot overwrite, type confusion, or another "
                    "non-UAF mechanism instead of concluding use-after-free. "
                    "The exact corruption mechanism (use-after-free, out-of-bounds, or DMA) cannot be determined from this partial dump."
                ),
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "pointer_corruption",
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.reasoning, llm_step.reasoning)
        self.assertIsNone(audited.additional_notes)

    def test_render_action_arguments_quotes_grep_alternation_pattern(self) -> None:
        rendered = render_action_arguments(
            ["-m", "|", "grep", "-Ei", "dma|iommu|mapping|buffer"]
        )

        self.assertEqual(rendered, '-m | grep -Ei "dma|iommu|mapping|buffer"')

    def test_render_action_arguments_quotes_plain_grep_pattern_with_pipe_chars(
        self,
    ) -> None:
        rendered = render_action_arguments(
            ["-m", "|", "grep", "-i", "dma|iommu|mapping|buffer", "|", "head", "-10"]
        )

        self.assertEqual(
            rendered,
            '-m | grep -i "dma|iommu|mapping|buffer" | head -10',
        )

    def test_render_action_arguments_preserves_existing_grep_quotes(self) -> None:
        rendered = render_action_arguments(
            [
                "-m",
                "|",
                "grep",
                "-i",
                "mpt3sas",
                "|",
                "grep",
                "-Ei",
                '"fail|error|timeout|fault|xid|mmu|fifo|dma|map|reset"',
            ]
        )

        self.assertEqual(
            rendered,
            '-m | grep -i mpt3sas | grep -Ei "fail|error|timeout|fault|xid|mmu|fifo|dma|map|reset"',
        )

    def test_build_tool_calls_preserves_grep_pattern_quoting(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 8,
                "reasoning": "Need a filtered log query next.",
                "action": {
                    "command_name": "log",
                    "arguments": [
                        "-m",
                        "|",
                        "grep",
                        "-Ei",
                        "dma|iommu|mapping|buffer",
                    ],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        tool_calls = build_tool_calls(llm_step, is_last_step=False)

        self.assertEqual(tool_calls[0]["name"], "log")
        self.assertEqual(
            tool_calls[0]["args"]["command"],
            '-m | grep -Ei "dma|iommu|mapping|buffer"',
        )

    def test_build_tool_calls_quotes_plain_grep_pattern_with_pipe_chars(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 9,
                "reasoning": "Need a broader filtered log query next.",
                "action": {
                    "command_name": "log",
                    "arguments": [
                        "-m",
                        "|",
                        "grep",
                        "-i",
                        "dma|iommu|mapping|buffer",
                        "|",
                        "head",
                        "-10",
                    ],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        tool_calls = build_tool_calls(llm_step, is_last_step=False)

        self.assertEqual(
            tool_calls[0]["args"]["command"],
            '-m | grep -i "dma|iommu|mapping|buffer" | head -10',
        )

    def test_build_tool_calls_preserves_existing_grep_quotes(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 10,
                "reasoning": "Need a quoted filtered driver log query next.",
                "action": {
                    "command_name": "log",
                    "arguments": [
                        "-m",
                        "|",
                        "grep",
                        "-i",
                        "mpt3sas",
                        "|",
                        "grep",
                        "-Ei",
                        '"fail|error|timeout|fault|xid|mmu|fifo|dma|map|reset"',
                    ],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        tool_calls = build_tool_calls(llm_step, is_last_step=False)

        self.assertEqual(
            tool_calls[0]["args"]["command"],
            '-m | grep -i mpt3sas | grep -Ei "fail|error|timeout|fault|xid|mmu|fifo|dma|map|reset"',
        )

    def test_repair_structured_output_normalizes_mechanism_into_root_cause_class(
        self,
    ) -> None:
        repaired = repair_structured_output(
            (
                "{"
                '"step_id": 22,'
                '"reasoning": "source typing confirms a dma field misuse",'
                '"action": null,'
                '"is_conclusive": true,'
                '"signature_class": "pointer_corruption",'
                '"root_cause_class": "field_type_misuse",'
                '"partial_dump": "partial"'
                "}"
            ),
            model_class=VMCoreLLMAnalysisStep,
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired.root_cause_class, "dma_corruption")
        self.assertEqual(repaired.corruption_mechanism, "field_type_misuse")

    def test_repair_structured_output_lifts_stack_corruption_from_mechanism(
        self,
    ) -> None:
        repaired = repair_structured_output(
            (
                "{"
                '"step_id": 23,'
                '"reasoning": "stack canary overwrite is confirmed",'
                '"action": null,'
                '"is_conclusive": false,'
                '"signature_class": "stack_corruption",'
                '"root_cause_class": "memory_corruption",'
                '"corruption_mechanism": "stack_corruption",'
                '"partial_dump": "partial"'
                "}"
            ),
            model_class=VMCoreLLMAnalysisStep,
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired.root_cause_class, "stack_corruption")
        self.assertEqual(repaired.corruption_mechanism, "unknown")

    def test_top_level_step_accepts_explicit_corruption_mechanism(self) -> None:
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 22,
                "reasoning": "The driver dereferenced a DMA-side field as a virtual pointer.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "corruption_mechanism": "field_type_misuse",
                "partial_dump": "partial",
            }
        )

        self.assertEqual(llm_step.root_cause_class, "dma_corruption")
        self.assertEqual(llm_step.corruption_mechanism, "field_type_misuse")

    def test_repair_structured_output_moves_out_of_bounds_to_root_cause_class(
        self,
    ) -> None:
        repaired = repair_structured_output(
            (
                "{"
                '"step_id": 24,'
                '"reasoning": "stack text suggests an upward overwrite into older frames",'
                '"action": null,'
                '"is_conclusive": true,'
                '"signature_class": "bug_on",'
                '"root_cause_class": "memory_corruption",'
                '"corruption_mechanism": "out_of_bounds",'
                '"partial_dump": "partial",'
                '"final_diagnosis": {'
                '"crash_type": "stack protector",'
                '"panic_string": "Kernel stack is corrupted",'
                '"faulting_instruction": "search_module_extables+0x99",'
                '"root_cause": "A stack overwrite is the most likely cause.",'
                '"detailed_analysis": "The stack contains text-like payload and corrupted older frames.",'
                '"suspect_code": {'
                '"file": "fs/namei.c",'
                '"function": "link_path_walk",'
                '"line": "unknown"},'
                '"evidence": ["ASCII text on stack"],'
                '"corruption_mechanism": "out_of_bounds"'
                "}"
                "}"
            ),
            model_class=VMCoreLLMAnalysisStep,
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired.root_cause_class, "out_of_bounds")
        self.assertEqual(repaired.corruption_mechanism, "unknown")
        self.assertIsNotNone(repaired.final_diagnosis)
        self.assertEqual(repaired.final_diagnosis.corruption_mechanism, "unknown")

    def test_repair_structured_output_accepts_stack_corruption_root_cause(
        self,
    ) -> None:
        repaired = repair_structured_output(
            (
                "{"
                '"step_id": 24,'
                '"reasoning": "stack canary overwrite confirms stack damage but not the exact overwrite primitive",'
                '"action": null,'
                '"is_conclusive": false,'
                '"signature_class": "stack_corruption",'
                '"root_cause_class": "stack_corruption",'
                '"partial_dump": "partial"'
                "}"
            ),
            model_class=VMCoreLLMAnalysisStep,
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired.root_cause_class, "stack_corruption")

    def test_repair_structured_output_maps_stack_protector_root_cause_alias(
        self,
    ) -> None:
        repaired = repair_structured_output(
            (
                "{"
                '"step_id": 24,'
                '"reasoning": "legacy stack-protector wording should not break structured output",'
                '"action": null,'
                '"is_conclusive": false,'
                '"signature_class": "stack_corruption",'
                '"root_cause_class": "stack_protector",'
                '"partial_dump": "partial"'
                "}"
            ),
            model_class=VMCoreLLMAnalysisStep,
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(repaired.root_cause_class, "stack_corruption")

    def test_downgrades_conclusion_when_write_fault_is_attributed_to_plain_read(
        self,
    ) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content=(
                        "Oops: 0002 [#1] SMP NOPTI\n" "RIP: 0010:ffffffff8656bf75\n"
                    )
                ),
                ToolMessage(
                    content=(
                        "0xffffffff8656bf63 <cpu_idle_poll+35>:\tmov    %gs:0x1b440,%rax\n"
                        "0xffffffff8656bf6c <cpu_idle_poll+44>:\tmov    (%rax),%rax\n"
                        "0xffffffff8656bf75 <cpu_idle_poll+53>:\tpause\n"
                    ),
                    tool_call_id="call_1",
                    name="dis",
                ),
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 4,
                "reasoning": "The most likely explanation is DMA corruption of the current pointer.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "partial_dump": "partial",
                "confidence": "medium",
                "final_diagnosis": FinalDiagnosis(
                    crash_type="kernel paging request",
                    panic_string="BUG: unable to handle kernel paging request",
                    faulting_instruction="mov (%rax),%rax",
                    root_cause="DMA corruption is the root cause.",
                    detailed_analysis="The report concludes DMA corruption without discussing the access-type contradiction.",
                    suspect_code=SuspectCode(
                        file="kernel/sched/idle.c",
                        function="cpu_idle_poll",
                        line="unknown",
                    ),
                    evidence=["Oops: 0002", "mov (%rax),%rax", "pause"],
                ),
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertFalse(audited.is_conclusive)
        self.assertIsNone(audited.final_diagnosis)
        self.assertEqual(audited.root_cause_class, "unknown")
        self.assertIn("access-type contradiction", audited.reasoning)
        self.assertIn("Oops 0x0002 decodes to write fault", audited.additional_notes)

    def test_leaves_step_unchanged_when_reasoning_already_discusses_mismatch(
        self,
    ) -> None:
        state = {
            "messages": [
                HumanMessage(
                    content="Oops: 0002 [#1] SMP NOPTI\nRIP: 0010:ffffffff8656bf75\n"
                ),
                ToolMessage(
                    content=(
                        "0xffffffff8656bf6c <cpu_idle_poll+44>:\tmov    (%rax),%rax\n"
                        "0xffffffff8656bf75 <cpu_idle_poll+53>:\tpause\n"
                    ),
                    tool_call_id="call_2",
                    name="dis",
                ),
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 5,
                "reasoning": (
                    "Oops error code 0002 indicates a write fault, but mov (%rax), %rax is a read; "
                    "this contradiction remains unresolved, so I need more evidence before concluding."
                ),
                "action": {
                    "command_name": "rd",
                    "arguments": ["0xffff8cd9befdb440", "1"],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(
            audited.reasoning,
            llm_step.reasoning,
        )
        self.assertIsNone(audited.additional_notes)

    def test_rebuilds_structured_action_from_explicit_piped_action_hint(self) -> None:
        state = {"messages": [HumanMessage(content="BUG: stack protector triggered\n")]}
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 16,
                "reasoning": (
                    "The next diagnostic step should search the kernel log for BUG markers.\n"
                    'Action: log -m | grep -Ei "BUG|page fault|kernel BUG" | head -30'
                ),
                "action": {
                    "command_name": "log",
                    "arguments": ["-m"],
                },
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.action.command_name, "run_script")
        self.assertEqual(
            audited.action.arguments,
            ['log -m | grep -Ei "BUG|page fault|kernel BUG" | head -30'],
        )
        self.assertIn(
            "structured action dropped the pipeline", audited.additional_notes
        )

    def test_preflight_inserts_mod_s_prelude_when_missing(self) -> None:
        # 设计目标 3：debug_symbol_paths 非空 + run_script 缺 mod -s 时，托底插入
        state = {
            "debug_symbol_paths": [
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                "/tmp/mpt3sas.ko.debug",
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 30,
                "reasoning": "Need to disassemble a third-party module function.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["dis -l rcu_stall_thread"],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.action.command_name, "run_script")
        # 头部应插入两条 mod -s，顺序与 debug_symbol_paths 一致
        self.assertEqual(
            audited.action.arguments[0],
            "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
        )
        self.assertEqual(
            audited.action.arguments[1], "mod -s mpt3sas /tmp/mpt3sas.ko.debug"
        )
        # 原命令保留在末尾
        self.assertEqual(audited.action.arguments[-1], "dis -l rcu_stall_thread")
        self.assertIn("Inserted 2 mod -s prelude line(s)", audited.additional_notes)

    def test_preflight_skips_insertion_when_mod_s_already_present(self) -> None:
        # LLM 已正确前置 mod -s 时，不应重复插入
        state = {
            "debug_symbol_paths": [
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko"
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 31,
                "reasoning": "Load module symbols then disassemble.",
                "action": {
                    "command_name": "run_script",
                    "arguments": [
                        "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                        "dis -l rcu_stall_thread",
                    ],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(len(audited.action.arguments), 2)
        self.assertEqual(
            audited.action.arguments[0],
            "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
        )
        self.assertIsNone(audited.additional_notes)

    def test_preflight_skips_insertion_when_no_debug_symbols(self) -> None:
        # 未传 --debug-symbols 时，不强制插入 mod -s
        state: dict = {}
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 32,
                "reasoning": "Disassemble a kernel function.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["dis -l panic_on_rcu_stall"],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        self.assertEqual(audited.action.arguments, ["dis -l panic_on_rcu_stall"])
        self.assertIsNone(audited.additional_notes)

    def test_preflight_adds_audit_when_reasoning_names_module_without_source_attempt(
        self,
    ) -> None:
        state = {
            "debug_symbol_paths": [
                "/tmp/mlx5_core.ko.debug",
                "/tmp/mpt3sas.ko.debug",
            ]
        }
        llm_step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 33,
                "reasoning": (
                    "The victim device is driven by mlx5_core, so mlx5_core is now a leading suspect. "
                    "I want one more ownership check before source-level blame."
                ),
                "action": {
                    "command_name": "struct",
                    "arguments": ["device", "ff292187955ae0b8"],
                },
                "is_conclusive": False,
                "signature_class": "null_deref",
                "root_cause_class": None,
                "partial_dump": "partial",
            }
        )

        audited = apply_executor_consistency_audit(llm_step, state)

        assert audited.additional_notes is not None
        self.assertIn(
            "does not attempt module-symbol closure",
            audited.additional_notes,
        )
        self.assertIn("mlx5_core", audited.additional_notes)


if __name__ == "__main__":
    unittest.main()
