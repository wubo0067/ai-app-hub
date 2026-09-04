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

import difflib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import networkx as nx
from networkx.readwrite import json_graph

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

# 概念锚点的常见教学修饰尾缀（意图判定 LLM 常把"分析/思路/方法/讲解"拼进锚点名）
_CONCEPT_SUFFIXES = (
    "的分析", "的思路", "的方法", "的讲解", "的规律", "的问题",
    "分析", "思路", "方法", "讲解", "总结", "归纳", "综合",
)
# difflib 模糊兜底的相似度阈值
_FUZZY_THRESHOLD = 0.6

# get_subgraph 每类关联实体的默认返回上限：命中"枢纽概念"（关联几十条公式/例题）时
# 截断至 top-N，防止下游问答 prompt 被撑爆；None 表示不限。
_DEFAULT_MAX_PER_KIND = 8

# 存储时不需要入检索/向量回表的实体类型
_RETRIEVABLE = (K_CONCEPT, K_FORMULA, K_EXPERIMENT, K_QUESTION_TYPE, K_METHOD, K_EXAMPLE)

# 图谱默认持久化文件（JSON node_link 格式），支持跨进程累积与独立只读问答。
_DEFAULT_GRAPH_PATH = str(
    Path(__file__).resolve().parent.parent / "output" / "knowledge_graph.json"
)


def node_key(subject: str, kind: str, name: str) -> str:
    """生成学科命名空间下的全局节点键。"""
    return f"{subject}:{kind}:{name}"


def bare_name(node_key_: str) -> str:
    """去掉 `subject:Kind:` 前缀，还原展示用名称。"""
    return node_key_.split(":", 2)[-1]


class ScienceGraphStore:
    """初中理科全科知识图谱（内存图 + 可选 JSON 落盘）。

    运行期为内存 ``nx.DiGraph``；通过 ``save`` / ``load`` 可持久化到
    JSON（node_link 格式），实现跨进程累积与独立只读问答。
    """

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    # ------------------------------------------------------------- 持久化
    def save(self, path: Union[str, Path, None] = None) -> str:
        """把当前图谱序列化为 JSON（node_link 格式）落盘，返回写入路径。"""
        p = str(path or _DEFAULT_GRAPH_PATH)
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        data = json_graph.node_link_data(self.graph, edges="links")
        Path(p).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("[graph_store] 图谱已保存: %s（节点 %d, 边 %d）",
                 p, self.graph.number_of_nodes(), self.graph.number_of_edges())
        return p

    @classmethod
    def load(cls, path: Union[str, Path, None] = None) -> "ScienceGraphStore":
        """从 JSON 文件加载图谱；文件不存在时返回空库（便于首次运行）。"""
        store = cls()
        p = str(path or _DEFAULT_GRAPH_PATH)
        if not Path(p).exists():
            log.info("[graph_store] 图谱文件不存在，新建空库: %s", p)
            return store
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            store.graph = json_graph.node_link_graph(data, edges="links")
            log.info("[graph_store] 已加载图谱: %s（节点 %d, 边 %d）",
                     p, store.graph.number_of_nodes(), store.graph.number_of_edges())
        except Exception:
            log.exception("[graph_store] 图谱加载失败，回退空库: %s", p)
            store.graph = nx.DiGraph()
        return store

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

    def _resolve_concept(self, subject: str, name: str) -> Optional[str]:
        """概念锚点模糊解析：去教学修饰尾缀 -> 双向包含 -> difflib 相似度兜底。

        意图判定 LLM 输出的锚点名常带"分析/思路/方法"等修饰（如
        "可变电路的分析思路"被缩成"可变电路分析"），与图谱概念名
        （如"可变电路"）不完全一致。本方法在同科 Concepts 中逐步放宽
        匹配口径，返回命中的概念名；找不到返回 None。
        """
        if not name:
            return None
        candidates = [
            bare_name(n) for n, nd in self.graph.nodes(data=True)
            if nd.get("subject") == subject and nd.get("type") == K_CONCEPT
        ]
        if not candidates:
            return None
        # 1) 去掉"分析/思路/方法"等教学修饰尾缀后精确命中
        for suffix in _CONCEPT_SUFFIXES:
            if len(name) > len(suffix) and name.endswith(suffix):
                trimmed = name[:-len(suffix)]
                if trimmed in candidates:
                    return trimmed
        # 2) 双向包含（锚点名与节点名互为子串），取相似度最高者
        contained = [
            cand for cand in candidates
            if len(cand) >= 2 and len(name) >= 2 and (cand in name or name in cand)
        ]
        if contained:
            return max(contained, key=lambda c: difflib.SequenceMatcher(None, name, c).ratio())
        # 3) difflib 相似度兜底（覆盖同义改写等场景）
        best, best_ratio = None, 0.0
        for cand in candidates:
            ratio = difflib.SequenceMatcher(None, name, cand).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
        return best if best_ratio >= _FUZZY_THRESHOLD else None

    def get_subgraph(self, subject: str, concept_name: str,
                     max_per_kind: Optional[int] = _DEFAULT_MAX_PER_KIND) -> Dict[str, Any]:
        """按知识点锚点做 1~2 跳聚合检索。

        返回 concept 自身的拆解信息 + 关联的公式/实验/题型/方法/例题，
        供问答链路组装教研上下文。
        max_per_kind：每类关联实体的返回上限（None 不限），命中枢纽概念时截断
        至 top-N 防止下游 prompt 膨胀；examples 截断后回表取原题的页数随之减少。
        """
        result: Dict[str, Any] = {
            "subject": subject,
            "concept": None,
            "prerequisites": [],    # 真先修：nb -PREREQUISITE_OF-> ckey
            "follow_ups": [],       # 后续概念：ckey -PREREQUISITE_OF-> nb
            "related_concepts": [], # 其它概念关联（extra_relations 等，非先修语义）
            "formulas": [],
            "experiments": [],
            "question_types": [],
            "methods": [],
            "examples": [],
        }
        ckey = node_key(subject, K_CONCEPT, concept_name)
        if ckey not in self.graph:
            resolved = self._resolve_concept(subject, concept_name)
            if resolved is not None:
                log.warning("[graph_store] 概念节点 %s 未精确命中，模糊解析到 %s",
                             ckey, node_key(subject, K_CONCEPT, resolved))
                ckey = node_key(subject, K_CONCEPT, resolved)
                concept_name = resolved
            else:
                log.warning("[graph_store] 图谱中不存在概念节点: %s（模糊解析亦未命中）", ckey)
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

        # 1 跳：非概念邻居按类型归类；概念邻居必须按「边方向 + relation」区分，
        # 否则会把后续概念、extra_relations 的任意关联误当成先修。
        hop_buckets: Dict[str, List[Any]] = {
            k: [] for k in _RETRIEVABLE if k != K_CONCEPT
        }
        concept_prereq: List[Any] = []     # nb -PREREQUISITE_OF-> ckey
        concept_followup: List[Any] = []   # ckey -PREREQUISITE_OF-> nb
        concept_related: List[Any] = []    # 概念间其它关系（含 REL_EXTRA / 自定义）
        seen_keys: set = {ckey}

        for nb in self.graph.successors(ckey):        # 出边 ckey -> nb
            if nb in seen_keys:
                continue
            seen_keys.add(nb)
            rel = self.graph[ckey][nb].get("relation")
            nd = self.graph.nodes[nb]
            t = nd.get("type")
            if t == K_CONCEPT:
                if rel == REL_PREREQUISITE_OF:
                    concept_followup.append((nb, nd))
                else:
                    concept_related.append((nb, nd, rel))
            elif t in _RETRIEVABLE:
                hop_buckets[t].append((nb, nd))

        for nb in self.graph.predecessors(ckey):      # 入边 nb -> ckey
            if nb in seen_keys:
                continue
            seen_keys.add(nb)
            rel = self.graph[nb][ckey].get("relation")
            nd = self.graph.nodes[nb]
            t = nd.get("type")
            if t == K_CONCEPT:
                if rel == REL_PREREQUISITE_OF:
                    concept_prereq.append((nb, nd))
                else:
                    concept_related.append((nb, nd, rel))
            elif t in _RETRIEVABLE:
                hop_buckets[t].append((nb, nd))

        # 2 跳：沿题型/方法节点取挂载例题（EXEMPLIFIED_BY）
        for kind in (K_QUESTION_TYPE, K_METHOD):
            for nb, nd in hop_buckets[kind]:
                for ex_key in self.graph.successors(nb):
                    if ex_key in seen_keys or self.graph.nodes[ex_key].get("type") != K_EXAMPLE:
                        continue
                    seen_keys.add(ex_key)
                    hop_buckets[K_EXAMPLE].append((ex_key, self.graph.nodes[ex_key]))

        # ---- 归类输出
        def _capped(kind: str, items: List[Any]) -> List[Any]:
            """按 max_per_kind 截断某类实体，命中枢纽概念时记 warning。"""
            if max_per_kind is not None and len(items) > max_per_kind:
                log.warning("[graph_store] %s 关联 %s 共 %d 条，截断至 top-%d",
                            ckey, kind, len(items), max_per_kind)
                return items[:max_per_kind]
            return items

        for key, nd in concept_prereq:
            result["prerequisites"].append({
                "name": bare_name(key),
                "description": nd.get("description", ""),
            })
        for key, nd in concept_followup:
            result["follow_ups"].append({
                "name": bare_name(key),
                "description": nd.get("description", ""),
            })
        for key, nd, rel in concept_related:
            result["related_concepts"].append({
                "name": bare_name(key),
                "description": nd.get("description", ""),
                "relation": rel or REL_EXTRA,
            })
        for key, nd in _capped("formulas", hop_buckets[K_FORMULA]):
            result["formulas"].append({
                "name": bare_name(key),
                "expression": nd.get("expression", ""),
                "symbols": list(nd.get("symbols", [])),
                "applicable_scope": nd.get("applicable_scope", ""),
                "derivation": list(nd.get("derivation", [])),
            })
        for key, nd in _capped("experiments", hop_buckets[K_EXPERIMENT]):
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
        for key, nd in _capped("question_types", hop_buckets[K_QUESTION_TYPE]):
            result["question_types"].append({
                "name": bare_name(key),
                "identify_features": list(nd.get("identify_features", [])),
                "template": list(nd.get("template", [])),
                "traps": list(nd.get("traps", [])),
            })
        for key, nd in _capped("methods", hop_buckets[K_METHOD]):
            result["methods"].append({
                "name": bare_name(key),
                "scope": nd.get("scope", ""),
                "steps": list(nd.get("steps", [])),
            })
        for key, nd in _capped("examples", hop_buckets[K_EXAMPLE]):
            result["examples"].append({
                "id": key,  # 与向量库 metadata["id"] 一致，供回表取原题全文
                "title": nd.get("title", ""),
                "question_type": nd.get("question_type", ""),
                "source": nd.get("source", {}),
            })
        return result