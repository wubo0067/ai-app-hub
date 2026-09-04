#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初中理科全科问答 Agent 工作流（LangGraph）。

链路：提问 -> 学科+知识点锚点判定 -> 图谱聚合检索（概念拆解/公式/实验/题型/例题）
-> 向量库按 id 回表取例题全文 -> 生成分层讲解（概念拆解先行、公式推导、题型溯源）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_chroma import Chroma
from langgraph.graph import END, START, StateGraph

from agent.state import CircuitAgentState
from config import get_reasoning_llm
from logger import get_logger
from storage.graph_store import (
    K_EXAMPLE,
    K_METHOD,
    K_QUESTION_TYPE,
    ScienceGraphStore,
)

log = get_logger()

# 学科显示名（与 ingestion.SUBJECT_META 呼应，避免循环导入故本地维护一份）
_SUBJECT_LABEL = {"physics": "物理", "chemistry": "化学", "math": "数学"}

# 学科回答强调点（写进生成提示词）
_SUBJECT_ANSWER_GUIDE = {
    "physics": "注意公式成立条件与单位换算；实验类结论要写出控制变量思想。",
    "chemistry": "书写化学方程式注意配平与反应条件；回答现象要具体（颜色、沉淀、气体、放热等）。",
    "math": "推理要严谨，注意分类讨论、辅助线做法与漏解陷阱；结论前先给证明/推导。",
}


def _parse_intent(raw: str) -> tuple[str, str]:
    """从 LLM 输出中稳健解析 {"subject": "...", "concept": "..."}。"""
    default_concept = re.sub(r"[？?。！!，,\s]+", "", raw).strip() or "核心知识点"
    try:
        obj = json.loads(raw.strip())
        subject = str(obj.get("subject", "physics")).strip().lower()
        concept = str(obj.get("concept", "")).strip()
    except json.JSONDecodeError:
        # 兜底：输出可能不是标准 JSON，退回按行猜测
        m = re.search(r'"subject"\s*:\s*"([^"]+)"', raw)
        subject = m.group(1).strip().lower() if m else "physics"
        concept = default_concept
    if subject not in _SUBJECT_LABEL:
        subject = "physics"
    return subject, concept or default_concept


def create_circuit_agent(
    vector_db: Chroma,
    graph_db: ScienceGraphStore,
) -> Any:
    """创建并编译初中理科全科问答 Agent 工作流图。

    推理模型固定由 config.py + sida-agent/.env 的 REASONING_* 配置决定。
    """
    log.info("[workflow] 构建全科问答 Agent 工作流")
    llm = get_reasoning_llm()

    def analyze_intent_node(state: CircuitAgentState):
        query = state["query"]
        prompt = (
            f"判断学生提问所属初中学科与核心知识点锚点。\n"
            f"学科仅限三选一：physics(物理)/chemistry(化学)/math(数学)。\n"
            f"只输出一行严格 JSON，不要解释：{{\"subject\": \"physics\", \"concept\": \"知识点名\"}}\n\n"
            f"提问：{query}"
        )
        log.debug("[workflow.analyze_intent] 调用 LLM 判定学科与锚点, query=%r", query)
        subject, concept = _parse_intent(str(llm.invoke(prompt).content))
        log.info("[workflow.analyze_intent] 判定结果: subject=%s, concept=%s",
                 _SUBJECT_LABEL.get(subject, subject), concept)
        return {"target_subject": subject, "target_concept": concept}

    def graph_traversal_node(state: CircuitAgentState):
        subject = state.get("target_subject") or "physics"
        concept = state.get("target_concept") or ""
        log.debug("[workflow.graph_traversal] 图谱聚合检索, subject=%s, concept=%s", subject, concept)
        subgraph = graph_db.get_subgraph(subject, concept)
        if subgraph.get("concept") is None:
            # 锚点未直接命中概念：尝试把 concept 当题型/方法/例题名反查锚定到概念
            log.warning("[workflow.graph_traversal] 概念节点未命中，尝试题型/方法锚点定位")
            for kind_alias in (K_QUESTION_TYPE, K_METHOD, K_EXAMPLE):
                hit = graph_db.get_by_name(subject, kind_alias, concept)
                if hit and hit.get("related_concept"):
                    subgraph = graph_db.get_subgraph(subject, hit["related_concept"])
                    break
        log.info("[workflow.graph_traversal] 命中: 公式 %d, 实验 %d, 题型 %d, 方法 %d, 例题 %d",
                 len(subgraph.get("formulas", [])), len(subgraph.get("experiments", [])),
                 len(subgraph.get("question_types", [])), len(subgraph.get("methods", [])),
                 len(subgraph.get("examples", [])))
        return {"graph_context": subgraph}

    def fetch_chunks_node(state: CircuitAgentState):
        g_ctx = state.get("graph_context", {})
        example_ids = [ex["id"] for ex in g_ctx.get("examples", [])]
        log.debug("[workflow.fetch_chunks] 向量库回表, example_ids=%s", example_ids)
        chunks = []
        for ex_id in example_ids:
            results = vector_db.get(where={"id": ex_id})
            if results and results.get("documents"):
                chunks.append(results["documents"][0])
            else:
                log.warning("[workflow.fetch_chunks] 向量库未命中例题: %s", ex_id)
        log.info("[workflow.fetch_chunks] 回表得到原题切片 %d 条", len(chunks))
        return {"vector_chunks": chunks}

    def generate_response_node(state: CircuitAgentState):
        g_ctx = state.get("graph_context", {})
        subject = g_ctx.get("subject") or state.get("target_subject") or "physics"
        subject_label = _SUBJECT_LABEL.get(subject, subject)
        guide = _SUBJECT_ANSWER_GUIDE.get(subject, "")

        concept = g_ctx.get("concept")
        concept_block = ""
        if concept:
            lines = [f"- 定义：{concept.get('description', '')}"]
            if concept.get("chapter"):
                lines.append(f"- 章节：{concept['chapter']}")
            if concept.get("breakdown"):
                lines.append("- 概念拆解：")
                lines += [f"  {i + 1}. {b}" for i, b in enumerate(concept["breakdown"])]
            if concept.get("common_mistakes"):
                lines.append("- 易错点：")
                lines += [f"  * {e}" for e in concept["common_mistakes"]]
            concept_block = "\n".join(lines)

        prereq_block = "、".join(p["name"] for p in g_ctx.get("prerequisites", [])) or "无"

        formula_block = "\n".join(
            f"- {f['name']}：{f['expression']}"
            + (f"（适用：{f['applicable_scope']}）" if f.get("applicable_scope") else "")
            + ("；推导： " + " -> ".join(f["derivation"]) if f.get("derivation") else "")
            for f in g_ctx.get("formulas", []))

        experiment_block = "\n".join(
            f"- {e['name']}：目的：{e.get('purpose', '')}"
            + (f"\n  器材：{'、'.join(e.get('apparatus', []))}" if e.get("apparatus") else "")
            + (f"\n  现象：{e.get('phenomenon', '')}" if e.get("phenomenon") else "")
            + (f"\n  结论：{e.get('conclusion', '')}" if e.get("conclusion") else "")
            + (f"\n  装置图解：{e.get('diagram', '')}" if e.get("diagram") else "")
            for e in g_ctx.get("experiments", []))

        qtype_block = "\n".join(
            f"- {q['name']}：识别特征：{'、'.join(q.get('identify_features', []))}"
            + (f"；解题模板：{' -> '.join(q.get('template', []))}" if q.get("template") else "")
            + (f"；陷阱：{'、'.join(q.get('traps', []))}" if q.get("traps") else "")
            for q in g_ctx.get("question_types", []))

        method_block = "\n".join(
            f"- {m['name']}：{' -> '.join(m.get('steps', []))}"
            + (f"（适用：{m.get('scope', '')}）" if m.get("scope") else "")
            for m in g_ctx.get("methods", []))

        examples_text = "\n\n".join(state.get("vector_chunks", []))

        final_prompt = f"""你是一位金牌初中{subject_label}名师。请系统回答学生提问："{state['query']}"。

{guide}

【知识点定位（来自教研知识图谱）】：
{concept_block if concept_block else "（图谱中暂无该知识点的拆解信息，请基于学科知识作答）"}
先修基础：{prereq_block}

【公式与推导（图谱）】：
{formula_block or "暂无"}

【实验与图解（图谱）】：
{experiment_block or "暂无"}

【题型模板与陷阱（图谱）】：
{qtype_block or "暂无"}

【方法套路（图谱）】：
{method_block or "暂无"}

【典型例题原文（向量库回表）】：
{examples_text or "暂无关联例题"}

【输出规范】：
1. 概念先行：先讲清"是什么"，用分层拆解的方式讲解，避免堆砌术语。
2. 公式推导：给出公式/定理的来龙去脉与适用条件，不直接扔结论。
3. 实验/图形：涉及实验用文字描述装置与操作、现象、结论；涉及图形要用文字讲清结构。
4. 题型溯源：结合图谱中的题型模板与陷阱，把例题归类到具体题型，示范完整推导。
5. 结尾给出易错点与检查清单。
"""
        response = str(llm.invoke(final_prompt).content)
        log.info("[workflow.generate_response] 解答生成完成, 长度=%d 字符", len(response))
        return {"final_answer": response}

    # 组装状态机工作流
    workflow = StateGraph(CircuitAgentState)
    workflow.add_node("analyze_intent", analyze_intent_node)
    workflow.add_node("graph_traversal", graph_traversal_node)
    workflow.add_node("fetch_chunks", fetch_chunks_node)
    workflow.add_node("generate_response", generate_response_node)

    workflow.add_edge(START, "analyze_intent")
    workflow.add_edge("analyze_intent", "graph_traversal")
    workflow.add_edge("graph_traversal", "fetch_chunks")
    workflow.add_edge("fetch_chunks", "generate_response")
    workflow.add_edge("generate_response", END)

    return workflow.compile()