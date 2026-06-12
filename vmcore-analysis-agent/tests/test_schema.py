import unittest

from src.react.schema import (
    FinalDiagnosis,
    GateEntry,
    Hypothesis,
    SuspectCode,
    VMCoreAnalysisStep,
    VMCoreLLMAnalysisStep,
)


class SchemaTests(unittest.TestCase):
    def test_vmcore_llm_analysis_step_accepts_top_level_corruption_mechanism(
        self,
    ) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 22,
                "reasoning": "Source correlation isolates a DMA-side field misuse.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "corruption_mechanism": "field_type_misuse",
                "partial_dump": "partial",
            }
        )

        self.assertEqual(step.corruption_mechanism, "field_type_misuse")

    def test_vmcore_llm_analysis_step_coerces_unknown_corruption_mechanism(
        self,
    ) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 24,
                "reasoning": "The stack overwrite looks like an out-of-bounds write.",
                "action": None,
                "is_conclusive": True,
                "signature_class": "bug_on",
                "root_cause_class": "memory_corruption",
                "corruption_mechanism": "out_of_bounds",
                "partial_dump": "partial",
            }
        )

        self.assertEqual(step.root_cause_class, "out_of_bounds")
        self.assertEqual(step.corruption_mechanism, "unknown")

    def test_vmcore_llm_analysis_step_accepts_stack_corruption_root_cause(
        self,
    ) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 24,
                "reasoning": "Stack canary overwrite is confirmed, but the precise overwrite primitive is still unknown.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": "stack_corruption",
                "partial_dump": "partial",
            }
        )

        self.assertEqual(step.root_cause_class, "stack_corruption")

    def test_vmcore_llm_analysis_step_maps_stack_protector_root_cause_alias(
        self,
    ) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 24,
                "reasoning": "The model used a legacy stack-protector label for the root cause.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": "stack_protector",
                "partial_dump": "partial",
            }
        )

        self.assertEqual(step.root_cause_class, "stack_corruption")

    def test_final_diagnosis_accepts_driver_source_evidence(self) -> None:
        diagnosis = FinalDiagnosis.model_validate(
            {
                "crash_type": "kernel paging request",
                "panic_string": "BUG: unable to handle kernel paging request at 000000e500080008",
                "faulting_instruction": "movzbl (%rcx,%rax,1), %eax",
                "root_cause": "A driver queue field was misused as a virtual pointer.",
                "detailed_analysis": "Source correlation identifies a DMA-side field at the corrupted offset.",
                "suspect_code": {
                    "file": "drivers/scsi/mpt3sas/mpt3sas_base.c",
                    "function": "_base_process_reply_queue",
                    "line": "unknown",
                },
                "evidence": [
                    "offset 0x10 contains 0x000000e500000000",
                    "offset 0x60 resolves to _base_interrupt",
                ],
                "driver_source_evidence": {
                    "object_type": "struct adapter_reply_queue",
                    "corrupted_field_name": "reply_post_free_dma",
                    "corrupted_field_type": "dma_addr_t",
                    "field_semantics": "DMA address field was used where a virtual pointer field was expected",
                    "inference_method": "function_pointer_anchor",
                    "upstream_reference": "drivers/scsi/mpt3sas/mpt3sas_base.c",
                },
                "corruption_mechanism": "field_type_misuse",
            }
        )

        self.assertEqual(
            diagnosis.driver_source_evidence.corrupted_field_name,
            "reply_post_free_dma",
        )
        self.assertEqual(diagnosis.corruption_mechanism, "field_type_misuse")
        self.assertIsInstance(diagnosis.suspect_code, SuspectCode)

    def test_final_diagnosis_normalizes_driver_inference_method_phrase(self) -> None:
        diagnosis = FinalDiagnosis.model_validate(
            {
                "crash_type": "kernel paging request",
                "panic_string": "BUG: unable to handle kernel paging request at 000000e500080008",
                "faulting_instruction": "movzbl (%rcx,%rax,1), %eax",
                "root_cause": "A driver queue field was misused as a virtual pointer.",
                "detailed_analysis": "Source correlation identifies a DMA-side field at the corrupted offset.",
                "suspect_code": {
                    "file": "drivers/scsi/mpt3sas/mpt3sas_base.c",
                    "function": "_base_process_reply_queue",
                    "line": "unknown",
                },
                "evidence": [
                    "offset 0x10 contains 0x000000e500000000",
                    "offset 0x60 resolves to _base_interrupt",
                ],
                "driver_source_evidence": {
                    "object_type": "struct adapter_reply_queue",
                    "corrupted_field_name": "reply_post_free_dma",
                    "corrupted_field_type": "dma_addr_t",
                    "field_semantics": "DMA address field was used where a virtual pointer field was expected",
                    "inference_method": "struct access from kernel debuginfo",
                    "upstream_reference": "drivers/scsi/mpt3sas/mpt3sas_base.c",
                },
                "corruption_mechanism": "field_type_misuse",
            }
        )

        self.assertEqual(
            diagnosis.driver_source_evidence.inference_method, "symbol_lookup"
        )

    def test_final_diagnosis_downgrades_unknown_corruption_mechanism(self) -> None:
        diagnosis = FinalDiagnosis.model_validate(
            {
                "crash_type": "stack protector",
                "panic_string": "Kernel stack is corrupted",
                "faulting_instruction": "search_module_extables+0x99",
                "root_cause": "A stack overwrite is the likely cause.",
                "detailed_analysis": "The stack contains text data and overwritten caller frames.",
                "suspect_code": {
                    "file": "fs/namei.c",
                    "function": "link_path_walk",
                    "line": "unknown",
                },
                "evidence": ["ASCII text on stack"],
                "corruption_mechanism": "out_of_bounds",
            }
        )

        self.assertEqual(diagnosis.corruption_mechanism, "unknown")

    def test_vmcore_llm_analysis_step_coerces_stack_corruption_signature(
        self,
    ) -> None:
        """LLM 输出 stack_corruption 作为 signature_class 时应被正确接受。"""
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 1,
                "reasoning": "The panic is stack-protector failure in search_module_extables.",
                "action": {"command_name": "dis", "arguments": ["-rl", "0xffffffff"]},
                "is_conclusive": False,
                "signature_class": "stack_corruption",
                "root_cause_class": None,
                "corruption_mechanism": None,
                "partial_dump": "partial",
            }
        )
        self.assertEqual(step.signature_class, "stack_corruption")

    def test_vmcore_llm_analysis_step_maps_unknown_signature_to_unknown(
        self,
    ) -> None:
        """LLM 输出完全未知的 signature_class 时应降级为 unknown，不抛异常。"""
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 1,
                "reasoning": "Testing unknown signature.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "some_totally_made_up_class",
                "root_cause_class": None,
                "corruption_mechanism": None,
                "partial_dump": "unknown",
            }
        )
        self.assertEqual(step.signature_class, "unknown")

    def test_vmcore_llm_analysis_step_maps_alias_signature(self) -> None:
        """LLM 输出 stack_protector 别名时应映射到 stack_corruption。"""
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 1,
                "reasoning": "Testing alias mapping.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "stack_protector",
                "root_cause_class": None,
                "corruption_mechanism": None,
                "partial_dump": "unknown",
            }
        )
        self.assertEqual(step.signature_class, "stack_corruption")

    def test_vmcore_llm_analysis_step_normalizes_confidence_phrase(self) -> None:
        step = VMCoreLLMAnalysisStep.model_validate(
            {
                "step_id": 3,
                "reasoning": "The evidence is bounded but not yet fully closed.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "unknown",
                "corruption_mechanism": None,
                "partial_dump": "partial",
                "confidence": "medium confidence",
            }
        )

        self.assertEqual(step.confidence, "medium")

    def test_hypothesis_normalizes_status_phrase(self) -> None:
        hypothesis = Hypothesis.model_validate(
            {
                "id": "H1",
                "label": "DMA_overwrite",
                "status": "primary",
            }
        )

        self.assertEqual(hypothesis.status, "leading")

    def test_gate_entry_normalizes_status_phrase(self) -> None:
        gate = GateEntry.model_validate(
            {
                "required_for": ["pointer_corruption"],
                "status": "done",
                "evidence": "register provenance is closed by disassembly",
            }
        )

        self.assertEqual(gate.status, "closed")

    def test_vmcore_analysis_step_normalizes_confidence_phrase(self) -> None:
        step = VMCoreAnalysisStep.model_validate(
            {
                "step_id": 10,
                "reasoning": "The evidence chain is suggestive but a gate remains open.",
                "action": None,
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "unknown",
                "partial_dump": "partial",
                "confidence": "low confidence",
            }
        )

        self.assertEqual(step.confidence, "low")


if __name__ == "__main__":
    unittest.main()
