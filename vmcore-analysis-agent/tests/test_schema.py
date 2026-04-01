import unittest

from src.react.schema import FinalDiagnosis, SuspectCode, VMCoreLLMAnalysisStep


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


if __name__ == "__main__":
    unittest.main()
