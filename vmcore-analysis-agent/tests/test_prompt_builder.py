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

from src.react.prompt_builder import build_executor_state_section
from src.react.schema import GateEntry

OPEN_GATE = {
    "register_provenance": GateEntry(
        status="open", evidence="pending", required_for=["null_deref"]
    )
}
CLOSED_GATE = {
    "register_provenance": GateEntry(
        status="closed", evidence="rd+dis", required_for=["null_deref"]
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
        self.assertIn("Pivot requirement", rendered)

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
        self.assertIn("prefer terminating with your conclusion", rendered)
        self.assertNotIn("root_cause_class is still unset", rendered)

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
