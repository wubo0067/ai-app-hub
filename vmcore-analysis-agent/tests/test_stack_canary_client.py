import unittest

from src.mcp_tools.stack_canary.client import build_tool_payload


class StackCanaryClientPayloadTests(unittest.TestCase):
    def test_resolve_stack_canary_slot_uses_plain_command_arguments(self) -> None:
        payload = build_tool_payload(
            "resolve_stack_canary_slot",
            {
                "command": "search_module_extables --panic-return-address ffffffffb4b1f419"
            },
            {
                "vmcore_path": "/tmp/vmcore",
                "vmlinux_path": "/tmp/vmlinux",
            },
        )

        self.assertEqual(
            payload["command"],
            "search_module_extables --panic-return-address ffffffffb4b1f419",
        )

    def test_classify_saved_rip_frames_tool_allows_empty_command(self) -> None:
        payload = build_tool_payload(
            "classify_saved_rip_frames_tool",
            {"command": ""},
            {
                "vmcore_path": "/tmp/vmcore",
                "vmlinux_path": "/tmp/vmlinux",
            },
        )

        self.assertEqual(payload["command"], "")

    def test_resolve_stack_canary_slot_rejects_empty_command(self) -> None:
        with self.assertRaises(ValueError):
            build_tool_payload(
                "resolve_stack_canary_slot",
                {"command": ""},
                {
                    "vmcore_path": "/tmp/vmcore",
                    "vmlinux_path": "/tmp/vmlinux",
                },
            )


if __name__ == "__main__":
    unittest.main()
