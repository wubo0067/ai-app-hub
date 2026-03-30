import unittest

from src.react.prompts import analysis_crash_prompt


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


if __name__ == "__main__":
    unittest.main()
