import sys
import types
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from src.react.graph_state import CONVERGENCE_GUARD_STREAK_THRESHOLD
from src.react.prompt_builder import build_executor_state_section
from src.react.schema import GateEntry

OPEN_GATE = {
    "register_provenance": GateEntry(
        status="open", evidence="pending", required_for=["null_deref"]
    )
}
CLOSED_GATE = {
    "register_provenance": GateEntry(
        status="closed",
        evidence="rd+dis",
        required_for=["null_deref"],
        closure_basis="executor_evidence",
    )
}
ALL_DIMENSION_FACTS = [
    "rd_word:0xff292187ae124a80=0x12",
    "struct_type:irqaction",
    "dis_symbol:show_interrupts",
    "sym:show_interrupts@0xffffffffbc379660:t",
]

BASE_STATE = {
    "step_count": 30,
    "current_partial_dump": "partial",
    "managed_active_hypotheses": [],
    "messages": [],
    "evidence_goal_version": 3,
    "evidence_goal_status": "closed",
}

MENU_MARKERS = (
    "Gate closure is not root-cause proof",
    "Mandatory gates are still unresolved",
    "prefer terminating with your conclusion",
    "Untapped evidence dimensions",
    "All four structured evidence dimensions",
    "Pivot requirement",
)


def _render(**overrides: object) -> str:
    state = dict(BASE_STATE)
    state.update(overrides)
    return build_executor_state_section(state)


class ReplanProbeMenuTests(unittest.TestCase):
    """C5：门控全部关闭但无根因时，提示词必须给出可执行的转向方向。"""

    def test_dead_end_replaces_no_outstanding_gate(self) -> None:
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class=None,
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="duplicate",
            duplicate_streak=3,
            no_progress_streak=3,
            replan_required=True,
            evidence_facts=ALL_DIMENSION_FACTS,
        )
        self.assertNotIn("Current gate objective: no outstanding gate", rendered)
        self.assertIn("all mandatory gates are closed but no root cause", rendered)
        self.assertIn("Gate closure is not root-cause proof", rendered)
        self.assertIn("signature_class=null_deref", rendered)
        self.assertIn("Synthesize your closed gates now", rendered)
        self.assertIn("Pivot requirement", rendered)

    def test_layer0_system_contains_bounded_uncertainty_and_payload_rules(self) -> None:
        from src.react.layer0_system import LAYER0_SYSTEM_PROMPT_TEMPLATE
        from src.react.playbooks import PLAYBOOKS
        from src.react.prompt_builder import build_analysis_system_prompt
        from src.react.prompts import simplified_structure_reasoning_prompt
        from src.react.sop_fragments import SOP_FRAGMENTS

        self.assertIn("Bounded uncertainty convergence", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("Corrupted payload handling", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("CORRUPTED PAYLOAD", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("coherent, documented protocol template", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("candidate writers", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("Bounded low-confidence convergence is always preferred", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        # N3: Neither LAYER0 nor the assembled prompt may recommend "memory_corruption" alongside pointer_corruption
        self.assertNotIn('"pointer_corruption" or "memory_corruption"', LAYER0_SYSTEM_PROMPT_TEMPLATE)
        assembled = build_analysis_system_prompt(BASE_STATE, is_last_step=False)
        self.assertNotIn('"pointer_corruption" or "memory_corruption"', assembled)
        self.assertNotIn('`"pointer_corruption"` or `"memory_corruption"`', assembled)
        # B1: struct dot syntax must be explicitly permitted in both LAYER0 and simplified_structure_reasoning_prompt
        self.assertIn("struct <type>.<field>[,<field>] <addr>", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("struct <type>.<field>[,<field>] <addr>", simplified_structure_reasoning_prompt())
        # B3: CR2 classification in step 5 and #PF vs #GP rules must be conditionalized
        self.assertIn("For a General Protection Fault (#GP), the CPU does NOT update CR2", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("fault-address classification (#PF CR2 / #GP non-canonical operand)", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertNotIn("RIP instruction, CR2 classification, and immediate bad operand", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("BUG: unable to handle page fault for address", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        # B4: bt -f consistency across layer0 and SOP 3.8b (only hex task_addr allowed; numeric PID/frame number rejected)
        self.assertNotIn("bt -f <pid>", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("bt -f <task_addr>", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("bt -f <task_addr>", SOP_FRAGMENTS["stack_overflow"])
        self.assertIn("never pass a frame number or numeric PID to bt -f", SOP_FRAGMENTS["stack_overflow"])
        # B2 & N7: Generic stack_corruption playbook must not unconditionally require a canary slot;
        # Canary Overwrite Value Analysis belongs in stack_protector_canary
        self.assertNotIn("Divide-by-Zero", PLAYBOOKS["general_protection_fault"])
        self.assertNotIn("MANDATORY PRE-CONCLUSION GATE (stack_protector_canary)", PLAYBOOKS["stack_corruption"])
        self.assertNotIn("Canary Overwrite Value Analysis", PLAYBOOKS["stack_corruption"])
        self.assertIn("Canary Overwrite Value Analysis", PLAYBOOKS["stack_protector_canary"])
        self.assertNotIn("Given canary_frame_addr", PLAYBOOKS["stack_corruption"])
        self.assertNotIn("relative to the corrupted canary slot", PLAYBOOKS["stack_corruption"])
        self.assertNotIn("Locate the stack canary check point (__stack_chk_fail call site)", PLAYBOOKS["stack_corruption"])
        # R3: canary-only rules must not remain in the generic (non-canary) stack playbook
        self.assertNotIn("Do NOT attribute canary corruption", PLAYBOOKS["stack_corruption"])
        self.assertNotIn("This restriction applies ONLY to the canary slot", PLAYBOOKS["stack_corruption"])
        self.assertNotIn("{CANARY_", PLAYBOOKS["stack_corruption"])
        # R1: bt -f guidance must never suggest a pid/task target
        self.assertNotIn("concrete pid", LAYER0_SYSTEM_PROMPT_TEMPLATE)
        # R2: every bt -f mention must carry the address form
        self.assertNotIn("Use bt -f,", SOP_FRAGMENTS["advanced_techniques"])

    def test_driver_overlay_requires_signal_or_dma_not_step_count_alone(self) -> None:
        from langchain_core.messages import HumanMessage
        from src.react.prompt_builder import _select_context_overlays, _select_sop_fragments
        from src.react.prompt_overlays import DRIVER_OBJECT_OVERLAY

        # Plain 'sym ' at step_count >= 6 must NOT trigger DRIVER_OBJECT_OVERLAY or duplicate SOP
        state_no_signal = {
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": None,
            "step_count": 8,
            "messages": [HumanMessage(content="sym ffffffff81000000")],
        }
        recent_no_signal = "sym ffffffff81000000"
        self.assertNotIn(
            DRIVER_OBJECT_OVERLAY,
            _select_context_overlays(state_no_signal, recent_no_signal),
        )
        self.assertEqual(
            _select_sop_fragments(state_no_signal, recent_no_signal),
            [],
        )

        # Concrete driver signal ('mod -s' / '_base_') triggers DRIVER_OBJECT_OVERLAY without duplicate SOP
        recent_with_signal = "mod -s mpt3sas /usr/lib/debug/mpt3sas.ko.debug"
        self.assertIn(
            DRIVER_OBJECT_OVERLAY,
            _select_context_overlays(state_no_signal, recent_with_signal),
        )
        self.assertEqual(
            _select_sop_fragments(state_no_signal, recent_with_signal),
            [],
        )

    def test_repeated_value_conflict_renders_compact_residual(self) -> None:
        from src.react.consistency import MAX_VALUE_CONFLICT_AUDIT_INJECTIONS
        from src.react.prompt_builder import _format_value_conflicts

        fact = (
            "conflict:object_does_not_match_type:irqaction@0xff292187ae124a80:"
            "next@0x18=0x12,thread_fn@0x20=0x5c"
        )
        full = _format_value_conflicts([fact], {fact: 1})
        self.assertIn("next@0x18=0x12", full[0])

        compact = _format_value_conflicts(
            [fact], {fact: MAX_VALUE_CONFLICT_AUDIT_INJECTIONS + 1}
        )
        self.assertIn("already audited", compact[0])
        self.assertNotIn("next@0x18=0x12", compact[0])

    def test_dmesg_rule_distinguishes_payload_provenance_from_dma_writer(self) -> None:
        from src.react.prompt_overlays import DRIVER_OBJECT_OVERLAY
        from src.react.sop_fragments import SOP_FRAGMENTS

        self.assertIn("confirmed payload provenance", DRIVER_OBJECT_OVERLAY)
        self.assertIn("mpt3sas_base_get_reply_virt_addr", DRIVER_OBJECT_OVERLAY)
        self.assertIn("ioc->reply_dma_min_address", DRIVER_OBJECT_OVERLAY)
        self.assertIn("Sub-step C2", SOP_FRAGMENTS["dma_corruption"])
        self.assertIn("reply_dma_min_address", SOP_FRAGMENTS["dma_corruption"])

    def test_protocol_candidate_routes_to_dma_sop(self) -> None:
        from src.react.prompt_builder import _select_sop_fragments
        from src.react.sop_fragments import SOP_FRAGMENTS

        state_with_candidate = {
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": None,
            "step_count": 26,
            "messages": [],
            "evidence_facts": [
                "rd_ascii:0xff292187ae125ab0=sense pool",
                "vtop_phys:0xff292187ae124a80=0x12e124a80",
            ],
        }

        fragments = _select_sop_fragments(
            state_with_candidate, "rd -x ff292187ae125ab0 2"
        )

        self.assertIn(SOP_FRAGMENTS["dma_corruption"], fragments)

    def test_reply_helper_routes_to_dma_sop(self) -> None:
        from src.react.prompt_builder import _select_sop_fragments
        from src.react.sop_fragments import SOP_FRAGMENTS

        state = {
            "current_signature_class": "pointer_corruption",
            "current_root_cause_class": None,
            "step_count": 26,
            "messages": [],
        }
        fragments = _select_sop_fragments(
            state,
            "call 0xffffffffc21598f0 <mpt3sas_base_get_reply_virt_addr>",
        )
        self.assertIn(SOP_FRAGMENTS["dma_corruption"], fragments)

    def test_no_menu_without_replan(self) -> None:
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class=None,
            managed_gates=OPEN_GATE,
            current_evidence_goal={"goal_id": "register_provenance"},
            last_action_status="executed",
            duplicate_streak=0,
            no_progress_streak=0,
            replan_required=False,
            evidence_facts=["rd_word:0x1=0x2"],
        )
        for marker in MENU_MARKERS:
            self.assertNotIn(marker, rendered)
        self.assertIn(
            "Current gate objective: close register_provenance by tracing the bad register back",
            rendered,
        )

    def test_known_root_cause_steers_to_termination(self) -> None:
        """L1：根因已定且门控全关时，提示词只能剩下"本轮收尾"这一个指令。

        原先只把"prefer terminating"与转向菜单并列输出，同一份提示词里
        "继续换方向探测"与"立即收尾"互相矛盾；run D 中模型跟随后者失败，
        收尾轮被 rd 探测占掉，tool_calls 被剥掉后整轮作废。
        """
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class="use_after_free",
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="executed",
            duplicate_streak=0,
            no_progress_streak=1,
            replan_required=True,
            evidence_facts=ALL_DIMENSION_FACTS,
        )
        self.assertIn("TERMINATE ON THIS TURN", rendered)
        self.assertIn("is NOT a prerequisite for concluding", rendered)
        self.assertIn(
            "terminate on this turn by emitting the final JSON conclusion", rendered
        )
        self.assertNotIn("root_cause_class is still unset", rendered)
        # 转向菜单必须被压制，否则又构成"继续探索"的竞争指令
        self.assertNotIn("Untapped evidence dimensions", rendered)
        self.assertNotIn("All four structured evidence dimensions", rendered)
        self.assertNotIn("Pivot requirement", rendered)
        self.assertNotIn("prefer terminating with your conclusion", rendered)

    def test_root_cause_with_open_gate_still_offers_pivot(self) -> None:
        """根因已定但门控未全关时不能要求收尾，仍保留转向方向。"""
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class="use_after_free",
            managed_gates=OPEN_GATE,
            current_evidence_goal={"goal_id": "register_provenance"},
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=2,
            replan_required=True,
            evidence_facts=["rd_word:0x1=0x2"],
        )
        self.assertIn("prefer terminating with your conclusion", rendered)
        self.assertNotIn("TERMINATE ON THIS TURN", rendered)
        self.assertIn("Untapped evidence dimensions", rendered)
        self.assertIn("Pivot requirement", rendered)

    def test_unregistered_gates_never_trigger_terminate_only(self) -> None:
        """门控尚未注册（None/{}）时不得声称"每个强制门控都已关闭"。"""
        for unregistered in (None, {}):
            with self.subTest(managed_gates=unregistered):
                rendered = _render(
                    current_signature_class="null_deref",
                    current_root_cause_class="use_after_free",
                    managed_gates=unregistered,
                    current_evidence_goal=None,
                    last_action_status="rejected",
                    duplicate_streak=0,
                    no_progress_streak=2,
                    replan_required=True,
                    evidence_facts=["rd_word:0x1=0x2"],
                )
                self.assertNotIn("TERMINATE ON THIS TURN", rendered)
                self.assertIn("Pivot requirement", rendered)

    def test_open_gate_replan_never_claims_gates_closed(self) -> None:
        """C1 的 DEDUP-BLOCKED 走 rejected 分支时门控可能仍未关闭。"""
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class=None,
            managed_gates=OPEN_GATE,
            current_evidence_goal={"goal_id": "register_provenance"},
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=1,
            replan_required=True,
            evidence_facts=[],
        )
        self.assertNotIn("Gate closure is not root-cause proof", rendered)
        self.assertIn("Mandatory gates are still unresolved", rendered)
        self.assertIn(
            "Current gate objective: close register_provenance by tracing",
            rendered,
        )

    def test_open_optional_gate_is_not_a_probe_objective(self) -> None:
        gates = {
            name: GateEntry(
                status="closed",
                evidence="executor-verified",
                required_for=["pointer_corruption"],
                closure_basis="executor_evidence",
            )
            for name in (
                "register_provenance",
                "object_lifetime",
                "local_corruption_exclusion",
            )
        }
        gates["external_corruption_gate"] = GateEntry(
            status="open",
            required_for=["pointer_corruption"],
        )
        rendered = _render(
            current_signature_class="pointer_corruption",
            current_root_cause_class=None,
            managed_gates=gates,
            current_evidence_goal=None,
            last_action_status="executed",
            duplicate_streak=0,
            no_progress_streak=0,
            replan_required=False,
            evidence_facts=ALL_DIMENSION_FACTS,
        )
        self.assertIn("Current gate objective: no outstanding gate", rendered)
        self.assertIn("Unresolved mandatory gates: none", rendered)
        self.assertIn("external_corruption_gate=open (optional; advisory only)", rendered)
        self.assertNotIn("advance external_corruption_gate", rendered)
        self.assertIn("Stage 6: convergence and bounded conclusion", rendered)
        self.assertNotIn("external_corruption_gate still pending", rendered)

    def test_telemetry_only_required_gate_does_not_invite_repeated_probing(self) -> None:
        gates = {
            "stack_integrity": GateEntry(
                status="open",
                required_for=["soft_lockup"],
            ),
            "lock_holder": GateEntry(
                status="open",
                required_for=["soft_lockup"],
            ),
        }
        rendered = _render(
            current_signature_class="soft_lockup",
            current_root_cause_class=None,
            managed_gates=gates,
            current_evidence_goal=None,
            last_action_status="executed",
            duplicate_streak=0,
            no_progress_streak=0,
            replan_required=False,
            evidence_facts=[],
        )
        self.assertIn("stack_integrity is telemetry-only", rendered)
        self.assertIn("Do not keep probing solely to close this gate", rendered)
        self.assertIn("result remains non-conclusive", rendered)

    def test_unregistered_gates_never_claim_gates_closed(self) -> None:
        """门控集合尚未注册时，`_format_unresolved_gates` 同样返回 "none"。

        这表示"没有门控"，而非"门控已全部关闭"。早期步或
        `_build_managed_gates` 返回 None 时会走到这里，若误判为门控穷尽，
        就会把"每个强制门控都已关闭"这条错误事实注入提示词。
        """
        for unregistered in (None, {}):
            with self.subTest(managed_gates=unregistered):
                rendered = _render(
                    current_signature_class="null_deref",
                    current_root_cause_class=None,
                    managed_gates=unregistered,
                    current_evidence_goal=None,
                    last_action_status="rejected",
                    duplicate_streak=0,
                    no_progress_streak=1,
                    replan_required=True,
                    evidence_facts=["rd_word:0x1=0x2"],
                )
                self.assertNotIn("Gate closure is not root-cause proof", rendered)
                self.assertNotIn("every mandatory gate", rendered)
                self.assertIn("Mandatory gates are still unresolved", rendered)

    def test_unclassified_signature_is_not_presented_as_gate_exhaustion(self) -> None:
        rendered = _render(
            current_signature_class="unknown",
            current_root_cause_class="pointer_corruption",
            managed_gates=None,
            current_evidence_goal=None,
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=2,
            replan_required=True,
            evidence_facts=[],
        )
        self.assertIn("no registered policy, conclusion prohibited", rendered)
        self.assertIn("no gate policy is registered", rendered)
        self.assertNotIn("all mandatory gates are closed", rendered)

    def test_untapped_dimensions_exclude_observed_ones(self) -> None:
        rendered = _render(
            current_signature_class="pointer_corruption",
            current_root_cause_class=None,
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="duplicate",
            duplicate_streak=3,
            no_progress_streak=3,
            replan_required=True,
            evidence_facts=["rd_word:0x1=0x2", "dis_symbol:show_interrupts"],
        )
        self.assertIn("Untapped evidence dimensions", rendered)
        self.assertIn("struct -o <type>", rendered)
        self.assertIn("sym <address>", rendered)
        self.assertNotIn("`rd <address> <count>`", rendered)
        self.assertNotIn("dis -rl <symbol>", rendered)

    def test_unknown_root_cause_never_claims_conclusion(self) -> None:
        """P0-2：`current_root_cause_class="unknown"` 表示尚未定论。

        `unknown` 是 RootCauseClass 的合法成员，`bool("unknown")` 为 True；
        若按真值判断，门控全关时会输出 "TERMINATE ON THIS TURN: root_cause_class=unknown
        is set"，等于告诉模型一个不存在的结论已经成立。
        """
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class="unknown",
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=1,
            replan_required=True,
            evidence_facts=["rd_word:0x1=0x2"],
        )
        self.assertNotIn("TERMINATE ON THIS TURN", rendered)
        self.assertNotIn("prefer terminating with your conclusion", rendered)
        self.assertIn("root_cause_class is still unset", rendered)

    def test_no_progress_spin_offers_forced_choice_without_root_cause(self) -> None:
        """P0-1：空转时提示词必须给出"提交结论 / 声明新取证目标"的二选一。"""
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class=None,
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD,
            replan_required=True,
            evidence_facts=ALL_DIMENSION_FACTS,
        )
        self.assertIn("COMMIT A CONCLUSION", rendered)
        self.assertIn("DECLARE A NEW EVIDENCE TARGET", rendered)
        self.assertIn("Re-reading the same bytes cannot produce new evidence", rendered)

    def test_forced_choice_suppressed_once_root_cause_committed(self) -> None:
        """已提交根因时走 TERMINATE 通道，不再并列二选一，避免自相矛盾。"""
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class="use_after_free",
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD,
            replan_required=True,
            evidence_facts=ALL_DIMENSION_FACTS,
        )
        self.assertIn("TERMINATE ON THIS TURN", rendered)
        self.assertNotIn("COMMIT A CONCLUSION", rendered)

    def test_forced_choice_suppressed_below_streak_threshold(self) -> None:
        rendered = _render(
            current_signature_class="null_deref",
            current_root_cause_class=None,
            managed_gates=CLOSED_GATE,
            current_evidence_goal=None,
            last_action_status="rejected",
            duplicate_streak=0,
            no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD - 1,
            replan_required=True,
            evidence_facts=["rd_word:0x1=0x2"],
        )
        self.assertNotIn("COMMIT A CONCLUSION", rendered)

    def test_malformed_evidence_facts_are_tolerated(self) -> None:
        for facts in (None, "notalist", [None, 42, "rd_word:0x1=0x2"]):
            with self.subTest(evidence_facts=facts):
                rendered = _render(
                    current_signature_class="null_deref",
                    current_root_cause_class=None,
                    managed_gates=CLOSED_GATE,
                    current_evidence_goal=None,
                    last_action_status="duplicate",
                    duplicate_streak=1,
                    no_progress_streak=1,
                    replan_required=True,
                    evidence_facts=facts,
                )
                self.assertIn("Pivot requirement", rendered)


if __name__ == "__main__":
    unittest.main()
