import unittest

from src.react.prompts import (
    analysis_crash_prompt,
    build_minimal_schema_enum_contract,
    simplified_structure_reasoning_prompt,
)
from src.react.schema import (
    get_corruption_mechanism_values,
    get_partial_dump_values,
    get_root_cause_class_values,
    get_signature_class_values,
)


class PromptContractTests(unittest.TestCase):
    def _stack_frame_prompt(self) -> str:
        return analysis_crash_prompt(
            signature_class="stack_corruption",
            recent_text="stack corruption duplicate saved RIP phantom frame unreliable bt",
        )

    def _stack_protector_prompt(self) -> str:
        return analysis_crash_prompt(
            signature_class="stack_corruption",
            recent_text="stack-protector __stack_chk_fail kernel stack is corrupted in",
        )

    def _driver_dma_prompt(self) -> str:
        return analysis_crash_prompt(
            signature_class="pointer_corruption",
            recent_text="dma iommu function pointer mod -s fee0 list_head self-referential",
            root_cause_class="dma_corruption",
            step_count=18,
            enabled_gates={"dma_corruption", "driver_source_correlation"},
        )

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
        prompt = self._driver_dma_prompt()

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

    def test_analysis_prompt_forbids_broad_rd_ss_ascii_sweeps(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "If you use `rd -SS`, the action MUST include an explicit small count",
            prompt,
        )
        self.assertIn("grep -E '[ -~]{8,}'", prompt)
        self.assertIn("Prefer a narrow window plus a symbol name", prompt)

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
        prompt = self._driver_dma_prompt()

        self.assertIn("Do not call a value a DMA physical address until", prompt)
        self.assertIn("validate it against sys -m or kmem -i", prompt)
        self.assertIn(
            "DMA-address-equals-physical-address only as a conditional working assumption",
            prompt,
        )

    def test_analysis_prompt_requires_mod_s_fallback_after_guessed_type_failure(
        self,
    ) -> None:
        prompt = self._driver_dma_prompt()

        self.assertIn(
            "If struct -o <guessed_type> fails on a module crash path", prompt
        )
        self.assertIn("sym -l <module> | grep -i <keyword>", prompt)

    def test_analysis_prompt_adds_driver_source_correlation_rules(self) -> None:
        prompt = self._driver_dma_prompt()

        self.assertIn("## Driver-Private Object Overlay", prompt)
        self.assertIn("### Step A: Function Pointer Anchoring", prompt)
        self.assertIn("### Step D: Open Source Cross-Reference", prompt)
        self.assertIn(
            "field type must drive the corruption-mechanism classification", prompt
        )

    def test_analysis_prompt_requires_field_type_disambiguation(self) -> None:
        prompt = self._driver_dma_prompt()

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

    def test_simplified_prompt_includes_current_schema_enum_contract(self) -> None:
        prompt = simplified_structure_reasoning_prompt()

        self.assertIn("Allowed enum values in final JSON:", prompt)
        for value in get_signature_class_values():
            self.assertIn(f"'{value}'", prompt)
        for value in get_root_cause_class_values():
            self.assertIn(f"'{value}'", prompt)
        for value in get_corruption_mechanism_values():
            self.assertIn(f"'{value}'", prompt)
        for value in get_partial_dump_values():
            self.assertIn(f"'{value}'", prompt)

    def test_simplified_prompt_requires_action_object_not_string(self) -> None:
        prompt = simplified_structure_reasoning_prompt()

        self.assertIn("Do NOT return action as a string", prompt)
        self.assertIn('"command_name": "rd"', prompt)
        self.assertIn('"arguments": ["-x", "ffff...", "16"]', prompt)

    def test_simplified_prompt_can_be_formatted_without_key_errors(self) -> None:
        prompt = simplified_structure_reasoning_prompt().format(
            current_step=4,
            force_conclusion="",
        )

        self.assertIn('"command_name": "rd"', prompt)
        self.assertIn('"command_name": "dis"', prompt)
        self.assertIn('"step_id": 4', prompt)

    def test_minimal_schema_enum_contract_requires_canonical_values(self) -> None:
        contract = build_minimal_schema_enum_contract()

        self.assertIn("Do not emit aliases or shorthand in final JSON", contract)
        self.assertIn("'stack_protector' -> 'stack_corruption'", contract)
        self.assertIn("'type_misuse' -> 'field_type_misuse'", contract)

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
        prompt = self._stack_protector_prompt()

        self.assertIn(
            "do not automatically pivot to that interrupted non-exception chain in every stack-protector case",
            prompt,
        )
        self.assertIn(
            "First explain why the canary-bearing function's own frame is not the primary suspect",
            prompt,
        )

    def test_analysis_prompt_defaults_stack_protector_to_own_frame(self) -> None:
        prompt = self._stack_protector_prompt()

        self.assertIn(
            "When the panic string explicitly says stack-protector failure in function F, the default hypothesis is corruption of F's own frame during F's execution",
            prompt,
        )
        self.assertIn(
            "Do not name an unrelated interrupted-path function unless you can prove a concrete write primitive or proven cross-frame overlap into F's canary slot",
            prompt,
        )

    def test_analysis_prompt_forces_pointer_valued_canary_provenance_read(self) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "treat it as a high-priority provenance clue rather than a completed diagnosis",
            prompt,
        )
        self.assertIn(
            "You are FORBIDDEN from invoking `partial dump` as an excuse to skip that provenance read before attempting it",
            prompt,
        )
        self.assertIn(
            "saved RBP, saved RIP, spilled local pointer, or nearby object reference",
            prompt,
        )

    def test_analysis_prompt_blocks_local_buffer_hunting_after_causality_elimination(
        self,
    ) -> None:
        prompt = analysis_crash_prompt()

        self.assertIn(
            "strictly FORBIDDEN from spending `dis` or `rd` on that function merely to hunt for local buffers",
            prompt,
        )
        self.assertIn(
            "instead, immediately move to the canary-bearing function itself, lower-address active callees, or overwritten-canary-value provenance",
            prompt,
        )

    def test_analysis_prompt_conditions_saved_rip_tool_for_stack_protector_cases(
        self,
    ) -> None:
        prompt = self._stack_protector_prompt()

        self.assertIn(
            "In explicit stack-protector cases, first close the canary slot with `resolve_stack_canary_slot`",
            prompt,
        )
        self.assertIn(
            "only then use `classify_saved_rip_frames_tool` for NON-CANARY provenance checks",
            prompt,
        )
        self.assertNotIn(
            "Prefer `classify_saved_rip_frames_tool` for phantom-frame and saved-RIP classification. Only if the tool is unavailable or unproven may you fall back to manual frame-by-frame saved-RIP validation.",
            prompt,
        )
        self.assertEqual(
            prompt.count(
                "In explicit stack-protector cases, first close the canary slot with `resolve_stack_canary_slot`"
            ),
            1,
        )
        self.assertEqual(
            prompt.count(
                "only then use `classify_saved_rip_frames_tool` for NON-CANARY provenance checks"
            ),
            1,
        )

    def test_analysis_prompt_prioritizes_self_frame_for_canary_owner(self) -> None:
        prompt = self._stack_frame_prompt()

        self.assertIn(
            "If suspect_frame_addr == canary_frame_addr, prioritize self-frame overflow, inline expansion,",
            prompt,
        )
        self.assertIn(
            "or unprotected leaf-callee overwrite before investigating any other frame",
            prompt,
        )

    def test_analysis_prompt_treats_duplicate_frames_as_hint_not_proof(self) -> None:
        prompt = self._stack_frame_prompt()

        self.assertIn(
            "treat this as a strong unwind or exception-boundary hint, not an automatic",
            prompt,
        )
        self.assertIn(
            "proof of stack smearing",
            prompt,
        )

    def test_analysis_prompt_rejects_formula_only_rbp_derivation(self) -> None:
        prompt = self._stack_frame_prompt()

        self.assertIn(
            "Do NOT derive RBP_absolute from the bt frame address by formula alone",
            prompt,
        )
        self.assertIn(
            "only after RBP_absolute has been established by an independently closed proof",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
