#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移：为既有知识图谱回填教材出处（零 LLM）。

背景：公式/实验/题型/方法/章节实体在旧版 ingestion 中不记录来源 pdf_id，
且没有 pdf_id -> 教材显示名 注册表，导致问答标注只能写「教材知识点，图谱收录」
而给不出具体书名。本脚本：

1. 登记 PdfSource 节点（pdf_id -> 显示名，显示名取历史建库日志里的 PDF 文件名）；
2. 按边关系传播 Concept 已有的 sources，回填：
   - Formula    <- HAS_FORMULA    概念出边
   - Experiment <- HAS_EXPERIMENT 概念出边
   - Method     <- HAS_METHOD     概念出边
   - QuestionType <- TRACES_TO 出边指向的概念 + EXEMPLIFIED_BY 挂载的例题 pdf_id
   - Chapter    <- 归属该章节（chapter 属性逐字匹配标题）的概念
3. 保存回 output/knowledge_graph.json（幂等：sources 为并集合并，重复跑不产生重复项）。

Concept 缺 sources 的节点不猜测、不传播（可能是历史脏数据），保持为空。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import networkx as nx

from storage.graph_store import (
    K_CHAPTER,
    K_CONCEPT,
    K_EXPERIMENT,
    K_FORMULA,
    K_METHOD,
    K_QUESTION_TYPE,
    ScienceGraphStore,
)

# 历史建库日志（output/sida_agent.log「流水线启动, PDF=...」）中确认的两个来源，
# 显示名按用户选择直接用 PDF 文件名（去扩展名）。
PDF_NAMES: Dict[str, str] = {
    "3c62fbd60298934d": "9S合并PDF-1",
    "8babbb8eac81e3cb": "9S合并PDF-完整",
}


def _union(node: dict, pids: List[str]) -> bool:
    """把 pids 并入 node['sources']（去重保序）；有变化返回 True。"""
    cur = list(node.get("sources") or [])
    seen = set(cur)
    add = []
    for p in pids:
        if p and p not in seen:
            seen.add(p)
            add.append(p)
    if add:
        node["sources"] = cur + add
        return True
    return False


def migrate(store: ScienceGraphStore) -> Dict[str, int]:
    g = store.graph
    stats: Dict[str, int] = {}

    # 0) 规范化：去除历史写入产生的 sources 重复项（幂等修复）
    for _n, nd in g.nodes(data=True):
        src = nd.get("sources")
        if isinstance(src, list) and len(src) != len(set(src)):
            nd["sources"] = list(dict.fromkeys(str(x) for x in src))

    # 1) 教材名注册表
    for pid, name in PDF_NAMES.items():
        store.register_pdf_name(pid, name)
    stats["PdfSource"] = len(PDF_NAMES)

    # 2) 沿边传播 Concept 的 sources（用快照防止本轮回填影响本轮传播）
    concept_src = {
        n: list(nd.get("sources") or [])
        for n, nd in g.nodes(data=True)
        if nd.get("type") == K_CONCEPT
    }

    def _propagate(rel: str, target_type: str, label: str) -> None:
        changed = 0
        for c, pids in concept_src.items():
            if not pids:
                continue
            for nb in g.successors(c):
                if g.nodes[nb].get("type") != target_type:
                    continue
                if g[c][nb].get("relation") != rel:
                    continue
                if _union(g.nodes[nb], pids):
                    changed += 1
        stats[label] = changed

    _propagate("HAS_FORMULA", K_FORMULA, "Formula")
    _propagate("HAS_EXPERIMENT", K_EXPERIMENT, "Experiment")
    _propagate("HAS_METHOD", K_METHOD, "Method")

    # 题型：TRACES_TO 溯源概念 + EXEMPLIFIED_BY 挂载例题的 pdf_id
    qt_pids: Dict[str, List[str]] = defaultdict(list)
    for n in [k for k, nd in g.nodes(data=True) if nd.get("type") == K_QUESTION_TYPE]:
        for c in g.successors(n):  # 题型 -TRACES_TO-> 概念
            if g[n][c].get("relation") == "TRACES_TO":
                qt_pids[n].extend(concept_src.get(c, []))
        for ex in g.successors(n):  # 题型 -EXEMPLIFIED_BY-> 例题
            if g[n][ex].get("relation") == "EXEMPLIFIED_BY":
                qt_pids[n].append(str(g.nodes[ex].get("pdf_id") or ""))
    stats["QuestionType"] = sum(_union(g.nodes[n], p) for n, p in qt_pids.items())

    # 章节：归属概念（Concept.chapter 属性 == 章节标题）的 sources 并集
    ch_by_title: Dict[str, str] = {
        nd.get("title", ""): n
        for n, nd in g.nodes(data=True)
        if nd.get("type") == K_CHAPTER and nd.get("title")
    }
    ch_pids: Dict[str, List[str]] = defaultdict(list)
    for c, pids in concept_src.items():
        title = g.nodes[c].get("chapter", "")
        key = ch_by_title.get(title)
        if key and pids:
            ch_pids[key].extend(pids)
    stats["Chapter"] = sum(_union(g.nodes[n], p) for n, p in ch_pids.items())

    # 概念：经 PREREQUISITE_OF / 自定义边与已标注概念直连、且自身无 sources 的，
    # 不回填（见模块 docstring），仅统计现状。
    stats["Concept-missing"] = sum(1 for p in concept_src.values() if not p)
    return stats


def main() -> None:
    store = ScienceGraphStore.load()
    stats = migrate(store)
    path = store.save()
    print("迁移完成 ->", path)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    # 复核：注册表可读回
    print("  PdfSource 表:", store.pdf_names())


if __name__ == "__main__":
    main()
