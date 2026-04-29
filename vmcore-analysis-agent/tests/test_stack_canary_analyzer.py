import unittest
from unittest.mock import patch

from src.mcp_tools.stack_canary.analyzer import (
    _get_function_disassembly,
    _resolve_function_address,
    BtFrame,
    resolve_stack_canary,
)


class StackCanaryAnalyzerTests(unittest.TestCase):
    def test_resolve_function_address_prefers_bt_rip(self) -> None:
        frames = [
            BtFrame(
                num=4,
                frame_addr=0x1000,
                function="search_module_extables",
                rip=0xDEADBEEF,
            )
        ]

        with patch("src.mcp_tools.stack_canary.analyzer.run_crash_command") as mock_run:
            address = _resolve_function_address(
                "search_module_extables",
                frames,
                "/tmp/vmcore",
                "/tmp/vmlinux",
            )

        self.assertEqual(address, 0xDEADBEEF)
        mock_run.assert_not_called()

    def test_get_function_disassembly_prefers_address_form(self) -> None:
        with patch(
            "src.mcp_tools.stack_canary.analyzer.run_crash_command",
            return_value=(
                "crash> dis -rl ffffffffb4b1f419\n"
                "0xffffffffb4b1f380 <search_module_extables>: nopl 0x0(%rax,%rax,1)\n"
                "0xffffffffb4b1f385 <search_module_extables+5>: push %rbp\n"
            ),
        ) as mock_run:
            disassembly = _get_function_disassembly(
                "search_module_extables",
                0xFFFFFFFFB4B1F419,
                "/tmp/vmcore",
                "/tmp/vmlinux",
            )

        self.assertIn("push %rbp", disassembly)
        mock_run.assert_called_once_with(
            "dis -rl ffffffffb4b1f419",
            "/tmp/vmcore",
            "/tmp/vmlinux",
            True,
        )

    def test_get_function_disassembly_falls_back_when_name_form_is_stub(self) -> None:
        responses = {
            "sym search_module_extables": "ffffffffb4b1f419 (T) search_module_extables+153 kernel/module.c:4143",
            "dis -rl ffffffffb4b1f419": "crash> dis -rl ffffffffb4b1f419\n0xffffffffb4b1f380 <search_module_extables>: nopl 0x0(%rax,%rax,1)\n",
            "dis -rl search_module_extables": "crash> dis -rl search_module_extables\n0xffffffffb4b1f380 <search_module_extables>: nopl 0x0(%rax,%rax,1)\n",
        }

        def fake_run(command: str, *_args):
            return responses[command]

        frames: list[BtFrame] = []
        with patch(
            "src.mcp_tools.stack_canary.analyzer.run_crash_command",
            side_effect=fake_run,
        ):
            address = _resolve_function_address(
                "search_module_extables",
                frames,
                "/tmp/vmcore",
                "/tmp/vmlinux",
            )
            disassembly = _get_function_disassembly(
                "search_module_extables",
                address,
                "/tmp/vmcore",
                "/tmp/vmlinux",
            )

        self.assertEqual(address, 0xFFFFFFFFB4B1F419)
        self.assertIn("search_module_extables", disassembly)

    def test_resolve_stack_canary_uses_address_disassembly_before_name_form(
        self,
    ) -> None:
        bt_output = "\n".join(
            [
                'PID: 15482    TASK: ffff8b84894ce300  CPU: 5    COMMAND: "python"',
                " #3 [ffff8b817de17a00] __stack_chk_fail at ffffffffb4a9af2b",
                " #4 [ffff8b817de17a10] search_module_extables at ffffffffb4b1f419",
            ]
        )
        canary_dis = "\n".join(
            [
                "crash> dis -rl ffffffffb4b1f419",
                "0xffffffffb4b1f380 <search_module_extables>: nopl 0x0(%rax,%rax,1)",
                "0xffffffffb4b1f385 <search_module_extables+5>: push %rbp",
                "0xffffffffb4b1f386 <search_module_extables+6>: mov %rsp,%rbp",
                "0xffffffffb4b1f39c <search_module_extables+28>: mov %rax,-0x18(%rbp)",
                "0xffffffffb4b1f414 <search_module_extables+148>: call 0xffffffffb4a9af10 <__stack_chk_fail>",
                "0xffffffffb4b1f419 <search_module_extables+153>: nopl 0x0(%rax)",
            ]
        )
        stack_chk_fail_dis = "\n".join(
            [
                "crash> dis -rl ffffffffb4a9af2b",
                "0xffffffffb4a9af10 <__stack_chk_fail>: nopl 0x0(%rax,%rax,1)",
                "0xffffffffb4a9af15 <__stack_chk_fail+5>: push %rbp",
                "0xffffffffb4a9af16 <__stack_chk_fail+6>: mov %rsp,%rbp",
            ]
        )
        stack_dump = "\n".join(
            [
                "ffff8b817de17a00:  ffffffffb4a9af2b ffff8b817de17a38",
                "ffff8b817de17a10:  ffffffffb4b1f419 ffffffffb56619d0",
                "ffff8b817de17a20:  ffff8b817de17ae8 ffffffffb4bf605d",
            ]
        )
        canary_slot_dump = "ffff8b817de17a20:  00000000deadbeef 0000000000000000"
        per_cpu_output = "$1 = 0xffff8b87fb740000"
        live_canary_dump = "ffff8b87fb740028:  00000000af23354d 0000000000000000"

        responses = {
            "bt": bt_output,
            "dis -rl ffffffffb4b1f419": canary_dis,
            "dis -rl ffffffffb4a9af2b": stack_chk_fail_dis,
            "rd -x ffff8b817de17a00 64": stack_dump,
            "rd -x ffff8b817de17a20 1": canary_slot_dump,
            "p/x __per_cpu_offset[5]": per_cpu_output,
            "rd -x ffff8b87fb740028 1": live_canary_dump,
        }

        def fake_run(command: str, *_args):
            return responses[command]

        with patch(
            "src.mcp_tools.stack_canary.analyzer.run_crash_command",
            side_effect=fake_run,
        ) as mock_run:
            result = resolve_stack_canary(
                "/tmp/vmcore",
                "/tmp/vmlinux",
                "search_module_extables",
            )

        self.assertIn('"success": true', result)
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("dis -rl ffffffffb4b1f419", commands)
        self.assertIn("dis -rl ffffffffb4a9af2b", commands)
        self.assertNotIn("dis -rl search_module_extables", commands)
        self.assertNotIn("dis -rl __stack_chk_fail", commands)


if __name__ == "__main__":
    unittest.main()
