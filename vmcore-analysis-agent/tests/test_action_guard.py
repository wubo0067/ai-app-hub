import unittest

from src.react.action_guard import (
    build_command_fingerprint,
    extract_crash_path_struct_offsets,
    extract_struct_layouts,
    validate_tool_call_request,
)


class ActionGuardTests(unittest.TestCase):
    def test_rejects_standalone_log_m(self) -> None:
        error = validate_tool_call_request(
            "log",
            {"command": "log -m"},
        )
        self.assertIn("standalone log -m is forbidden", error)

    def test_rejects_log_m_without_grep_after_pipe(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "log -m | sed -n '1,20p'"},
        )
        self.assertIn("must be piped to grep", error)

    def test_allows_log_m_with_grep(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": 'log -m | grep -Ei "BUG|page fault|kernel BUG"'},
        )
        self.assertIsNone(error)

    def test_rejects_unfiltered_sym_list_in_run_script(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas"},
        )
        self.assertIn("sym -l is forbidden", error)

    def test_allows_grep_filtered_sym_list_in_run_script(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nsym -l mpt3sas | grep -i reply"
            },
        )
        self.assertIsNone(error)

    def test_rejects_bt_a_without_hard_lockup_context(self) -> None:
        error = validate_tool_call_request("bt", {"command": "bt -a"})
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
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_address_arithmetic_with_decimal_offset(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "rd -x ff29204cce8f0a58+560 8"},
        )
        self.assertIn("address arithmetic must be resolved", error)

    def test_rejects_old_struct_offset_order(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {
                "script": "mod -s mpt3sas /tmp/mpt3sas.ko.debug\nstruct mpt3sas_reply_queue -o"
            },
        )
        self.assertIn("struct offset queries must use struct -o <type>", error)

    def test_rejects_module_symbol_without_mod_s(self) -> None:
        error = validate_tool_call_request(
            "run_script",
            {"script": "struct -o mpt3sas_reply_queue"},
        )
        self.assertIn("must start with mod -s", error)

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


if __name__ == "__main__":
    unittest.main()
