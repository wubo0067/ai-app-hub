#!/usr,bin/,nv p:t,on3:,
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
import re
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

# 章节兜底：query 剥离常见教学词后，与章节主题词需形成互含子串才判定命中；
# 主题词/剥离后 query 达到该长度才参与判定（防"电路"这类极短词偶然包含）
_CHAPTER_MATCH_MIN_CHARS = 4

# 学生提问中的常见教学语气词/尾缀（章节兜底前先剥掉，避免干扰标题包含匹配）
_TEACHING_WORDS = (
    "请讲解", "帮我讲解", "给我讲解", "介绍一下", "请介绍", "讲一讲", "讲一下",
    "讲解", "介绍", "说说", "谈谈", "分析", "复习", "总结", "归纳",
    "什么叫做", "什么是", "什么叫", "怎么样理解", "如何理解", "怎么理解",
    "如何", "怎么", "怎样", "理解", "掌握", "学习",
    "的讲解", "的内容", "的知识点", "的知识", "一下",
    "思路", "怎么做", "如何做",
)

# 章节标题的常见编号前缀（"模块一"/"第一章"/"1.3"/"第二单元 我们周围的空气"…）。
# 学生提问从不带章号，章节匹配前必须剥掉编号，标题才能与提问做包含判定。
_CHAPTER_TITLE_PREFIX_RE = re.compile(
    r"^(?:第[0-9一二三四五六七八九十百千]+(?:单元|模块|讲|课|章|节|部分)"
    r"|模块[0-9一二三四五六七八九十百千]+"
    r"|[0-9]+(?:\.[0-9]+)*)[：:、\s]*"
)

# 章节主题词参与"被提问包含"判定的最小长度（挡单字噪点）
_CHAPTER_THEME_MIN_CHARS = 2


def _chapter_theme(ch: str) -> str:
    """剥掉章号前缀并去空白，得到可参与包含判定的主题词。"""
    t = _CHAPTER_TITLE_PREFIX_RE.sub("", ch or "")
    return "".join(t.split())


def _lcs_len(a: str, b: str) -> int:
    """两串最长公共子串长度（DP，O(mn)）。"""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                best = max(best, dp[i][j])
    return best


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

    # ------------------------------------------------------------ 概念合并
    def find_similar_concept(self, subject: str, name: str,
                             threshold: float = _FUZZY_THRESHOLD,
                             require_described: bool = True) -> Optional[str]:
        """在同科已建概念中找与 name 名称相似度最高的概念名（用于人工核对去重）。

        增量建库长期累积难免出现命名漂移（近义新名与旧节点并存），本方法给出
        唯一最相似候选；相似度低于 threshold 返回 None（视为新概念）。
        require_described=True 时跳过无描述的空壳节点：占位引用不应成为合并目标。
        """
        best, best_ratio = None, 0.0
        for n, nd in self.graph.nodes(data=True):
            if nd.get("subject") != subject or nd.get("type") != K_CONCEPT:
                continue
            if require_described and not str(nd.get("description", "")).strip():
                continue
            cand = bare_name(n)
            ratio = difflib.SequenceMatcher(None, name, cand).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
        return best if best_ratio >= threshold else None

    def merge_concepts(self, subject: str, canonical: str, alias: str) -> bool:
        """把冗余概念节点 alias 合并进规范概念 canonical（两概念须为同科）。

        合并动作：alias 的全部入边/出边按原方向与关系重指到 canonical；
        随后按「越建越全」策略把 alias 的属性逐字段并入 canonical（见下方
        注释），最后删除 alias 节点。
        特殊情形：canonical 尚不存在（例如想让某别名升格为规范名）时，把
        alias 节点整体改名为 canonical，等价合并且不丢任何属性。
        返回是否实际执行了合并（两节点键相同/alias 不存在返回 False）。
        """
        alias_key = node_key(subject, K_CONCEPT, alias)
        canonical_key = node_key(subject, K_CONCEPT, canonical)
        if canonical == alias or alias_key not in self.graph:
            return False
        if canonical_key not in self.graph:
            self.graph = nx.relabel_nodes(self.graph, {alias_key: canonical_key})
            log.info("[graph_store] 合并: %s 节点不存在，将 %s 整体改名为 %s",
                     canonical, alias, canonical)
            return True
        # alias 的入边：谁指进 alias，改指 canonical（跳过 canonical 自身回边）
        for src, _dst, rel in list(self.graph.in_edges(alias_key, data="relation")):
            if src != canonical_key:
                self.relate(src, rel or REL_EXTRA, canonical_key)
        # alias 的出边：alias 指向谁，改由 canonical 指向（跳过指向 canonical 自身）
        for _src, dst, rel in list(self.graph.out_edges(alias_key, data="relation")):
            if dst != canonical_key:
                self.relate(canonical_key, rel or REL_EXTRA, dst)
        alias_nd = self.graph.nodes[alias_key]
        canon_nd = self.graph.nodes[canonical_key]
        # 属性合并：与 ingestion._ensure_entity 的「越建越全」策略保持一致。
        # 旧实现仅当 canonical 为空壳（无 description）时才 setdefault 补属性，
        # 但审计报告「疑似重复概念对」恰恰只扫两边都有描述的概念——主场景正是
        # canonical 与 alias 都有内容，旧逻辑下 alias 的 description/breakdown/
        # common_mistakes/sources 会随 remove_node 整体丢失，canonical 纹丝不动，
        # 与"越建越全"背道而驰。改为逐字段合并：type/subject 元数据跳过；
        # 空值不冲刷；列表 union 去重保序；标量字符串保留更长。
        for k_, v_ in alias_nd.items():
            if k_ in ("type", "subject"):
                continue  # 节点元数据：两节点一致，跳过
            if v_ is None or v_ == "" or v_ == [] or v_ == {}:
                continue  # 别名该字段为空：不冲刷 canonical 已有内容
            old = canon_nd.get(k_)
            if old is None or old == "" or old == [] or old == {}:
                canon_nd[k_] = v_  # canonical 缺失/为空：补全
                continue
            if isinstance(old, list) and isinstance(v_, list):
                # 无序要点集合 union 去重保序（concept 无 derivation/template/steps
                # 之类顺序敏感字段，故无需保留更长的特判）
                canon_nd[k_] = old + [x for x in v_ if x not in old]
            elif isinstance(old, str) and isinstance(v_, str) and len(v_) > len(old):
                canon_nd[k_] = v_  # 标量保留更长表述（description/chapter）
        self.graph.remove_node(alias_key)
        log.info("[graph_store] 合并完成: %s -> %s（关系已重指，别名节点已删除）",
                 alias, canonical)
        return True

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
                "pdf_id": nd.get("pdf_id", ""),  # 例题所在 PDF（回表组键用）
            })
        return result

    # ----------------------------------------------------------- 章节级检索
    def resolve_chapter(self, subject: str, query: str) -> Optional[str]:
        """把学生提问映射到教材章节标题（供空壳概念锚点的章节兜底使用）。

        章节式提问（如"讲解简单电路的电功率"，锚点只命中跨模块通用概念"电功率"）
        在概念级检索必然拿不到例题。先剥离提问中的教学语气词（讲解/分析/思路…），
        再与章节主题词（标题剥掉章号/模块号前缀，如"第一章 有理数"→"有理数"）做
        互含子串判定：短主题词被提问完整包含、或提问是主题词的子串，才视为可靠命中；
        仅共享"电功率"这类高频词不触发。信号不足或章节间歧义时返回 None，交由调用
        方保持原状。
        """
        if not query:
            return None
        qn = "".join(query.split())
        for w in _TEACHING_WORDS:
            qn = qn.replace(w, "")
        if len(qn) < _CHAPTER_MATCH_MIN_CHARS:
            return None
        hits: List[tuple] = []
        seen_ch: set = set()
        for _nid, nd in self.graph.nodes(data=True):
            if (nd.get("subject") != subject or nd.get("type") != K_CONCEPT
                    or not nd.get("chapter") or nd["chapter"] in seen_ch):
                continue
            seen_ch.add(nd["chapter"])
            theme = _chapter_theme(nd["chapter"])
            if len(theme) < _CHAPTER_THEME_MIN_CHARS:
                continue
            # 互含子串（主题词必须完整出现）：短主题词(有理数)被提问整含，
            # 或提问是主题词(总功率及电功率的复杂计算)的子串 → 命中
            if theme in qn or qn in theme:
                hits.append((_lcs_len(qn, theme),
                            difflib.SequenceMatcher(None, qn, theme).ratio(),
                            nd["chapter"]))
        if not hits:
            return None
        hits.sort(reverse=True)
        best_lcs, best_ratio, best_ch = hits[0]
        # 次优与最优同 lcs 且比率接近 -> 歧义，宁可不猜
        if len(hits) >= 2:
            _l2, _r2, _ = hits[1]
            if best_lcs == _l2 and best_ratio - _r2 < 0.05:
                return None
        log.debug("[graph_store] 章节兜底: query=%r 命中章节 %r (qn=%r)",
                  query, best_ch, qn)
        return best_ch

    def get_chapter_subgraph(self, subject: str, chapter_title: str,
                             max_per_kind: Optional[int] = _DEFAULT_MAX_PER_KIND) -> Dict[str, Any]:
        """整章聚合检索：合并该章节下所有概念的 1~2 跳子图，按名称/例题 id 去重。

        返回结构与 get_subgraph 兼容，差异：concept=None、concepts 为该章全部概念的
        拆解信息（供下游多概念渲染），prerequisites/follow_ups/related_concepts 同样
        合并去重；各关联实体先不限量合并、末尾再统一按 max_per_kind 截断，防 prompt 膨胀。
        """
        result: Dict[str, Any] = {
            "subject": subject,
            "concept": None,
            "concepts": [],
            "chapter": chapter_title,
            "prerequisites": [],
            "follow_ups": [],
            "related_concepts": [],
            "formulas": [],
            "experiments": [],
            "question_types": [],
            "methods": [],
            "examples": [],
        }
        members = sorted(
            bare_name(nid) for nid, nd in self.graph.nodes(data=True)
            if (nd.get("subject") == subject and nd.get("type") == K_CONCEPT
                and nd.get("chapter") == chapter_title)
        )
        if not members:
            log.warning("[graph_store] 章节 %r 下无概念成员，无法聚合", chapter_title)
            return result

        def _dedup_merge(dst_key: str, items: List[Dict[str, Any]], uid: str) -> None:
            dst = result[dst_key]
            seen_ids = {d[uid] for d in dst}
            for it in items:
                if it.get(uid) not in seen_ids:
                    dst.append(it)
                    seen_ids.add(it[uid])

        for name in members:
            sub = self.get_subgraph(subject, name, max_per_kind=None)  # 先不限量，末尾统一截断
            cd = sub.get("concept")
            if cd:
                result["concepts"].append(cd)
            _dedup_merge("prerequisites", sub.get("prerequisites", []), "name")
            _dedup_merge("follow_ups", sub.get("follow_ups", []), "name")
            _dedup_merge("related_concepts", sub.get("related_concepts", []), "name")
            _dedup_merge("formulas", sub.get("formulas", []), "name")
            _dedup_merge("experiments", sub.get("experiments", []), "name")
            _dedup_merge("question_types", sub.get("question_types", []), "name")
            _dedup_merge("methods", sub.get("methods", []), "name")
            _dedup_merge("examples", sub.get("examples", []), "id")

        for kind in ("formulas", "experiments", "question_types", "methods", "examples"):
            if max_per_kind is not None and len(result[kind]) > max_per_kind:
                log.info("[graph_store] 章节 %r 聚合后 %s 共 %d 条，截断至 top-%d",
                         chapter_title, kind, len(result[kind]), max_per_kind)
                result[kind] = result[kind][:max_per_kind]
        log.info("[graph_store] 章节聚合: %s（概念 %d）-> 公式 %d, 题型 %d, 方法 %d, 例题 %d",
                 chapter_title, len(result["concepts"]), len(result["formulas"]),
                 len(result["question_types"]), len(result["methods"]), len(result["examples"]))
        return result