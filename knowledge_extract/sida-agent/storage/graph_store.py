#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全科知识图谱存储：基于 NetworkX 的内存有向图（初中物理/化学/数学通用）。

设计要点：
- 所有实体节点以 `{subject}:{Kind}:{name}` 作为全局唯一键，
  避免不同学科同名概念（如物理"分子"与化学"分子"）互相覆盖。
- 节点属性统一携带 `type`（节点种类）与 `subject`，便于按类检索。
- 实体写库 API 与检索 API 解耦：`add_entity`/`relate` 只做图写入，
  复杂的实体-关系编排在 ingestion.py 完成；`get_subgraph` 提供按知识点
  锚点的 1~2 跳聚合检索，供 Agent 问答链路消费。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import networkx as nx

from logger import get_logger

log = get_logger()

# 节点种类（也是 node_key 的中间段，同时也是节点 type 属性值）
K_SUBJECT = "Subject"
K_CHAPTER = "Chapter"
K_CONCEPT = "Concept"
K_FORMULA = "Formula"
K_EXPERIMENT = "Experiment"
K_QUESTION_TYPE = "QuestionType"
K_METHOD = "Method"
K_EXAMPLE = "Example"

# 关系类型（rel 属性值）
REL_PREREQUISITE_OF = "PREREQUISITE_OF"   # 先修概念 -> 概念（概念拆解前置依赖）
REL_HAS_FORMULA = "HAS_FORMULA"           # 概念 -> 公式
REL_HAS_EXPERIMENT = "HAS_EXPERIMENT"     # 概念 -> 实验
REL_HAS_METHOD = "HAS_METHOD"             # 概念 -> 方法
REL_TRACES_TO = "TRACES_TO"               # 题型 -> 概念（题型溯源到考点概念）
REL_EXEMPLIFIED_BY = "EXEMPLIFIED_BY"     # 题型/方法 -> 例题
REL_TESTS = "TESTS"                       # 例题 -> 概念（原题考到的知识点）
REL_EXTRA = "EXTRA"                       # 其它补充关系

# 存储时不需要入检索/向量回表的实体类型
_RETRIEVABLE = (K_CONCEPT, K_FORMULA, K_EXPERIMENT, K_QUESTION_TYPE, K_METHOD, K_EXAMPLE)


def node_key(subject: str, kind: str, name: str) -> str:
    """生成学科命名空间下的全局节点键。"""
    return f"{subject}:{kind}:{name}"


def bare_name(node_key_: str) -> str:
    """去掉 `subject:Kind:` 前缀，还原展示用名称。"""
    return node_key_.split(":", 2)[-1]


class ScienceGraphStore:
    """初中理科全科知识图谱（内存版）。"""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    # ------------------------------------------------------------------ 写库
    def add_entity(self, subject: str, kind: str, name: str, **attrs: Any) -> str:
        """新增/覆盖一个实体节点。key = subject:Kind:name。"""
        key = node_key(subject, kind, name)
        attrs.setdefault("type", kind)
        attrs["subject"] = subject
        self.graph.add_node(key, **attrs)
        log.debug("[graph_store] 添加节点: %s", key)
        return key

    def relate(self, source: str, relation: str, target: str) -> None:
        """新增一条有向边（source/target 为 node_key，节点不存在时自动补建空节点）。"""
        self.graph.add_edge(source, target, relation=relation)
        log.debug("[graph_store] 添加关系: %s -[%s]-> %s", source, relation, target)

    def add_relation(self, subject: str, kind_a: str, name_a: str,
                     relation: str, kind_b: str, name_b: str) -> None:
        """带学科前缀的便捷建边：把两个裸名实体转为 node_key 后连边。"""
        self.relate(node_key(subject, kind_a, name_a), relation, node_key(subject, kind_b, name_b))

    # --------------------------------------------------------------- 检索
    def get_by_name(self, subject: str, kind: str, name: str) -> Optional[Dict[str, Any]]:
        """按学科+种类+名称反查单个实体；若实体存在且关联了概念，返回其属性与锚定概念。

        用途：知识点锚点未直接命中 Concept 节点时（例如锚点其实是题型/方法名），
        通过本方法找到关联概念，再对概念做 get_subgraph 聚合检索。
        """
        key = node_key(subject, kind, name)
        if key not in self.graph:
            return None
        nd = self.graph.nodes[key]
        # 找相邻的 Concept 节点作为锚定概念（不区分边方向）
        anchor = None
        for nb in list(self.graph.successors(key)) + list(self.graph.predecessors(key)):
            if self.graph.nodes[nb].get("type") == K_CONCEPT:
                anchor = bare_name(nb)
                break
        if anchor is None:
            return None
        info: Dict[str, Any] = {"id": key, "name": bare_name(key), "related_concept": anchor}
        for k_, v_ in nd.items():
            if k_ not in ("type", "subject"):
                info[k_] = v_
        return info

    def get_subgraph(self, subject: str, concept_name: str) -> Dict[str, Any]:
        """按知识点锚点做 1~2 跳聚合检索。

        返回 concept 自身的拆解信息 + 关联的公式/实验/题型/方法/例题，
        供问答链路组装教研上下文。
        """
        result: Dict[str, Any] = {
            "subject": subject,
            "concept": None,
            "prerequisites": [],   # 先修概念（概念拆解的前置链）
            "formulas": [],
            "experiments": [],
            "question_types": [],
            "methods": [],
            "examples": [],
        }
        ckey = node_key(subject, K_CONCEPT, concept_name)
        if ckey not in self.graph:
            log.warning("[graph_store] 图谱中不存在概念节点: %s", ckey)
            return result
        cdata = self.graph.nodes[ckey]
        result["concept"] = {
            "name": concept_name,
            "chapter": cdata.get("chapter", ""),
            "description": cdata.get("description", ""),
            "breakdown": list(cdata.get("breakdown", [])),
            "common_mistakes": list(cdata.get("common_mistakes", [])),
        }
        log.debug("[graph_store] 从 %s 出发检索 1~2 跳子图", ckey)

        hop_buckets: Dict[str, List[Any]] = {k: [] for k in _RETRIEVABLE}
        seen_keys: set = {ckey}

        # 1 跳：出边与入边都可能是关联实体（关系方向见 REL_* 常量）
        for nb in list(self.graph.successors(ckey)) + list(self.graph.predecessors(ckey)):
            if nb in seen_keys:
                continue
            seen_keys.add(nb)
            nd = self.graph.nodes[nb]
            if nd.get("type") in _RETRIEVABLE:
                hop_buckets[nd["type"]].append((nb, nd))

        # 2 跳：沿题型/方法节点取挂载例题（EXEMPLIFIED_BY）
        for kind in (K_QUESTION_TYPE, K_METHOD):
            for nb, nd in hop_buckets[kind]:
                for ex_key in self.graph.successors(nb):
                    if ex_key in seen_keys or self.graph.nodes[ex_key].get("type") != K_EXAMPLE:
                        continue
                    seen_keys.add(ex_key)
                    hop_buckets[K_EXAMPLE].append((ex_key, self.graph.nodes[ex_key]))

        # ---- 归类输出
        for key, nd in hop_buckets[K_CONCEPT]:
            result["prerequisites"].append({
                "name": bare_name(key),
                "description": nd.get("description", ""),
            })
        for key, nd in hop_buckets[K_FORMULA]:
            result["formulas"].append({
                "name": bare_name(key),
                "expression": nd.get("expression", ""),
                "symbols": list(nd.get("symbols", [])),
                "applicable_scope": nd.get("applicable_scope", ""),
                "derivation": list(nd.get("derivation", [])),
            })
        for key, nd in hop_buckets[K_EXPERIMENT]:
            result["experiments"].append({
                "name": bare_name(key),
                "purpose": nd.get("purpose", ""),
                "apparatus": list(nd.get("apparatus", [])),
                "steps": list(nd.get("steps", [])),
                "phenomenon": nd.get("phenomenon", ""),
                "conclusion": nd.get("conclusion", ""),
                "diagram": nd.get("diagram", ""),
                "exam_focus": list(nd.get("exam_focus", [])),
            })
        for key, nd in hop_buckets[K_QUESTION_TYPE]:
            result["question_types"].append({
                "name": bare_name(key),
                "identify_features": list(nd.get("identify_features", [])),
                "template": list(nd.get("template", [])),
                "traps": list(nd.get("traps", [])),
            })
        for key, nd in hop_buckets[K_METHOD]:
            result["methods"].append({
                "name": bare_name(key),
                "scope": nd.get("scope", ""),
                "steps": list(nd.get("steps", [])),
            })
        for key, nd in hop_buckets[K_EXAMPLE]:
            result["examples"].append({
                "id": key,  # 与向量库 metadata["id"] 一致，供回表取原题全文
                "title": nd.get("title", ""),
                "question_type": nd.get("question_type", ""),
                "source": nd.get("source", {}),
            })
        return result