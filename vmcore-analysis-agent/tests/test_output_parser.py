import unittest

from langchain_core.messages import HumanMessage, ToolMessage

from src.react.output_parser import (
    apply_executor_consistency_audit,
    build_tool_calls,
    repair_structured_output,
    render_action_arguments,
)
from src.react.schema import FinalDiagnosis, SuspectCode, VMCoreLLMAnalysisStep


class OutputParserAuditTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
