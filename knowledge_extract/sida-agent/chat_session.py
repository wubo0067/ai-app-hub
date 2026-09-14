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
    # 相对路径：以 sida-agent 为工作目录运行时落在 output/chat/checkpoints.sqlite
    return _CHAT_DB_DIR / "checkpoints.sqlite"


@contextmanager
def open_saver() -> Iterator[SqliteSaver]:
    """打开 chat 检查点连接（生命周期内单连接，退出自动 close）。"""
    db = chat_db_path()
    # 首次运行 output/chat 可能不存在，先建目录再让 sqlite 建库文件
    db.parent.mkdir(parents=True, exist_ok=True)
    # from_conn_string 是上下文管理器：进入时建连接，退出时自动 close，
    # 因此调用方用 with open_saver() as saver 包住整段逻辑即可
    with SqliteSaver.from_conn_string(str(db)) as saver:
        yield saver


def _latest_tuple(saver: SqliteSaver, thread_id: str) -> Optional[Any]:
    """取某会话最新 checkpoint tuple（无会话返回 None）。

    SqliteSaver.list 未承诺返回顺序，这里遍历该会话全部后按 checkpoint.ts
    取最大（ts 为 UTC ISO 时间串，字典序即时间序）。
    """
    best: Optional[Any] = None
    best_ts = ""
    # saver.list 只保证「属于该 thread_id」，不保证顺序，故全量遍历取 ts 最大者
    for cp in saver.list({"configurable": {"thread_id": thread_id}}):
        # checkpoint 是 dict，ts 为 UTC ISO 串（如 2026-09-14T03:21:07.123456+00:00）
        ts = (cp.checkpoint or {}).get("ts") or ""
        if ts > best_ts:  # ISO 串字典序 == 时间序，可直接比较
            best_ts = ts
            best = cp
    return best


def _msg_text(msg: AnyMessage) -> str:
    """取消息正文（content 为 str 或文本块列表时均返回纯文本）。"""
    c = msg.content
    # 普通文本消息：content 直接是 str
    if isinstance(c, str):
        return c
    # 多模态/带思考块的消息：content 是 [{"type": "text", "text": ...}, ...]
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                       for b in c)
    # 兜底：其他类型（如 None）转字符串，避免调用方拿到非 str
    return str(c)


def _normalize_math_delims(text: str) -> str:
    """\\[ \\] / \\( \\) 公式定界符 -> $$ / $（跨阅读器通用，同 main.py 版本）。

    chat_session 独立维护一份，避免与 main 相互循环导入。
    """
    # 块级公式：\[ ... \] -> $$ ... $$（re.S 让 . 跨行匹配多行公式）
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]",
                  lambda m: "$$\n" + m.group(1).strip() + "\n$$", text, flags=re.S)
    # 行内公式：\( ... \) -> $ ... $
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
        # mode=ro 只读打开：避免与正在写入的 REPL 进程争锁，也防止误写
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            # checkpoints 表每轮一行，DISTINCT 得到全部会话 id
            for row in con.execute("SELECT DISTINCT thread_id FROM checkpoints"):
                if row[0] not in ids:  # 去重保序（SQL 不保证 DISTINCT 顺序）
                    ids.append(row[0])
        finally:
            con.close()
    except sqlite3.Error:
        # 库损坏/表不存在等：视为无会话，不阻断上层列表展示
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
            if cp is None:  # 该 thread 只有中间态、无完整 checkpoint，跳过
                continue
            # channel_values 是图状态快照：messages 为累积消息列表，
            # history_summary 为 manage_context 截断后写入的摘要
            values = (cp.checkpoint or {}).get("channel_values", {}) or {}
            msgs = list(values.get("messages", []) or [])
            first = ""
            # 首条 HumanMessage 作为会话标题（跳过 System/AI 消息）
            for m in msgs:
                if isinstance(m, HumanMessage):
                    first = _msg_text(m).strip().replace("\n", " ")
                    break
            out.append({
                "thread_id": tid,
                "updated_at": (cp.checkpoint or {}).get("ts", ""),
                # 轮数 = 学生提问次数（一次提问对应一轮）
                "turns": sum(1 for m in msgs if isinstance(m, HumanMessage)),
                "first_question": first[:60],  # 截断，避免列表行过长
                "chars": sum(len(_msg_text(m)) for m in msgs),  # 会话体量参考
            })
    # 最近更新的排前面（ts 为 ISO 串，倒序即时间倒序）
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def session_snapshot(saver: SqliteSaver, thread_id: str
                     ) -> Tuple[str, List[AnyMessage]]:
    """返回 (history_summary, messages) 最新快照；无会话返回 ("", [])。"""
    cp = _latest_tuple(saver, thread_id)
    if cp is None:
        return "", []
    values = (cp.checkpoint or {}).get("channel_values", {}) or {}
    # history_summary 可能为 None（未触发截断），统一成空串
    return ((values.get("history_summary") or ""),
            list(values.get("messages", []) or []))


def export_session_md(thread_id: str,
                      out_dir: Optional[Path] = None) -> Optional[Path]:
    """把整段会话导出为 Markdown（/export、--export 用），返回文件路径。

    无该会话时返回 None。回答正文做公式定界符兜底归一化（同 main 保存 md
    的处理），保证任意 Markdown 阅读器可读。
    """
    out_dir = out_dir or (_CHAT_DB_DIR / "exports")
    # 先在同一连接内取快照，再关闭连接做纯文本处理，缩短持锁时间
    with open_saver() as saver:
        summary, msgs = session_snapshot(saver, thread_id)
    if not msgs:  # 会话不存在或没有任何消息
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    # thread_id 可能含 / : 等非法文件名字符，替换成下划线；全被替换时兜底 session
    safe_tid = re.sub(r"[^\w\-]+", "_", thread_id) or "session"
    path = out_dir / f"session_{safe_tid}_{now:%Y%m%d_%H%M%S}.md"
    # 文件头：导出元信息，便于归档后辨认来源会话
    lines = [
        "# 学习会话记录", "",
        f"- 导出时间：{now:%Y-%m-%d %H:%M:%S}",
        f"- 会话 ID：{thread_id}",
        f"- 对话轮数：{sum(1 for m in msgs if isinstance(m, HumanMessage))}", "",
    ]
    if summary:
        # 被截断的更早对话只剩摘要，放在正文前说明，避免读者误以为会话不完整
        lines += ["## 更早对话摘要（超上下文预算被截断前自动压缩）", "",
                  summary.strip(), ""]
    # 按 Human 提问 + 其后 AI 回答成组；末尾悬空提问单列
    pairs: List[Tuple[str, str]] = []
    cur_q = ""
    for m in msgs:
        if isinstance(m, HumanMessage):
            # 上一问还没等到回答就又提问（如中断/报错），补一条空回答占位
            if cur_q:
                pairs.append((cur_q, ""))
            cur_q = _msg_text(m).strip()
        elif isinstance(m, AIMessage):
            # 只把「紧跟在提问后的第一条 AI 消息」当回答；
            # 工具调用产生的中间 AI 消息因 cur_q 已清空而被忽略
            if cur_q:
                pairs.append((cur_q, _msg_text(m).strip()))
                cur_q = ""
    if cur_q:  # 最后一问没有回答
        pairs.append((cur_q, ""))
    for i, (q, a) in enumerate(pairs, 1):
        lines += [f"## 第 {i} 轮", "", f"**学生**：{q}", ""]
        if a:
            # 回答正文做公式定界符归一化，保证任意 Markdown 阅读器可渲染
            lines += ["**讲解**：", "", _normalize_math_delims(a), ""]
        else:
            lines += ["（该轮暂无回答）", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
