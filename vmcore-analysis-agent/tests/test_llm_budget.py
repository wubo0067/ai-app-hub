import json
import sys
import types
import unittest
from pathlib import Path
from typing import ClassVar

import openai
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.llm_node import _build_structured_chain
from src.react.llm_runtime import (
    ContextBudgetError,
    LLMBudgetConfig,
    OutputBudget,
    ainvoke_with_retry,
    compress_messages_for_llm,
    plan_output_budget,
    prepare_messages_for_llm,
)
from src.llm.model import ChatDeepSeekReasoner


def _config(**overrides) -> LLMBudgetConfig:
    values = {
        "context_window": 524_288,
        "max_output_tokens": 48_000,
        "min_thinking_output_tokens": 32_768,
        "thinking_enabled": True,
        "reasoning_effort": "max",
        "safety_margin_tokens": 8_192,
        "min_output_tokens": 4_096,
        "max_text_output_tokens": 16_384,
        "reasoning_history_keep": 2,
        "chars_per_token": 2.0,
    }
    values.update(overrides)
    return LLMBudgetConfig(**values)


class CompressionTests(unittest.TestCase):
    def test_ai_gate_evidence_is_stripped_but_status_is_kept(self) -> None:
        content = json.dumps(
            {
                "step_id": 1,
                "reasoning": "r",
                "gates": {
                    "register_provenance": {
                        "status": "open",
                        "closure_basis": None,
                        "evidence": "E" * 20_000,
                    }
                },
            }
        )

        compressed = compress_messages_for_llm([AIMessage(content=content)])

        payload = json.loads(compressed[0].content)
        gate = payload["gates"]["register_provenance"]
        self.assertIsNone(gate["evidence"])
        self.assertEqual(gate["status"], "open")

    def test_prepare_keeps_latest_tool_and_strips_repeated_evidence(self) -> None:
        system = SystemMessage(content="s" * 1_000)
        messages = [HumanMessage(content="analyze")]
        messages.append(
            AIMessage(
                content=json.dumps(
                    {
                        "step_id": 1,
                        "reasoning": "r",
                        "gates": {
                            "register_provenance": {
                                "status": "open",
                                "evidence": "E" * 200_000,
                            }
                        },
                    }
                )
            )
        )
        for index in range(4):
            messages.append(
                ToolMessage(
                    content="O" * 30_000,
                    name="bt",
                    tool_call_id=f"old-{index}",
                )
            )
        latest = "L" * 10_000
        messages.append(
            ToolMessage(content=latest, name="bt", tool_call_id="latest")
        )

        compressed, budget = prepare_messages_for_llm(system, messages, _config())

        self.assertFalse(budget.must_compress)
        self.assertTrue(budget.thinking)
        self.assertEqual(compressed[-1].content, latest)
        payload = json.loads(compressed[1].content)
        self.assertIsNone(payload["gates"]["register_provenance"]["evidence"])
        self.assertIn("[SYSTEM LOG:", compressed[2].content)

    def test_prepare_raises_for_oversized_system_prompt(self) -> None:
        system = SystemMessage(content="s" * 1_100_000)
        with self.assertRaises(ContextBudgetError):
            prepare_messages_for_llm(system, [HumanMessage(content="x")], _config())


class BudgetPlanningTests(unittest.TestCase):
    def test_thinking_budget_uses_full_available_window(self) -> None:
        budget = plan_output_budget(100_000, 200_000, _config())

        self.assertFalse(budget.must_compress)
        self.assertTrue(budget.thinking)
        self.assertEqual(budget.max_tokens, 48_000)

    def test_thinking_is_disabled_when_window_is_tight(self) -> None:
        budget = plan_output_budget(500_000, 1_000_000, _config())

        self.assertFalse(budget.must_compress)
        self.assertFalse(budget.thinking)
        self.assertEqual(budget.max_tokens, 16_096)

    def test_insufficient_window_requires_compression(self) -> None:
        budget = plan_output_budget(520_000, 1_040_000, _config())

        self.assertTrue(budget.must_compress)
        self.assertIsNone(budget.max_tokens)


class _ProbeLLM(ChatDeepSeekReasoner):
    captured_kwargs: ClassVar[dict] = {}
    captured_payload: ClassVar[dict] = {}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).captured_kwargs = kwargs
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        type(self).captured_payload = payload
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content='{"step_id": 1, "reasoning": "ok", "action": null}'
                    )
                )
            ]
        )


class BindingTests(unittest.TestCase):
    def test_structured_chain_passes_budget_to_model(self) -> None:
        llm = _ProbeLLM(
            api_key="test",
            api_base="https://example.invalid/v1",
            model="test",
            max_tokens=48_000,
        )
        budget = OutputBudget(
            max_tokens=123,
            thinking=False,
            reasoning_effort="low",
            prompt_tokens=1,
            prompt_chars=2,
        )
        chain = _build_structured_chain(llm, budget)

        chain.invoke([AIMessage(content="x")])

        self.assertEqual(llm.captured_kwargs["max_tokens"], 123)
        self.assertEqual(llm.captured_kwargs["reasoning_effort"], "low")
        self.assertEqual(
            llm.captured_kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(llm.captured_payload["max_tokens"], 123)
        self.assertEqual(llm.captured_payload["reasoning_effort"], "low")
        self.assertEqual(
            llm.captured_payload["extra_body"],
            {"thinking": {"type": "disabled"}},
        )


class _LengthErrorChain:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        exc = openai.LengthFinishReasonError.__new__(openai.LengthFinishReasonError)
        Exception.__init__(exc, "length")
        raise exc


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_length_error_is_not_retried(self) -> None:
        chain = _LengthErrorChain()

        with self.assertRaises(openai.LengthFinishReasonError):
            await ainvoke_with_retry(chain, [], max_retries=3, base_delay=0)

        self.assertEqual(chain.calls, 1)


if __name__ == "__main__":
    unittest.main()