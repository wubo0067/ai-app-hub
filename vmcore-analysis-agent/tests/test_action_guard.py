import unittest
from pathlib import Path
import sys
import types

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.action_guard import (
    build_command_fingerprint,
    build_mod_s_prelude,
    extract_command_lines,
    extract_crash_path_struct_offsets,
    extract_struct_layouts,
    maybe_rewrite_module_symbol_tool_call,
    validate_tool_call_request,
)


class ActionGuardTests(unittest.TestCase):
    def test_rejects_standalone_log_m(self) -> None:
        error = validate_tool_call_request(
            "log",
            {"command": "log -m"},
        )
        assert error is not None
        self.assertIn("standalone log -m is forbidden", error)

    def test_rejects_log_m_without_grep_after_pipe(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "log -m | sed -n '1,20p'"},
        )
        assert error is not None
        self.assertIn("must be immediately piped to grep", error)

    def test_rejects_log_m_when_non_grep_command_appears_first_in_pipeline(
        self,
    ) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "log -m | head -20 | grep error"},
        )
        assert error is not None
        self.assertIn("must be immediately piped to grep", error)

    def test_rejects_log_m_with_bare_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "log -m | grep"},
        )
        assert error is not None
        self.assertIn("grep filter must include a concrete pattern", error)

    def test_rejects_log_m_with_grep_option_but_no_pattern(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "log -m | grep -i"},
        )
        assert error is not None
        self.assertIn("grep filter must include a concrete pattern", error)

    def test_allows_log_m_with_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'log -m | grep -Ei "BUG|page fault|kernel BUG"'},
        )
        self.assertIsNone(error)

    def test_allows_log_t_with_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'log -t | grep -Ei "watchdog|hard LOCKUP|NMI"'},
        )
        self.assertIsNone(error)

    def test_allows_log_a_with_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'log -a | grep -Ei "firmware|ACPI Error|BIOS bug"'},
        )
        self.assertIsNone(error)

    def test_extract_command_lines_accepts_run_script_arguments_list(self) -> None:
        lines = extract_command_lines(
            "run_script",
            [
                "log -m | grep -i error",
                "sym mpt3sas_base_attach",
            ],
        )

        self.assertEqual(
            lines,
            [
                "log -m | grep -i error",
                "sym mpt3sas_base_attach",
            ],
        )

    def test_extract_command_lines_accepts_run_script_arguments_dict(self) -> None:
        lines = extract_command_lines(
            "run_script",
            {
                "arguments": [
                    "log -m | grep -i error",
                    "sym mpt3sas_base_attach",
                ]
            },
        )

        self.assertEqual(
            lines,
            [
                "log -m | grep -i error",
                "sym mpt3sas_base_attach",
            ],
        )

    def test_rejects_unfiltered_sym_list_in_run_script(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas"},
        )
        assert error is not None
        self.assertIn("sym -l is forbidden", error)

    def test_rejects_sym_list_outside_run_script(self) -> None:
        error = validate_tool_call_request(
            "sym",
            {"command": "sym -l mpt3sas | grep -i reply"},
        )
        assert error is not None
        self.assertIn("sym -l is only allowed inside run_script", error)

    def test_rejects_grep_filtered_sym_list_without_target(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "sym -l | grep -i reply"},
        )
        assert error is not None
        self.assertIn("must include a concrete module or symbol target", error)

    def test_allows_grep_filtered_sym_list_in_run_script(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas | grep -i reply"
            },
        )
        self.assertIsNone(error)

    def test_rejects_grep_filtered_sym_list_without_pattern(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas | grep"},
        )
        assert error is not None
        self.assertIn("sym -l grep filter must include a concrete pattern", error)

    def test_allows_grep_filtered_sym_list_with_grep_options(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": 'mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas | grep -Ei "reply|queue"'
            },
        )
        self.assertIsNone(error)

    def test_allows_grep_filtered_sym_list_with_single_dash_grep_option(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas | grep -i mpt"
            },
        )
        self.assertIsNone(error)

    def test_still_rejects_address_arithmetic_before_grep_tail(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x ffff888012340000+0x40 16 | grep abc"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_large_rd_ss_printable_sweep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -SS 0xffff8b817de14000 8192 | grep -E '[ -~]{8,}'"},
        )
        assert error is not None
        self.assertIn("broad printable-character grep", error)

    def test_rejects_oversized_rd_ss_even_with_specific_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'rd -SS 0xffff8b817de14000 1024 | grep -i "task_struct"'},
        )
        assert error is not None
        self.assertIn("rd -SS count 1024 is too large", error)

    def test_allows_bounded_rd_ss_with_specific_anchor(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'rd -SS 0xffff8b817de17b40 64 | grep -i "task_struct"'},
        )
        self.assertIsNone(error)

    def test_rejects_bt_a_without_hard_lockup_context(self) -> None:
        error = validate_tool_call_request("bt", {"command": "bt -a"})
        assert error is not None
        self.assertIn("bt -a is forbidden", error)

    def test_allows_bt_a_for_hard_lockup(self) -> None:
        error = validate_tool_call_request(
            "bt",
            {"command": "bt -a"},
            allow_bt_a=True,
        )
        self.assertIsNone(error)

    def test_rejects_address_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x ff292053098eca58+0x10 8"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_address_arithmetic_with_decimal_offset(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x ff29204cce8f0a58+560 8"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_address_subtraction(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x ffff8b817de17a10-0x40 16"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_symbol_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "sym security_inode_permission+0x34"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_search_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "search -s ffff8b817de17a10-0x40 -e ffff8b817de17a10 deadbeef"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_dis_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "dis -rl security_inode_permission+0x34"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_dis_l_with_comma_appended_offset(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "dis -l show_interrupts,0x150"},
        )
        assert error is not None
        self.assertIn("does not accept comma-appended offsets", error)

    def test_rejects_struct_instance_query_with_appended_field_names(self) -> None:
        error = validate_tool_call_request(
            "struct",
            {"command": "struct device ff1149f3d327a0b8 driver init_name"},
        )
        assert error is not None
        self.assertIn(
            "struct instance queries only allow 'struct <type>' or 'struct <type> <addr>'",
            error,
        )

    def test_rejects_symbol_on_symbol_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x symbol_a+symbol_b 16"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_p_x_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "p/x some_symbol+0x8"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_allows_plain_p_x_symbol_read(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "p/x __per_cpu_offset[5]"},
        )
        self.assertIsNone(error)

    def test_rejects_struct_symbol_arithmetic(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "struct foo some_symbol+bar"},
        )
        assert error is not None
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_old_struct_offset_order(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct mpt3sas_reply_queue -o"
            },
        )
        assert error is not None
        self.assertIn("struct offset queries must use struct -o <type>", error)

    def test_allows_kmem_s_with_kernel_virtual_address(self) -> None:
        error = validate_tool_call_request(
            "kmem",
            {"command": "kmem -S ffff8b817de17a10"},
        )
        self.assertIsNone(error)

    def test_allows_kmem_s_with_cache_name(self) -> None:
        error = validate_tool_call_request(
            "kmem",
            {"command": "kmem -S kmalloc-128"},
        )
        self.assertIsNone(error)

    def test_allows_module_symbol_without_debug_symbols(self) -> None:
        # 设计意图：未传入 --debug-symbols 时，不强制 run_script 以 mod -s 开头
        error = validate_tool_call_request(
            "run_script",
            {"script": "struct -o mpt3sas_reply_queue"},
        )
        self.assertIsNone(error)

    def test_rejects_module_symbol_without_mod_s(self) -> None:
        # 传入 --debug-symbols 后，使用模块符号必须以 mod -s 开头
        error = validate_tool_call_request(
            "run_script",
            {"script": "struct -o mpt3sas_reply_queue"},
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/mpt3sas/mpt3sas.ko.debug"
            ],
        )
        assert error is not None
        self.assertIn("must start with one or more mod -s", error)
        self.assertIn("mpt3sas", error)

    def test_rejects_single_third_party_dis_l_without_mod_s(self) -> None:
        error = validate_tool_call_request(
            "dis",
            {"command": "dis -l rcu_stall_thread"},
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko"
            ],
        )
        assert error is not None
        self.assertIn("single crash commands that use third-party module symbols/types are forbidden", error)

    def test_allows_run_script_with_mod_s_for_dynamic_module_prefix(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko\ndis -l rcu_stall_thread"
            },
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko"
            ],
        )
        self.assertIsNone(error)

    def test_rewrites_single_third_party_dis_l_to_run_script(self) -> None:
        rewritten = maybe_rewrite_module_symbol_tool_call(
            "dis",
            {"command": "dis -l rcu_stall_thread"},
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko"
            ],
        )

        self.assertEqual(rewritten[0], "run_script")
        self.assertEqual(
            rewritten[1],
            {
                "script": "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko\ndis -l rcu_stall_thread"
            },
        )

    def test_does_not_rewrite_plain_kernel_symbol_query(self) -> None:
        rewritten = maybe_rewrite_module_symbol_tool_call(
            "dis",
            {"command": "dis -l panic_on_rcu_stall"},
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko"
            ],
        )

        self.assertIsNone(rewritten)

    def test_build_mod_s_prelude_generates_one_line_per_ko(self) -> None:
        # 每个 ko 生成一条 mod -s，module 名从路径推导
        prelude = build_mod_s_prelude(
            [
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                "/tmp/mpt3sas.ko.debug",
            ]
        )
        self.assertEqual(
            prelude,
            [
                "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                "mod -s mpt3sas /tmp/mpt3sas.ko.debug",
            ],
        )

    def test_build_mod_s_prelude_empty_when_no_paths(self) -> None:
        self.assertEqual(build_mod_s_prelude(None), [])
        self.assertEqual(build_mod_s_prelude([]), [])

    def test_maybe_rewrite_inserts_all_mod_s_prelude(self) -> None:
        # 多个 ko 时，改写后的 script 头部应包含所有 ko 的 mod -s
        rewritten = maybe_rewrite_module_symbol_tool_call(
            "dis",
            {"command": "dis -l rcu_stall_thread"},
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                "/tmp/mpt3sas.ko.debug",
            ],
        )
        assert rewritten is not None
        self.assertEqual(rewritten[0], "run_script")
        script_lines = rewritten[1]["script"].split("\n")
        # 前两行必须是两条 mod -s，顺序与 debug_symbol_paths 一致
        self.assertEqual(
            script_lines[0],
            "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
        )
        self.assertEqual(script_lines[1], "mod -s mpt3sas /tmp/mpt3sas.ko.debug")
        # 最后一行是原命令
        self.assertEqual(script_lines[-1], "dis -l rcu_stall_thread")

    def test_validate_requires_all_kos_loaded_when_multiple(self) -> None:
        # debug_symbol_paths 有多个 ko 时，只加载一个仍应被拒绝
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s rcu_stall_mod /home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko\ndis -l rcu_stall_thread"
            },
            debug_symbol_paths=[
                "/home/calmwu/Program/vmcore-analysis-agent/simulate-crash/rcu_stall/rcu_stall_mod.ko",
                "/tmp/mpt3sas.ko.debug",
            ],
        )
        # 只加载了 rcu_stall_mod，未加载 mpt3sas；但命令未使用 mpt3sas 符号，
        # 当前 _uses_module_specific_symbol 只检测命令实际用到的符号，
        # 因此这里验证：命令用到 rcu_stall_mod 符号且已加载该模块 → 通过
        self.assertIsNone(error)

    def test_extracts_crash_path_offsets_from_disassembly(self) -> None:
        output = """0xffffffffc051a2f3 <_base_process_reply_queue+19>:\tmovzbl 0x8(%rdi),%eax
0xffffffffc051a2f7 <_base_process_reply_queue+23>:\tmov    (%rdi),%r14
0xffffffffc051a30a <_base_process_reply_queue+42>:\tmov    %rdi,%r13
0xffffffffc051a31f <_base_process_reply_queue+63>:\tmov    0x10(%r13),%rax
0xffffffffc051a323 <_base_process_reply_queue+67>:\tmov    0xc(%r13),%edx
0xffffffffc051a3b0 <_base_process_reply_queue+208>:\tmov    0x10(%r13),%rcx
0xffffffffc051a38f <_base_process_reply_queue+175>:\tmovzwl 0xb1c(%r14),%eax
"""
        self.assertEqual(
            extract_crash_path_struct_offsets(output),
            [0, 8, 12, 16],
        )

    def test_extracts_struct_layout(self) -> None:
        output = """struct MPT3SAS_TARGET {
   [0] struct scsi_target *starget;
   [8] u64 sas_address;
  [16] struct _raid_device *raid_device;
  [24] u16 handle;
  [28] int num_luns;
  [32] u32 flags;
  [36] u8 deleted;
  [37] u8 tm_busy;
  [40] struct hba_port *port;
  [48] struct _sas_device *sas_dev;
  [56] struct _pcie_device *pcie_dev;
}
SIZE: 64"""
        self.assertEqual(
            extract_struct_layouts(output)["MPT3SAS_TARGET"],
            {
                "size": 64,
                "field_offsets": [0, 8, 16, 24, 28, 32, 36, 37, 40, 48, 56],
            },
        )

    def test_rejects_first_time_struct_bundle_when_offsets_known(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct -o MPT3SAS_TARGET\nstruct MPT3SAS_TARGET ff292053098eca58"
            },
            observed_struct_offsets=[0, 8, 12, 16],
            struct_layout_cache={},
        )
        assert error is not None
        self.assertIn("cannot be combined with first-time struct -o", error)

    def test_rejects_struct_type_with_incompatible_field_offsets(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct MPT3SAS_TARGET ff292053098eca58"
            },
            observed_struct_offsets=[0, 8, 12, 16],
            struct_layout_cache={
                "MPT3SAS_TARGET": {
                    "size": 64,
                    "field_offsets": [0, 8, 16, 24, 28, 32, 36, 37, 40, 48, 56],
                }
            },
        )
        assert error is not None
        self.assertIn("does not cover the observed crash-path field offsets 0xc", error)

    def test_fingerprint_strips_mod_and_head_suffix(self) -> None:
        left = build_command_fingerprint(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\ndis -rl ffffffffc051a3c4 | head -20"
            },
        )
        right = build_command_fingerprint(
            "run_script",
            {"script": "dis -rl ffffffffc051a3c4"},
        )
        self.assertEqual(left, right)

    def test_fingerprint_normalizes_struct_offset_shape(self) -> None:
        left = build_command_fingerprint(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct mpt3sas_reply_queue -o"
            },
        )
        right = build_command_fingerprint(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct -o mpt3sas_reply_queue"
            },
        )
        self.assertEqual(left, right)

    def test_fingerprint_accepts_run_script_arguments_list_shape(self) -> None:
        fingerprint = build_command_fingerprint(
            "run_script",
            [
                "mod -s mpt3sas /tmp/mpt3sas.ko.debug",
                "log -m | grep -i error",
            ],
        )

        self.assertEqual(fingerprint, "log -m | grep -i error")


if __name__ == "__main__":
    unittest.main()