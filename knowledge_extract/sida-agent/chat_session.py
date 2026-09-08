#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat 多轮会话后端：checkpointer 连接 + 会话清单/导出。

langgraph-checkpoint-sqlite 是纯同步后端（SqliteSaver），因此 chat REPL
采用与 ask 相同的同步调用链：create_circuit_agent(checkpointer=saver) 编译，
每轮以 {"configurable": {"thread_id": 会话id}} 调 agent.stream，消息由
checkpointer 逐轮落盘 output/chat/checkpoints.sqlite。

- 同进程保持单连接：with open_saver() 包住整个 REPL 主循环；
- 跨进程续聊：--session <id> 指定既有 thread_id，checkpoint 自动恢复历史；
- 会话导出读取该会话「最新 checkpoint 快照」的 messages 通道（累积快照，
  含全部尚未截断的消息）；被 manage_context 截断的更早部分由 history_summary
  覆盖摘要，导出时置于文件头部说明。
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

# 检查点落盘目录（相对 sida-agent 工作目录，与 vector_db/graph 同根 output/）
_CHAT_DB_DIR = Path("output") / "chat"


def chat_db_path() -> Path:
    """检查点 sqlite 文件路径。"""
    return _CHAT_DB_DIR / "checkpoints.sqlite"


@contextmanager
def open_saver() -> Iterator[SqliteSaver]:
    """打开 chat 检查点连接（生命周期内单连接，退出自动 close）。"""
    db = chat_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(db)) as saver:
        yield saver


def _latest_tuple(saver: SqliteSaver, thread_id: str) -> Optional[Any]:
    """取某会话最新 checkpoint tuple（无会话返回 None）。

    SqliteSaver.list 未承诺返回顺序，这里遍历该会话全部后按 checkpoint.ts
    取最大（ts 为 UTC ISO 时间串，字典序即时间序）。
    """
    best: Optional[Any] = None
    best_ts = ""
    for cp in saver.list({"configurable": {"thread_id": thread_id}}):
        ts = (cp.checkpoint or {}).get("ts") or ""
        if ts > best_ts:
            best_ts = ts
            best = cp
    return best


def _msg_text(msg: AnyMessage) -> str:
    """取消息正文（content 为 str 或文本块列表时均返回纯文本）。"""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                       for b in c)
    return str(c)


def _normalize_math_delims(text: str) -> str:
    """\\[ \\] / \\( \\) 公式定界符 -> $$ / $（跨阅读器通用，同 main.py 版本）。

    chat_session 独立维护一份，避免与 main 相互循环导入。
    """
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]",
                  lambda m: "$$\n" + m.group(1).strip() + "\n$$", text, flags=re.S)
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)",
                  lambda m: "$" + m.group(1).strip() + "$", text, flags=re.S)
    return text


def _all_thread_ids() -> List[str]:
    """直接 SQL 列出存在过的 thread_id（不依赖 saver.list 语义差异）。"""
    db = chat_db_path()
    if not db.exists():
        return []
    ids: List[str] = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for row in con.execute("SELECT DISTINCT thread_id FROM checkpoints"):
                if row[0] not in ids:
                    ids.append(row[0])
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return ids


def list_sessions() -> List[dict]:
    """列出所有会话概览（按最近更新倒序）。

    返回 [{thread_id, updated_at, turns, first_question, chars}]；
    库不存在/无会话时返回空表。
    """
    out: List[dict] = []
    if not chat_db_path().exists():
        return out
    with open_saver() as saver:
        for tid in _all_thread_ids():
            cp = _latest_tuple(saver, tid)
            if cp is None:
                continue
            values = (cp.checkpoint or {}).get("channel_values", {}) or {}
            msgs = list(values.get("messages", []) or [])
            first = ""
            for m in msgs:
                if isinstance(m, HumanMessage):
                    first = _msg_text(m).strip().replace("\n", " ")
                    break
            out.append({
                "thread_id": tid,
                "updated_at": (cp.checkpoint or {}).get("ts", ""),
                "turns": sum(1 for m in msgs if isinstance(m, HumanMessage)),
                "first_question": first[:60],
                "chars": sum(len(_msg_text(m)) for m in msgs),
            })
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def session_snapshot(saver: SqliteSaver, thread_id: str
                     ) -> Tuple[str, List[AnyMessage]]:
    """返回 (history_summary, messages) 最新快照；无会话返回 ("", [])。"""
    cp = _latest_tuple(saver, thread_id)
    if cp is None:
        return "", []
    values = (cp.checkpoint or {}).get("channel_values", {}) or {}
    return ((values.get("history_summary") or ""),
            list(values.get("messages", []) or []))


def export_session_md(thread_id: str,
                      out_dir: Optional[Path] = None) -> Optional[Path]:
    """把整段会话导出为 Markdown（/export、--export 用），返回文件路径。

    无该会话时返回 None。回答正文做公式定界符兜底归一化（同 main 保存 md
    的处理），保证任意 Markdown 阅读器可读。
    """
    out_dir = out_dir or (_CHAT_DB_DIR / "exports")
    with open_saver() as saver:
        summary, msgs = session_snapshot(saver, thread_id)
    if not msgs:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    safe_tid = re.sub(r"[^\w\-]+", "_", thread_id) or "session"
    path = out_dir / f"session_{safe_tid}_{now:%Y%m%d_%H%M%S}.md"
    lines = [
        "# 学习会话记录", "",
        f"- 导出时间：{now:%Y-%m-%d %H:%M:%S}",
        f"- 会话 ID：{thread_id}",
        f"- 对话轮数：{sum(1 for m in msgs if isinstance(m, HumanMessage))}", "",
    ]
    if summary:
        lines += ["## 更早对话摘要（超上下文预算被截断前自动压缩）", "",
                  summary.strip(), ""]
    # 按 Human 提问 + 其后 AI 回答成组；末尾悬空提问单列
    pairs: List[Tuple[str, str]] = []
    cur_q = ""
    for m in msgs:
        if isinstance(m, HumanMessage):
            if cur_q:
                pairs.append((cur_q, ""))
            cur_q = _msg_text(m).strip()
        elif isinstance(m, AIMessage):
            if cur_q:
                pairs.append((cur_q, _msg_text(m).strip()))
                cur_q = ""
    if cur_q:
        pairs.append((cur_q, ""))
    for i, (q, a) in enumerate(pairs, 1):
        lines += [f"## 第 {i} 轮", "", f"**学生**：{q}", ""]
        if a:
            lines += ["**讲解**：", "", _normalize_math_delims(a), ""]
        else:
            lines += ["（该轮暂无回答）", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
