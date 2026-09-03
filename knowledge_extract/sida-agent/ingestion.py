#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识抽取入库：把「PDF 页 Markdown」提炼为初中理科全科知识网络。

支持学科：physics（物理）/ chemistry（化学）/ math（数学），subject 由调用方
显式传入（build_knowledge_bases(subject=...)），同一 graph_db / vector_db 可被
多个学科多次调用累积，形成全科知识库。

抽取本体（一套跨学科通用 schema）：
- chapters         章节骨架
- concepts         概念：定义 + 概念拆解 breakdown + 易错点 + 先修前置 prerequisites
- formulas         公式/定理：表达式 + 符号表 + 适用条件 + 推导步骤 derivation
- experiments      实验：目的/器材/步骤/现象/结论 + 装置图解 diagram + 考点
- question_types   题型：识别特征 + 解题模板 + 陷阱（可溯源到考点概念）
- examples         例题：原题全文 + 答案 + 解析 + 结构化出处 source + 归属题型
- methods          通法技巧
- extra_relations  补充关系

入库策略：
1. 图库（ScienceGraphStore）：实体为节点（subject:Kind:name 学科命名空间隔离），
   依据 prerequisites / related_concepts / question_type 字段自动建边，
   形成「概念拆解链 -> 公式/实验 -> 题型 -> 例题」的可溯源检索结构；
2. 向量库（Chroma）：各类实体各存一份可检索文本（metadata["id"] 与图节点键
   一致），供图谱命中后精确回表取全文。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import get_llm
from logger import get_logger
from storage.graph_store import (
    K_CONCEPT,
    K_EXAMPLE,
    K_EXPERIMENT,
    K_FORMULA,
    K_METHOD,
    K_QUESTION_TYPE,
    K_SUBJECT,
    REL_EXEMPLIFIED_BY,
    REL_EXTRA,
    REL_HAS_EXPERIMENT,
    REL_HAS_FORMULA,
    REL_HAS_METHOD,
    REL_PREREQUISITE_OF,
    REL_TESTS,
    REL_TRACES_TO,
    ScienceGraphStore,
    node_key,
)
from storage.vector_store import get_vector_store

log = get_logger()

# 学科元信息：label 用于提示词与展示；guide 为各学科的抽取引导（核心教学强调点）
SUBJECT_META: Dict[str, Dict[str, str]] = {
    "physics": {
        "label": "物理",
        "guide": (
            "- 概念：物理量给出定义与物理意义；规律/定律要写清成立条件。\n"
            "- 公式：用行内 LaTeX（如 $I=U/R$），说明各符号含义与单位、适用条件与常见变形。\n"
            "- 实验：写全器材、操作步骤、观察现象、结论（结论要表述为物理规律）；"
            "电路/装置图用文字描述连接方式（串并联、电表测量对象、滑动变阻器位置等）。\n"
            "- 题型：如动态电路分析、伏安法测电阻、浮力压强综合等，给出题干识别特征与陷阱。"
        ),
    },
    "chemistry": {
        "label": "化学",
        "guide": (
            "- 概念：区分物质的性质（物理/化学性质）与变化（物理/化学变化），"
            "微观解释要讲清分子/原子/离子层面的本质。\n"
            "- 化学用语规范：元素符号、化学式、化学方程式须书写正确，注意配平、"
            "反应条件与↑↓气体/沉淀符号；按 LaTeX（如 $\\mathrm{2H_2 + O_2 \\xrightarrow{点燃} 2H_2O}$）输出。\n"
            "- 实验：写全仪器、药品、操作步骤与关键现象（颜色变化、气泡、沉淀、放热等），"
            "结论要回答探究目的。\n"
            "- 题型：如物质推断、实验探究、化学式计算、溶液计算等，给出识别特征。"
        ),
    },
    "math": {
        "label": "数学",
        "guide": (
            "- 概念：按「定义 -> 性质 -> 判定/定理」三层拆解，每个要点都要可讲授。\n"
            "- 公式/定理：给出推导思路与成立条件，注明初中几何中常见辅助线做法。\n"
            "- 模型/题型：强调模型识别（如一次函数图象分析、将军饮马、圆中角的关系），"
            "模板步骤化，并说明典型陷阱（分类讨论、漏解、单位等）。\n"
            "- 例题：几何题在 content 中用文字把图形要素与已知条件描述完整。"
        ),
    },
}

_VALID_SUBJECT_ALIASES = {
    "physics": "physics", "物理": "physics", "wuli": "physics",
    "chemistry": "chemistry", "化学": "chemistry", "huaxue": "chemistry",
    "math": "math", "mathematics": "math", "数学": "math", "shuxue": "math",
}


def _normalize_subject(subject: str) -> str:
    """把用户传入的学科写法归一化为内部标识，非法值直接报错。"""
    key = str(subject).strip().lower()
    if key not in _VALID_SUBJECT_ALIASES:
        raise ValueError(f"不支持的学科 '{subject}'，可选：physics/chemistry/math（或中文 物理/化学/数学）")
    return _VALID_SUBJECT_ALIASES[key]


def _strip_code_fence(text: str) -> str:
    """去掉 LLM 可能附带的三反引号围栏。"""
    return text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def _build_extraction_prompt(subject: str, markdown: str) -> str:
    """按学科生成抽取提示词（含学科引导 + 通用 JSON schema）。"""
    label = SUBJECT_META[subject]["label"]
    guide = SUBJECT_META[subject]["guide"]
    return f"""你是深耕初中{label}教学的教研专家。请从下面的教材/讲义文本中提取知识网络，
只输出一个严格的 JSON 对象（不要 ```json 围栏、不要任何解释文字）：

{markdown}

【学科】{label}
{guide}

【JSON 结构（所有字段均可选，没有的内容填空数组/空串，严禁编造教材里不存在的实体）】
{{
  "chapters": [{{"title": "章节标题", "summary": "本章概要"}}],
  "concepts": [
    {{"name": "概念名", "description": "一句话精确定义", "breakdown": ["拆解要点1", "拆解要点2"],
      "common_mistakes": ["易错点"], "prerequisites": ["先修概念名"], "chapter": "所属章节标题"}}
  ],
  "formulas": [
    {{"name": "公式/定理名", "expression": "$I=U/R$", "symbols": [{{"symbol": "I", "meaning": "含义", "unit": "单位"}}],
      "applicable_scope": "适用条件", "derivation": ["推导/变形步骤"], "related_concepts": ["关联概念名"]}}
  ],
  "experiments": [
    {{"name": "实验名", "purpose": "实验目的", "apparatus": ["器材1"], "steps": ["步骤1"],
      "phenomenon": "现象", "conclusion": "结论", "diagram": "装置/电路连接的文字图解描述",
      "exam_focus": ["常考考点"], "related_concepts": ["关联概念名"]}}
  ],
  "question_types": [
    {{"name": "题型名", "identify_features": ["题干识别特征"], "template": ["解题模板步骤"],
      "traps": ["常见陷阱"], "related_concepts": ["考点概念名"]}}
  ],
  "examples": [
    {{"id": "原题编号如 例17", "title": "小标题",
      "content": "原题完整题干（含数值、选项、图表关键信息，不能省略）",
      "answer": "答案", "analysis": "解析思路",
      "question_type": "归属题型名（与上方 question_types.name 对应，无则填空串）",
      "source": {{"book": "教材/册", "chapter": "章节", "page": 页码, "number": "题号"}},
      "related_concepts": ["考点概念名"]}}
  ],
  "methods": [
    {{"name": "方法名", "scope": "适用场景", "steps": ["步骤"], "related_concepts": ["关联概念名"]}}
  ],
  "extra_relations": [{{"from": "实体名", "rel": "关系英文名", "to": "实体名"}}]
}}

【要求】
1. name 使用原文中的标准名词；例题保留原编号（如"例17"），不要自造编号。
2. 例题 source 按文本或讲义页填写 book/chapter/page/number，缺失字段省略。
3. 这是教研知识库抽取，请把核心价值做足：概念给出 breakdown 拆解要点与易错点，
   公式给出推导/变形思路与适用条件，实验写全器材/步骤/现象/结论，题型给出识别特征与陷阱。
4. 文本中根本没有实验时 experiments 返回 []，不要编造。
"""


def _ensure_entity(graph_db: ScienceGraphStore, subject: str, kind: str,
                   name: str, **attrs: Any) -> str:
    """确保实体节点存在并返回其 node_key；已存在时补充缺失属性。"""
    key = node_key(subject, kind, name)
    if key not in graph_db.graph:
        graph_db.add_entity(subject, kind, name, **attrs)
    else:
        cur = graph_db.graph.nodes[key]
        for k_, v_ in attrs.items():
            cur.setdefault(k_, v_)
    return key


def _write_graph(graph_db: ScienceGraphStore, subject: str, data: Dict[str, Any]) -> None:
    """把抽取出的实体与关系写入图库（核心编排逻辑）。"""
    # --- 章节 ---
    for ch in data.get("chapters", []):
        title = ch.get("title", "").strip()
        if title:
            graph_db.add_entity(subject, "Chapter", title, title=title, summary=ch.get("summary", ""))

    # --- 概念（含先修链）---
    concept_names: List[str] = []
    for c in data.get("concepts", []):
        name = c.get("name", "").strip()
        if not name:
            continue
        concept_names.append(name)
        _ensure_entity(graph_db, subject, K_CONCEPT, name,
                       description=c.get("description", ""),
                       breakdown=list(c.get("breakdown", [])),
                       common_mistakes=list(c.get("common_mistakes", [])),
                       chapter=c.get("chapter", ""))
        for pre in c.get("prerequisites", []):
            pre = str(pre).strip()
            if pre and pre != name:
                pre_key = _ensure_entity(graph_db, subject, K_CONCEPT, pre)
                graph_db.relate(pre_key, REL_PREREQUISITE_OF,
                                node_key(subject, K_CONCEPT, name))

    def _link_concept_refs(refs: Any, rel: str, key: str) -> None:
        """把 related_concepts / prerequisites 等引用转成概念节点出/入边。"""
        for ref in refs or []:
            ref = str(ref).strip()
            if not ref:
                continue
            ckey = _ensure_entity(graph_db, subject, K_CONCEPT, ref)
            if rel == REL_TRACES_TO:      # 题型 -> 概念（溯源）
                graph_db.relate(key, rel, ckey)
            elif rel == REL_HAS_FORMULA or rel == REL_HAS_EXPERIMENT or rel == REL_HAS_METHOD:
                graph_db.relate(ckey, rel, key)
            elif rel == REL_TESTS:        # 例题 -> 概念
                graph_db.relate(key, rel, ckey)

    # --- 公式 / 实验 / 方法（概念 -> 实体）---
    for f in data.get("formulas", []):
        name = f.get("name", "").strip()
        if not name:
            continue
        key = _ensure_entity(graph_db, subject, K_FORMULA, name,
                             expression=f.get("expression", ""),
                             symbols=list(f.get("symbols", [])),
                             applicable_scope=f.get("applicable_scope", ""),
                             derivation=list(f.get("derivation", [])))
        _link_concept_refs(f.get("related_concepts"), REL_HAS_FORMULA, key)

    for e in data.get("experiments", []):
        name = e.get("name", "").strip()
        if not name:
            continue
        key = _ensure_entity(graph_db, subject, K_EXPERIMENT, name,
                             purpose=e.get("purpose", ""),
                             apparatus=list(e.get("apparatus", [])),
                             steps=list(e.get("steps", [])),
                             phenomenon=e.get("phenomenon", ""),
                             conclusion=e.get("conclusion", ""),
                             diagram=e.get("diagram", ""),
                             exam_focus=list(e.get("exam_focus", [])))
        _link_concept_refs(e.get("related_concepts"), REL_HAS_EXPERIMENT, key)

    for m in data.get("methods", []):
        name = m.get("name", "").strip()
        if not name:
            continue
        key = _ensure_entity(graph_db, subject, K_METHOD, name,
                             scope=m.get("scope", ""),
                             steps=list(m.get("steps", [])))
        _link_concept_refs(m.get("related_concepts"), REL_HAS_METHOD, key)

    # --- 题型（溯源到考点概念）---
    qt_keys: Dict[str, str] = {}
    for qt in data.get("question_types", []):
        name = qt.get("name", "").strip()
        if not name:
            continue
        key = _ensure_entity(graph_db, subject, K_QUESTION_TYPE, name,
                             identify_features=list(qt.get("identify_features", [])),
                             template=list(qt.get("template", [])),
                             traps=list(qt.get("traps", [])))
        qt_keys[name] = key
        _link_concept_refs(qt.get("related_concepts"), REL_TRACES_TO, key)

    # --- 例题（题型/方法 -> 例题，例题 -> 概念）---
    for i, ex in enumerate(data.get("examples", []), start=1):
        src = ex.get("source", {}) or {}
        ex_name = str(ex.get("id") or src.get("number") or f"题{i}").strip()
        ex_key = _ensure_entity(graph_db, subject, K_EXAMPLE, ex_name,
                                title=ex.get("title", ""),
                                answer=ex.get("answer", ""),
                                analysis=ex.get("analysis", ""),
                                question_type=ex.get("question_type", ""),
                                source=src)
        qt_name = str(ex.get("question_type") or "").strip()
        if qt_name:
            graph_db.relate(qt_keys.get(qt_name) or _ensure_entity(
                graph_db, subject, K_QUESTION_TYPE, qt_name), REL_EXEMPLIFIED_BY, ex_key)
        _link_concept_refs(ex.get("related_concepts"), REL_TESTS, ex_key)

    # --- 补充关系 ---
    for r in data.get("extra_relations", []):
        src, dst = str(r.get("from", "")).strip(), str(r.get("to", "")).strip()
        if not src or not dst:
            continue
        src_key = _ensure_entity(graph_db, subject, K_CONCEPT, src)
        dst_key = _ensure_entity(graph_db, subject, K_CONCEPT, dst)
        graph_db.relate(src_key, str(r.get("rel") or REL_EXTRA), dst_key)


def _build_vector_docs(subject: str, data: Dict[str, Any]) -> List[Document]:
    """把各类实体各转为一条可检索文本（metadata["id"] 与图节点键一致）。"""
    docs: List[Document] = []

    def _push(kind: str, name: str, content: str, **extra_meta: Any) -> None:
        if not name.strip():
            return
        docs.append(Document(
            page_content=content.strip(),
            metadata={"id": node_key(subject, kind, name), "subject": subject,
                      "type": kind, **extra_meta},
        ))

    for c in data.get("concepts", []):
        name = c.get("name", "")
        lines = [f"概念：{name}", c.get("description", "")]
        if c.get("breakdown"):
            lines.append("拆解要点：" + "；".join(c["breakdown"]))
        if c.get("common_mistakes"):
            lines.append("易错点：" + "；".join(c["common_mistakes"]))
        _push(K_CONCEPT, name, "\n".join(lines), chapter=c.get("chapter", ""))

    for f in data.get("formulas", []):
        name = f.get("name", "")
        lines = [f"公式/定理：{name}", f.get("expression", "")]
        if f.get("symbols"):
            lines.append("符号：" + "；".join(
                f"{s.get('symbol')}={s.get('meaning')}({s.get('unit')})" for s in f["symbols"]))
        if f.get("applicable_scope"):
            lines.append("适用条件：" + f["applicable_scope"])
        if f.get("derivation"):
            lines.append("推导：" + "；".join(f["derivation"]))
        _push(K_FORMULA, name, "\n".join(lines))

    for e in data.get("experiments", []):
        name = e.get("name", "")
        lines = [f"实验：{name}", f"目的：{e.get('purpose', '')}"]
        if e.get("apparatus"):
            lines.append("器材：" + "、".join(e["apparatus"]))
        if e.get("steps"):
            lines.append("步骤：" + "；".join(e["steps"]))
        if e.get("phenomenon"):
            lines.append("现象：" + e["phenomenon"])
        if e.get("conclusion"):
            lines.append("结论：" + e["conclusion"])
        if e.get("diagram"):
            lines.append("装置图解：" + e["diagram"])
        if e.get("exam_focus"):
            lines.append("考点：" + "；".join(e["exam_focus"]))
        _push(K_EXPERIMENT, name, "\n".join(lines))

    for qt in data.get("question_types", []):
        name = qt.get("name", "")
        lines = [f"题型：{name}"]
        if qt.get("identify_features"):
            lines.append("识别特征：" + "；".join(qt["identify_features"]))
        if qt.get("template"):
            lines.append("解题模板：" + "；".join(qt["template"]))
        if qt.get("traps"):
            lines.append("常见陷阱：" + "；".join(qt["traps"]))
        _push(K_QUESTION_TYPE, name, "\n".join(lines))

    for m in data.get("methods", []):
        name = m.get("name", "")
        lines = [f"方法：{name}"]
        if m.get("scope"):
            lines.append("适用场景：" + m["scope"])
        if m.get("steps"):
            lines.append("步骤：" + "；".join(m["steps"]))
        _push(K_METHOD, name, "\n".join(lines))

    for i, ex in enumerate(data.get("examples", []), start=1):
        src = ex.get("source", {}) or {}
        name = str(ex.get("id") or src.get("number") or f"题{i}").strip()
        lines = [f"【{name}】{ex.get('title', '')}"]
        if ex.get("content"):
            lines.append(ex["content"])
        if ex.get("answer"):
            lines.append("答案：" + ex["answer"])
        if ex.get("analysis"):
            lines.append("解析：" + ex["analysis"])
        _push(K_EXAMPLE, name, "\n".join(lines),
              title=ex.get("title", ""), page=src.get("page"))

    return docs


def build_knowledge_bases(
    pages_data: List[Dict[str, Any]],
    subject: str = "physics",
    *,
    text_llm_provider: str = "deepseek",
    vector_db: Optional[Chroma] = None,
    graph_db: Optional[ScienceGraphStore] = None,
) -> Tuple[Chroma, ScienceGraphStore]:
    """从 Markdown 中提取结构化知识网络，写入 Vector DB 与 Graph DB。

    同一 vector_db / graph_db 可跨多次调用、跨学科累积（全科知识库）。
    """
    subject = _normalize_subject(subject)
    log.info("[ingestion] 开始构建知识库: subject=%s, 输入页数=%d, provider=%s",
             SUBJECT_META[subject]["label"], len(pages_data), text_llm_provider)
    llm = get_llm(provider=text_llm_provider, is_vision=False)
    vector_db = vector_db or get_vector_store()
    graph_db = graph_db or ScienceGraphStore()
    if node_key(subject, K_SUBJECT, subject) not in graph_db.graph:
        graph_db.add_entity(subject, K_SUBJECT, subject, label=SUBJECT_META[subject]["label"])

    full_markdown = "\n\n".join(f"--- 第 {p['page']} 页 ---\n{p['content']}" for p in pages_data)
    log.debug("[ingestion] 拼接全文长度=%d 字符", len(full_markdown))

    prompt = _build_extraction_prompt(subject, full_markdown)
    log.debug("[ingestion] 调用 LLM 抽取知识网络 JSON...")
    res = str(llm.invoke(prompt).content).strip()
    clean_json = _strip_code_fence(res)
    try:
        data = json.loads(clean_json)
    except json.JSONDecodeError:
        log.error("[ingestion] LLM 返回非法 JSON，原文前 500 字符: %s", clean_json[:500])
        raise
    if not isinstance(data, dict):
        raise ValueError(f"[ingestion] LLM 返回的不是 JSON 对象: {type(data)}")

    n_kind = {k: len(data.get(k, [])) for k in
              ("chapters", "concepts", "formulas", "experiments",
               "question_types", "examples", "methods", "extra_relations")}
    log.info("[ingestion] JSON 抽取成功: %s",
             ", ".join(f"{k}={v}" for k, v in n_kind.items()))

    # 1. 写入 Graph DB
    _write_graph(graph_db, subject, data)

    # 2. 写入 Vector DB（全文回表）
    docs = _build_vector_docs(subject, data)
    if docs:
        vector_db.add_documents(docs)

    log.info("[ingestion] 构建完成: 图节点 %d 个, 向量切片 %d 条。",
             graph_db.graph.number_of_nodes(), len(docs))
    return vector_db, graph_db