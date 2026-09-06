#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初中理科全科问答 Agent 工作流（LangGraph）。

链路：提问 -> 学科+知识点锚点判定 -> 图谱聚合检索（概念拆解/公式/实验/题型/例题）
-> 按例题 source.page 回表取讲义页原文 -> 生成分层讲解（概念拆解先行、公式推导、题型溯源）。
"""

from __future__ import annotations

import json
import re
from typing import Any, List

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
    node_key,
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
    """从 LLM 输出中稳健解析 {"subject": "...", "concept": "..."}。

    任何回退到 physics 的路径都记 log.warning（含原因与原始输出片段），
    避免化学/数学问题被静默错路由到物理库后无从排查。
    """
    default_concept = re.sub(r"[？?。！!，,\s]+", "", raw).strip() or "核心知识点"
    fallback_reason = ""
    try:
        obj = json.loads(raw.strip())
        subject = str(obj.get("subject", "")).strip().lower()
        concept = str(obj.get("concept", "")).strip()
        if not subject:
            fallback_reason = "JSON 输出缺少 subject 字段"
            subject = "physics"
    except json.JSONDecodeError:
        # 兜底：输出可能不是标准 JSON，退回按行猜测
        m = re.search(r'"subject"\s*:\s*"([^"]+)"', raw)
        if m:
            subject = m.group(1).strip().lower()
        else:
            fallback_reason = "输出非 JSON 且正则未匹配到 subject"
            subject = "physics"
        concept = default_concept
    if subject not in _SUBJECT_LABEL:
        fallback_reason = f"subject 非法值 {subject!r}"
        subject = "physics"
    if fallback_reason:
        log.warning("[workflow._parse_intent] 学科回退为 physics（%s），原始输出: %s",
                    fallback_reason, raw[:200])
    return subject, concept or default_concept


def create_circuit_agent(
    vector_db: Chroma,
    graph_db: ScienceGraphStore,
) -> Any:
    """创建并编译初中理科全科问答 Agent 工作流图。

    推理模型固定由 config.py + sida-agent/.env 的 REASONING_* 配置决定。
    """
    log.info("[workflow] 构建全科问答 Agent 工作流")
    # 意图判定：只输出一行 JSON，追求短平快 —— 低温、小 max_tokens、关思考。
    intent_llm = get_reasoning_llm(temperature=0.0, max_tokens=128, enable_thinking=False)
    # 最终讲解：需要长输出与推理质量 —— 沿用模型默认思考与较大 token 预算。
    answer_llm = get_reasoning_llm(temperature=0.3)

    def analyze_intent_node(state: CircuitAgentState):
        query = state["query"]
        prompt = (
            f"判断学生提问所属初中学科与核心知识点锚点。\n"
            f"学科仅限三选一：physics(物理)/chemistry(化学)/math(数学)。\n"
            f"concept 必须是知识点名词本身（如\"可变电路\"\"欧姆定律\"\"电功率\"），\n"
            f"严格禁止拼接教学修饰或请求后缀（如\"的分析\"\"的思路\"\"的方法\"\"的讲解\"\"怎么做\"\"如何解\"），\n"
            f"提问是\"讲解XX的分析思路/解题方法\"时，concept 只填 XX 本身。\n"
            f"只输出一行严格 JSON，不要解释：{{\"subject\": \"physics\", \"concept\": \"知识点名\"}}\n\n"
            f"提问：{query}"
        )
        log.debug("[workflow.analyze_intent] 调用 LLM 判定学科与锚点, query=%r", query)
        subject, concept = _parse_intent(str(intent_llm.invoke(prompt).content))
        log.info("[workflow.analyze_intent] 判定结果: subject=%s, concept=%s",
                 _SUBJECT_LABEL.get(subject, subject), concept)
        return {"target_subject": subject, "target_concept": concept}

    def graph_traversal_node(state: CircuitAgentState):
        """图谱聚合检索节点：以意图节点给出的 (学科, 知识点锚点) 为入口，
        从知识图谱中聚合出概念拆解、公式、实验、题型、方法、例题等教研上下文。

        检索按三级兜底逐级降级：
        1. 锚点直接命中 Concept 节点 -> get_subgraph 一/二跳聚合；
        2. 锚点其实不是概念名（而是题型/方法/例题名）-> 反查其所属概念再聚合；
        3. 命中的是"空壳"概念（先修引用自动生成的占位节点）-> 重定位/章节兜底（见下方注释）。
        检索结果整体写入 state["graph_context"]，供后续回表与生成节点消费。
        """
        # 上游 analyze_intent 的判定结果；缺省时保守回退到物理学科（最常见库）
        subject = state.get("target_subject") or "physics"
        concept = state.get("target_concept") or ""
        log.debug("[workflow.graph_traversal] 图谱聚合检索, subject=%s, concept=%s", subject, concept)
        # 第一级：按概念名直接做 1~2 跳聚合检索（get_subgraph 内部还有一次模糊解析，
        # 返回的 dict 里 concept 为 None 即表示图谱里根本没有这个概念节点）
        subgraph = graph_db.get_subgraph(subject, concept)
        if subgraph.get("concept") is None:
            # 第二级兜底——锚点反查：意图 LLM 给出的 concept 可能并不是概念名，而是
            # 题型名/方法名/例题名（如问"动态电路分析怎么做"，锚点其实是题型）。
            # 此时按 题型 -> 方法 -> 例题 的优先级依次尝试把 concept 当作该类实体名
            # 反查图谱（get_by_name 会顺带返回其相邻的 Concept 节点作为锚定概念），
            # 一旦找到挂了 related_concept 的实体，就改用该概念重新做子图聚合并停止。
            log.warning("[workflow.graph_traversal] 概念节点未命中，尝试题型/方法锚点定位")
            for kind_alias in (K_QUESTION_TYPE, K_METHOD, K_EXAMPLE):
                hit = graph_db.get_by_name(subject, kind_alias, concept)
                if hit and hit.get("related_concept"):
                    subgraph = graph_db.get_subgraph(subject, hit["related_concept"])
                    break
        # 空壳概念重定位：锚点命中但自身内容贫瘠（无 description/breakdown）且未聚合到任何
        # 题型/例题，说明该节点多半是先修引用自动生成的"空壳"，真实内容（题型/例题）挂在
        # 1 跳先修/后续概念上；而子图检索的第二跳只沿题型/方法外扩、不会跨概念邻居，所以
        # 这类锚点必然拿不到例题（例：提问"总功率及电功率的计算"命中空壳"电功率"）。
        # 阶段一：按查询词在邻居概念里挑选内容枢纽重定向（查询词无匹配且邻居唯一时也重定向）；
        # 阶段二：邻居也无字面命中（章节式提问，如"讲解简单电路的电功率"），改用章节标题
        # 匹配做整章聚合兜底，把真实例题/题型（挂在章内各内容枢纽概念上）一并捞回。
        # 空壳判定：concept 节点存在（cd 非空）但四个内容维度全空——
        # 无定义(description)、无拆解(breakdown)、聚合不到题型、聚合不到例题，
        # 同时满足才认定为空壳，避免误伤"内容少但有实质信息"的正常概念。
        cd = subgraph.get("concept") or {}
        if (cd and not cd.get("description") and not cd.get("breakdown")
                and not subgraph.get("question_types") and not subgraph.get("examples")):
            # 取学生原始提问（非 LLM 提炼的锚点），用于与邻居概念名做字面匹配
            query = state.get("query") or ""
            # 收集候选重定向目标：后续概念 + 先修概念（真实内容通常挂在这两类 1 跳邻居上），
            # 按出现顺序去重，保证同名概念只保留一次
            cands: List[str] = []
            for p in subgraph.get("follow_ups", []) + subgraph.get("prerequisites", []):
                n = p.get("name")
                if n and n not in cands:
                    cands.append(n)
            # 阶段一筛选：邻居概念名直接出现在提问原文里的，视为学生真正想问的内容枢纽
            matched = [n for n in cands if n in query]
            # 选择策略：有字面命中取第一个；无命中但邻居唯一（空壳只挂一个邻居，
            # 大概率它就是真实内容所在）也敢重定向；多个邻居且无命中则不敢猜，置 None
            pick = matched[0] if matched else (cands[0] if len(cands) == 1 else None)
            if pick:
                # 阶段一命中：对选中的邻居概念重新做子图聚合，替换掉空壳结果
                log.warning("[workflow.graph_traversal] 锚点概念 %r 为空壳（无题型/例题挂载），"
                            "重定位到关联概念 %r", concept, pick)
                subgraph = graph_db.get_subgraph(subject, pick)
            else:
                # 阶段二兜底：章节式提问（如"讲解简单电路的电功率"）在邻居名上无字面命中，
                # 改为拿整句提问去匹配图谱中的章节标题，解析出所属章节
                chapter = graph_db.resolve_chapter(subject, query)
                if chapter:
                    # 章节解析成功：聚合整章子图（concepts 列表 + 章内全部公式/题型/例题），
                    # 真实例题虽挂在章内各内容枢纽概念上，也能被整体捞回
                    log.warning("[workflow.graph_traversal] 锚点概念 %r 为空壳且邻居无字面命中，"
                                "章节兜底聚合整章 %r", concept, chapter)
                    subgraph = graph_db.get_chapter_subgraph(subject, chapter)
        log.info("[workflow.graph_traversal] 命中: 公式 %d, 实验 %d, 题型 %d, 方法 %d, 例题 %d",
                 len(subgraph.get("formulas", [])), len(subgraph.get("experiments", [])),
                 len(subgraph.get("question_types", [])), len(subgraph.get("methods", [])),
                 len(subgraph.get("examples", [])))
        return {"graph_context": subgraph}

    def fetch_chunks_node(state: CircuitAgentState):
        g_ctx = state.get("graph_context", {})
        subject = state.get("target_subject") or "physics"
        examples = g_ctx.get("examples", [])
        log.debug("[workflow.fetch_chunks] 向量库回表, 例题数=%d", len(examples))
        # 例题原文按 (pdf_id, page) 回表取讲义页切片；同页多题只取一次。
        # 讲义页切片键带 pdf_id 前缀（subject:Page:{pdf_id}:{页码}），防止跨
        # PDF 的同页码互相覆盖；例题节点无 pdf_id（旧库/未传）时退化为裸页码
        # 键，兼容旧数据。
        page_keys: List[str] = []
        for ex in examples:
            page = (ex.get("source") or {}).get("page")
            if page is None:
                continue
            pdf_id = str(ex.get("pdf_id") or "")
            page_token = f"{pdf_id}:{page}" if pdf_id else str(page)
            pk = node_key(subject, "Page", page_token)
            if pk not in page_keys:
                page_keys.append(pk)
        chunks: List[str] = []
        for pk in page_keys:
            results = vector_db.get(where={"id": pk})
            if results and results.get("documents"):
                chunks.append(results["documents"][0])
            else:
                log.warning("[workflow.fetch_chunks] 向量库未命中讲义页: %s", pk)
        # 缺 page 的例题无法回表原文：例题自身的向量文档只是「编号+标题+题型」壳，
        # 从不含题干原文（原文只存在于按 page 索引的讲义页切片）。此处不再拿空壳
        # 冒充原文塞进 prompt，改为记 warning，让"典型例题原文"缺失显式可见。
        for ex in examples:
            if (ex.get("source") or {}).get("page") is None:
                log.warning("[workflow.fetch_chunks] 例题缺 source.page，无法回表原文: %s",
                            ex.get("id", "?"))
        log.info("[workflow.fetch_chunks] 回表得到原题切片 %d 条", len(chunks))
        return {"vector_chunks": chunks}

    def generate_response_node(state: CircuitAgentState):
        g_ctx = state.get("graph_context", {})
        subject = g_ctx.get("subject") or state.get("target_subject") or "physics"
        subject_label = _SUBJECT_LABEL.get(subject, subject)
        guide = _SUBJECT_ANSWER_GUIDE.get(subject, "")

        concept = g_ctx.get("concept")
        concepts = g_ctx.get("concepts") or []
        concept_block = ""
        if concepts:
            # 章节聚合检索：一个问题覆盖整章多个概念，逐个渲染供模型组织讲解
            for i, cd_ in enumerate(concepts, 1):
                lines = [f"【概念 {i}：{cd_.get('name', '')}】"]
                if cd_.get("chapter"):
                    lines.append(f"- 章节：{cd_['chapter']}")
                lines.append(f"- 定义：{cd_.get('description', '')}")
                if cd_.get("breakdown"):
                    lines.append("- 概念拆解：")
                    lines += [f"  {j + 1}. {b}" for j, b in enumerate(cd_["breakdown"])]
                if cd_.get("common_mistakes"):
                    lines.append("- 易错点：")
                    lines += [f"  * {e}" for e in cd_["common_mistakes"]]
                concept_block = (concept_block + "\n" if concept_block else "") + "\n".join(lines)
        elif concept:
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
        followup_block = "、".join(p["name"] for p in g_ctx.get("follow_ups", [])) or "无"
        related_block = "、".join(
            f"{p['name']}（{p.get('relation', '')}）" if p.get("relation") else p["name"]
            for p in g_ctx.get("related_concepts", [])) or "无"

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

        chunks = state.get("vector_chunks", [])
        examples_text = "\n\n".join(chunks)
        # 讲义页切片自带「--- 第 N 页 ---」头，据此列出命中页码供模型标注来源；
        # 图谱实体（公式/实验/题型/方法）抽取时不记页码，只能标注到「知识图谱」粒度。
        pages_hit = sorted({int(n) for c in chunks for n in re.findall(r"--- 第 (\d+) 页 ---", c)})
        retrieval_status = (
            f"知识图谱：{'命中' if (concept or concepts) else '未命中'}；"
            f"讲义原文：{'第 ' + '、'.join(map(str, pages_hit)) + ' 页' if pages_hit else '无'}"
        )

        final_prompt = f"""你是一位金牌初中{subject_label}名师。请系统回答学生提问："{state['query']}"。

【最高优先级约束·严格依据资料】：
本次回答的每一个知识点、公式、例题、结论，都必须能在下方【知识点定位】【公式与推导】
【实验与图解】【题型模板与陷阱】【方法套路】【典型例题原文】六个区块中找到出处。
你的任务是把这些检索资料整理、归纳、组织成条理清晰的讲解，而不是自由讲题：
- 严禁引入资料之外的任何知识点、公式、题型、拓展或"常见补充"；
- 资料没有的内容，直接写明"当前教材资料未收录该部分内容"，不要猜测或补全；
- 若六个区块全部为空或"暂无"，只输出一句说明（见输出规范第 6 条），不要展开作答。
- 在满足以上前提下尽量精炼：能一句话说清的不展开成三段，避免重复表述。

{guide}

【知识点定位（来自教研知识图谱）】：
{concept_block if concept_block else "（图谱中暂无该知识点的拆解信息）"}
先修基础（学本概念前应先掌握）：{prereq_block}
后续概念（掌握本概念后可进阶）：{followup_block}
关联概念（相关但非先修，仅供参照）：{related_block}

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

【本次检索命中情况】：{retrieval_status}

【输出规范】：
1. 概念先行：先讲清"是什么"，用分层拆解的方式讲解，避免堆砌术语。
2. 公式推导：给出公式/定理的来龙去脉与适用条件，不直接扔结论。
   公式排版：整条独立公式用块级公式，前后各加一行 "$$"，公式内容放中间
   （即 $$ ... $$ 各自单独成行），行内符号用 $...$；禁止用 \\[ \\] 或 \\( \\)
   包裹公式——这类定界符在多数 Markdown 阅读器会被渲染成字面的方括号。
3. 实验/图形：涉及实验用文字描述装置与操作、现象、结论；涉及图形要用文字讲清结构。
4. 题型溯源：结合图谱中的题型模板与陷阱，把例题归类到具体题型，示范完整推导。
5. 结尾给出易错点与检查清单。
6. 来源标注（重要，帮助学生判断内容是否贴合教材）：
   - 内容取自【典型例题原文】的，句末标注「（见教材第 X 页）」，页码取该段原文所在页；
   - 内容取自知识图谱各区块（概念拆解/公式/实验/题型/方法）的，标注「（教材知识点，图谱收录）」；
   - 不得出现无出处的内容；确实需要提示资料局限时，另起一段以「【资料说明·教材未涉及】」
     开头，只说明"该部分内容当前教材未收录"，不要补充具体知识；
   - 当检索命中情况显示图谱"未命中"且讲义原文为"无"时，不要作答，只输出一句：
     「当前教材资料未收录与该提问相关的知识点，无法基于教材作答。」
   - 讲义原文为"无"但图谱命中时，图谱内容仍按「（教材知识点，图谱收录）」标注，
     只是不得引用具体页码；宁可说明不确定，也不要编造页码。
"""
        response = str(answer_llm.invoke(final_prompt).content)
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