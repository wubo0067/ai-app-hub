import unittest

from src.react.prompts import (
    analysis_crash_prompt,
    simplified_structure_reasoning_prompt,
)


class PromptContractTests(unittest.TestCase):
    def test_analysis_prompt_uses_minimal_output_contract(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("Minimal-output contract", prompt)
        self.assertIn("executor-managed internal state", prompt)
        self.assertIn("MUST NOT appear in your JSON", prompt)
        self.assertNotIn('"active_hypotheses": [', prompt)
        self.assertNotIn('"gates": {{{{', prompt)

    def test_analysis_prompt_requires_offset_coverage_before_struct_interpretation(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("Q4 — Offset coverage", prompt)
        self.assertIn("debug info contains that type", prompt)
        self.assertIn("you MUST reject that type immediately".lower(), prompt.lower())

    def test_analysis_prompt_forbids_direct_address_arithmetic_actions(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("rd -x <addr>+<offset> <count>", prompt)
        self.assertIn("this agent forbids emitting address arithmetic directly", prompt)

    def test_analysis_prompt_enforces_register_identity_and_true_source_object(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("Never treat two registers as aliases", prompt)
        self.assertIn("if disassembly says mov 0x10(%r13), %rcx", prompt)
        self.assertIn("the source object to validate is r13, not rdi", prompt.lower())

    def test_analysis_prompt_distinguishes_non_null_invalid_address_from_null_deref(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "Oops: 0000 together with BUG: unable to handle kernel paging request is an x86 page-fault signature",
            prompt,
        )
        self.assertIn("0x000000e500080008 is NOT a NULL dereference equivalent", prompt)
        self.assertIn("prefer pointer_corruption as the signature path", prompt)
        self.assertIn("final_diagnosis.crash_type must stay consistent", prompt)

    def test_analysis_prompt_requires_quoted_grep_regex_and_module_symbol_loading(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn('grep -Ei "dma|iommu|mapping|buffer"', prompt)
        self.assertIn(
            "Do not guess protocol-layer or firmware-message struct names", prompt
        )
        self.assertIn("Load module symbols with mod -s first", prompt)

    def test_analysis_prompt_requires_second_stage_log_filter_for_noisy_drivers(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn('log -m | grep -i mpt3sas | grep -Evi "log_info"', prompt)
        self.assertIn(
            "If the first grep returns repetitive info or heartbeat lines", prompt
        )

    def test_analysis_prompt_forbids_standalone_log_actions(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "the emitted action itself MUST literally contain `| grep`", prompt
        )
        self.assertIn("NEVER emit `log -m`, `log -t`, or `log -a` standalone", prompt)
        self.assertIn(
            "do not pipe them to `head`, `tail`, `sed`, or other commands before grep",
            prompt,
        )

    def test_analysis_prompt_requires_alignment_aware_subword_reasoning(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("rd -x output is machine-word oriented", prompt)
        self.assertIn("Do not silently equate an aligned 8-byte word", prompt)
        self.assertIn(
            "if the field of interest is offset 0xc and width 32 bits", prompt
        )

    def test_analysis_prompt_requires_temporal_correlation_analysis(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "Repeated device reset, discovery, recovery, or link-flap messages", prompt
        )
        self.assertIn("If the last such event occurs seconds before the crash", prompt)

    def test_analysis_prompt_requires_dma_address_validation_before_labeling(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("Do not call a value a DMA physical address until", prompt)
        self.assertIn("validate it against sys -m or kmem -i", prompt)
        self.assertIn(
            "DMA-address-equals-physical-address only as a conditional working assumption",
            prompt,
        )

    def test_analysis_prompt_requires_mod_s_fallback_after_guessed_type_failure(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "If struct -o <guessed_type> fails on a module crash path", prompt
        )
        self.assertIn("sym -l <module> | grep -i <keyword>", prompt)

    def test_analysis_prompt_adds_driver_source_correlation_rules(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn("## 2.3b Driver Source Correlation", prompt)
        self.assertIn("Function Pointer Anchoring", prompt)
        self.assertIn("Open Source Cross-Reference", prompt)
        self.assertIn(
            "field type must drive the corruption-mechanism classification", prompt
        )

    def test_analysis_prompt_requires_field_type_disambiguation(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "Sub-step D: field-type disambiguation before naming the root cause", prompt
        )
        self.assertIn("If the field type is dma_addr_t", prompt)
        self.assertIn("Do not conflate these mechanisms", prompt)

    def test_simplified_prompt_separates_root_cause_class_and_corruption_mechanism(
        self,
    ) -> None:
        prompt = simplified_structure_reasoning_prompt()

        self.assertIn(
            "'corruption_mechanism': Extract a finer-grained mechanism", prompt
        )
        self.assertIn("NEVER in root_cause_class", prompt)
        self.assertIn("that is a schema error and must be corrected", prompt)
        self.assertIn('"corruption_mechanism": null', prompt)

    def test_analysis_prompt_treats_mechanism_in_root_cause_class_as_schema_error(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "field_type_misuse, missing_conversion, write_corruption, and reinit_path_bug belong only in corruption_mechanism",
            prompt,
        )
        self.assertIn("treat that output as a schema error", prompt)

    def test_analysis_prompt_rejects_stack_resident_code_pointer_as_writer_proof(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            'Reject any conclusion that infers "function X caused the overflow" merely because an address inside function X appears on the stack',
            prompt,
        )
        self.assertIn(
            "A kernel text address found on the stack is first evidence about the value that was written",
            prompt,
        )

    def test_analysis_prompt_requires_active_call_chain_before_exception_blame(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "if the active path is sys_open -> do_filp_open -> path_openat -> do_last -> link_path_walk -> inode_permission",
            prompt,
        )
        self.assertIn(
            "those VFS/open-path frames must be audited with disassembly and stack-layout reasoning before any blame shifts to handle_mm_fault or fault.c",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
