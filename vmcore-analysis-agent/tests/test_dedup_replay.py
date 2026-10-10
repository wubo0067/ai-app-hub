"""DEDUP 回放只给一次机会：同一指纹第二次索取必须升级为硬拒。

背景（run D）：LLM 在步骤 60/62/64 连续三次索取同一条
`rd -x ff292187ae124a80 16`。只要缓存输出"有证据"，DEDUP 分支就永远回放，
既不执行也不计数为拒绝，agent 只能靠 no_progress_streak 触顶才被掐停。
"""

import asyncio
import sys
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
react_pkg = types.ModuleType("src.react")
react_pkg.__path__ = [str(root / "src" / "react")]
sys.modules.setdefault("src.react", react_pkg)

from langchain_core.messages import AIMessage

from src.react.action_guard import build_command_fingerprint
from src.react.graph_state import CONVERGENCE_GUARD_STREAK_THRESHOLD
from src.react.nodes import call_crash_tool

CACHED_OUTPUT = """crash> rd -x ff292187ae124a80 16
ff292187ae124a80:  ffffffff81a24240 ffffffff81a24240
"""


def _state(*, already_replayed: bool, no_progress_streak: int = 0) -> dict:
    fingerprint = build_command_fingerprint(
        "crash", {"command": "rd -x ff292187ae124a80 16"}
    )
    return {
        "step_count": 10,
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "crash",
                        "args": {"command": "rd -x ff292187ae124a80 16"},
                        "id": "call-1",
                    }
                ],
            )
        ],
        "executed_fingerprints": [fingerprint],
        "tool_output_cache": {fingerprint: CACHED_OUTPUT},
        "replayed_fingerprints": [fingerprint] if already_replayed else [],
        "last_action_fingerprint": "",
        "no_progress_streak": no_progress_streak,
        "duplicate_streak": 0,
        "evidence_facts": [],
        "current_action_intent": {},
    }


def test_first_repeat_replays_and_records_fingerprint():
    update = asyncio.run(call_crash_tool(_state(already_replayed=False)))
    content = update["messages"][0].content
    assert content.startswith("[DEDUP] "), content
    assert not content.startswith("[DEDUP-BLOCKED]")
    assert update["replayed_fingerprints"], "首次回放必须登记指纹"
    assert update["last_action_status"] == "duplicate"


def test_second_repeat_is_blocked_instead_of_replayed():
    update = asyncio.run(call_crash_tool(_state(already_replayed=True)))
    msg = update["messages"][0]
    assert msg.content.startswith("[DEDUP-BLOCKED]"), msg.content
    assert "already replayed" in msg.content
    assert CACHED_OUTPUT not in msg.content, "第二次索取不得再拿到缓存输出"
    assert update["replayed_fingerprints"] == []
    assert update["last_action_status"] == "rejected"
    assert update["replan_required"] is True


def test_second_repeat_below_streak_threshold_gets_no_forced_choice():
    """未连续无进展时只给普通硬拒，不升级为二选一（避免过早掐断取证）。"""
    update = asyncio.run(call_crash_tool(_state(already_replayed=True)))
    content = update["messages"][0].content
    assert content.startswith("[DEDUP-BLOCKED]"), content
    assert "COMMIT A CONCLUSION" not in content


def test_spin_escalates_hard_refusal_to_forced_choice():
    """P0-1：无根因、无门控、已连续无进展时，硬拒必须附带可执行的收敛出口。

    既有收敛护栏要求"根因已提交 + 门控全部关闭"，而真实耗尽预算的 run 恰恰
    从未满足这两项，重复命令又在 dedup 分支提前 `continue`，因此该通道必须
    独立于根因与门控状态触发。
    """
    state = _state(already_replayed=True, no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD)
    assert state.get("current_root_cause_class") is None
    assert "managed_gates" not in state
    update = asyncio.run(call_crash_tool(state))
    content = update["messages"][0].content
    assert content.startswith("[DEDUP-BLOCKED]"), content
    assert "COMMIT A CONCLUSION" in content
    assert "DECLARE A NEW EVIDENCE TARGET" in content
    assert 'not "unknown"' in content
    # 升级不改变 dedup 自身的状态语义
    assert update["last_action_status"] == "rejected"
    assert update["replan_required"] is True


def test_no_evidence_refusal_also_escalates_when_spinning():
    """缓存输出为 echo-only（无实质证据）时同样要给出二选一。"""
    fingerprint = build_command_fingerprint(
        "crash", {"command": "struct irqaction ff292187ae124a80"}
    )
    state = _state(already_replayed=False, no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD)
    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "crash",
                    "args": {"command": "struct irqaction ff292187ae124a80"},
                    "id": "call-2",
                }
            ],
        )
    ]
    state["executed_fingerprints"] = [fingerprint]
    state["tool_output_cache"] = {fingerprint: "crash> struct irqaction ff292187ae124a80\n"}
    update = asyncio.run(call_crash_tool(state))
    content = update["messages"][0].content
    assert content.startswith("[DEDUP-BLOCKED]"), content
    assert "COMMIT A CONCLUSION" in content


def test_non_probe_repeat_does_not_get_forced_choice():
    """回溯等非只读取值类命令不被判为空转探测（收尾报告需要它们）。"""
    fingerprint = build_command_fingerprint("crash", {"command": "bt"})
    state = _state(already_replayed=True, no_progress_streak=CONVERGENCE_GUARD_STREAK_THRESHOLD)
    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[{"name": "crash", "args": {"command": "bt"}, "id": "call-3"}],
        )
    ]
    state["executed_fingerprints"] = [fingerprint]
    state["replayed_fingerprints"] = [fingerprint]
    state["tool_output_cache"] = {
        fingerprint: "crash> bt\nPID: 0  TASK: ffffffff82215000  CPU: 5  COMMAND: \"swapper/5\"\n"
    }
    update = asyncio.run(call_crash_tool(state))
    content = update["messages"][0].content
    assert content.startswith("[DEDUP-BLOCKED]"), content
    assert "COMMIT A CONCLUSION" not in content


if __name__ == "__main__":
    test_first_repeat_replays_and_records_fingerprint()
    test_second_repeat_is_blocked_instead_of_replayed()
    print("ok")
