import unittest

from langchain_core.messages import HumanMessage, ToolMessage

from src.react.output_parser import apply_executor_consistency_audit
from src.react.schema import FinalDiagnosis, SuspectCode, VMCoreLLMAnalysisStep


class OutputParserAuditTests(unittest.TestCase):
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
