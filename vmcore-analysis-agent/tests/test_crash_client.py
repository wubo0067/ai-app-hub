import unittest

from src.mcp_tools.crash.client import build_tool_payload


class CrashClientPayloadTests(unittest.TestCase):
    def test_build_tool_payload_restores_tool_name_prefix(self) -> None:
        payload = build_tool_payload(
            "dis",
            {"command": "-rl ffffffffb4b1f419"},
            {
                "vmcore_path": "/tmp/vmcore",
                "vmlinux_path": "/tmp/vmlinux",
            },
        )

        self.assertEqual(payload["command"], "dis -rl ffffffffb4b1f419")

    def test_build_tool_payload_keeps_existing_full_command(self) -> None:
        payload = build_tool_payload(
            "sys",
            {"command": "sys -t"},
            {
                "vmcore_path": "/tmp/vmcore",
                "vmlinux_path": "/tmp/vmlinux",
            },
        )

        self.assertEqual(payload["command"], "sys -t")


if __name__ == "__main__":
    unittest.main()
