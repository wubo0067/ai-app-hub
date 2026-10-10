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
from src.react.nodes import call_crash_tool

CACHED_OUTPUT = """crash> rd -x ff292187ae124a80 16
ff292187ae124a80:  ffffffff81a24240 ffffffff81a24240
"""


def _state(*, already_replayed: bool) -> dict:
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
        "no_progress_streak": 0,
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


if __name__ == "__main__":
    test_first_repeat_replays_and_records_fingerprint()
    test_second_repeat_is_blocked_instead_of_replayed()
    print("ok")
