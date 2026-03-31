import json
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.react.llm_runtime import compress_messages_for_llm


class LLMRuntimeCompressionTests(unittest.TestCase):
    def test_compress_messages_preserves_all_ai_reasoning_content(
        self,
    ) -> None:
        old_reasoning = "Old reasoning details " * 200
        recent_reasoning = "Recent reasoning details " * 80

        messages = [
            HumanMessage(content="Initial Context"),
            AIMessage(
                content=json.dumps(
                    {
                        "step_id": 2,
                        "reasoning": "pointer corruption remains likely",
                        "action": {
                            "command_name": "run_script",
                            "arguments": ["dis -rl ffffffff81000000"],
                        },
                        "is_conclusive": False,
                        "signature_class": "pointer_corruption",
                        "root_cause_class": None,
                        "partial_dump": "partial",
                    }
                ),
                additional_kwargs={"reasoning_content": old_reasoning},
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "step_id": 3,
                        "reasoning": "register provenance is the next gate",
                        "action": {
                            "command_name": "run_script",
                            "arguments": ["bt -f"],
                        },
                        "is_conclusive": False,
                        "signature_class": "pointer_corruption",
                        "root_cause_class": None,
                        "partial_dump": "partial",
                    }
                ),
                additional_kwargs={"reasoning_content": recent_reasoning},
            ),
        ]

        compressed = compress_messages_for_llm(
            messages,
        )

        old_ai = compressed[1]
        recent_ai = compressed[2]

        self.assertIsInstance(old_ai, AIMessage)
        self.assertIsInstance(recent_ai, AIMessage)
        self.assertEqual(old_ai.additional_kwargs["reasoning_content"], old_reasoning)
        self.assertEqual(
            recent_ai.additional_kwargs["reasoning_content"],
            recent_reasoning,
        )

    def test_compress_messages_truncates_only_old_tool_messages(
        self,
    ) -> None:
        old_tool_output = "OLD-TOOL-OUTPUT-" * 600
        recent_tool_output = "RECENT-TOOL-OUTPUT-" * 40
        ai_reasoning = "Detailed step reasoning " * 160
        structured_content = json.dumps(
            {
                "step_id": 7,
                "reasoning": "Need DMA range confirmation next.",
                "action": {
                    "command_name": "run_script",
                    "arguments": ["kmem -p 0x65db7000"],
                },
                "is_conclusive": False,
                "signature_class": "pointer_corruption",
                "root_cause_class": "dma_corruption",
                "partial_dump": "full",
            }
        )

        messages = [
            HumanMessage(content="Initial Context"),
            AIMessage(
                content=structured_content,
                additional_kwargs={"reasoning_content": ai_reasoning},
            ),
            ToolMessage(content=old_tool_output, tool_call_id="tool-old"),
            ToolMessage(content=recent_tool_output, tool_call_id="tool-recent"),
        ]

        compressed = compress_messages_for_llm(
            messages,
            max_tool_output_chars=240,
            recent_tool_messages_to_keep=1,
        )

        compressed_ai = compressed[1]
        compressed_old_tool = compressed[2]
        compressed_recent_tool = compressed[3]

        self.assertEqual(compressed_ai.content, structured_content)
        self.assertEqual(
            compressed_ai.additional_kwargs["reasoning_content"], ai_reasoning
        )
        self.assertLess(len(compressed_old_tool.content), len(old_tool_output))
        self.assertIn("have been pruned", compressed_old_tool.content)
        self.assertEqual(compressed_recent_tool.content, recent_tool_output)


if __name__ == "__main__":
    unittest.main()
