#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初中理科全科问答 Agent 工作流（LangGraph）。

链路：提问 -> 学科+知识点锚点判定 -> 图谱聚合检索（概念拆解/公式/实验/题型/例题）
-> 按例题 source.page 回表取讲义页原文 -> 生成分层讲解（概念拆解先行、公式推导、题型溯源）。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, List, Optional

from langchain_chroma import Chroma
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
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

# ---- chat 会话上下文管理预算（字符粗估，非计费口径） -----------------------
# 推理模型为本地 qwen3.8-flash（~64k 窗口）。对话历史超过预算即由
# manage_context 截断旧消息：被丢弃部分增量压缩进 history_summary，
# 保证每次 LLM 输入 = 摘要 + 最近窗口 + 本轮检索资料，均有界。
_CHAT_HISTORY_BUDGET_CHARS = 12000    # 保留在 messages 中的最近历史字符上限
_CHAT_SUMMARY_MAX_TOKENS = 600        # 单次摘要输出 token 上限

# find_problem 语义搜题：最终送给生成节点的讲义页数量；语义兜底时先取
# RERANK_K 页候选再按与 search_text 的二元组重合度重排，防止目标页被
# 「同主题不同题」的页面挤出前列。
_SEARCH_PROBLEM_TOP_K = 3
_SEARCH_PROBLEM_RERANK_K = 10


def _bigram_overlap(query: str, page: str) -> float:
    """query 的相邻字符二元组在 page 中出现的比例（0~1）。

    中文无空格分词，二元组是比单字更强的局部文本信号：目标页含原文片段
    时重合度显著高于同主题的其他页。query 过短（<2 字符）时恒为 0。
    """
    q = re.sub(r"\s+", "", query)
    if len(q) < 2:
        return 0.0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in page)
    return hit / len(grams)


def _parse_intent(raw: str) -> tuple[str, str, str, str]:
    """从 LLM 输出中稳健解析 {"subject", "intent", "concept", "search_text"}。

    intent 取 "concept"（问知识点，默认）或 "find_problem"（按题目内容原文搜题）。
    find_problem 时 concept 可为空、search_text 为提炼出的题目内容特征；
    concept 时 search_text 为空。任何回退到 physics 的路径都记 log.warning
    （含原因与原始输出片段），避免化学/数学问题被静默错路由到物理库后无从排查。
    """
    default_concept = re.sub(r"[？?。！!，,\s]+", "", raw).strip() or "核心知识点"
    fallback_reason = ""
    intent = "concept"
    search_text = ""
    try:
        obj = json.loads(raw.strip())
        subject = str(obj.get("subject", "")).strip().lower()
        concept = str(obj.get("concept", "")).strip()
        intent = str(obj.get("intent", "")).strip().lower()
        search_text = str(obj.get("search_text", "")).strip()
        if not subject:
            fallback_reason = "JSON 输出缺少 subject 字段"
            subject = "physics"
    except json.JSONDecodeError:
        # 兜底：输出可能不是标准 JSON（缺逗号/多引号等），退回按字段正则提取
        m = re.search(r'"subject"\s*:\s*"([^"]+)"', raw)
        if m:
            subject = m.group(1).strip().lower()
        else:
            fallback_reason = "输出非 JSON 且正则未匹配到 subject"
            subject = "physics"
        concept = ""
        im = re.search(r'"intent"\s*:\s*"([^"]+)"', raw)
        if im:
            intent = im.group(1).strip().lower()
        sm = re.search(r'"search_text"\s*:\s*"([^"]*)"', raw)
        if sm:
            search_text = sm.group(1).strip()
        if intent != "find_problem":
            cm = re.search(r'"concept"\s*:\s*"([^"]*)"', raw)
            if cm:
                concept = cm.group(1).strip()
    if intent not in ("concept", "find_problem", "offtopic"):
        intent = "concept"
    if subject not in _SUBJECT_LABEL:
        fallback_reason = f"subject 非法值 {subject!r}"
        subject = "physics"
    if fallback_reason:
        log.warning("[workflow._parse_intent] 学科回退为 physics（%s），原始输出: %s",
                    fallback_reason, raw[:200])
    if intent == "find_problem":
        # 搜题链路不依赖 concept 锚点：LLM 留空时保持为空，
        # 不要把整行 JSON 兜底串当概念名（只会污染日志与保存文件元信息）。
        return subject, concept, intent, search_text
    if intent == "offtopic":
        # 闲聊/与知识库无关：不检索、不需要概念锚点，直接走轻量回复节点
        return subject, "", intent, ""
    return subject, concept or default_concept, intent, search_text


# ---------------- chat 会话上下文辅助（纯函数，无 LLM 调用） ----------------

def _msg_text(msg: AnyMessage) -> str:
    """取消息正文文本（content 为 str 或文本块列表时均返回纯文本）。"""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                       for b in c)
    return str(c)


def _dialogue_text(msgs: List[AnyMessage]) -> str:
    """把一段消息渲染为「学生：…/老师：…」对话文本（摘要/上下文注入用）。"""
    lines: List[str] = []
    for m in msgs:
        if isinstance(m, HumanMessage):
            lines.append(f"学生：{_msg_text(m)}")
        elif isinstance(m, AIMessage):
            lines.append(f"老师：{_msg_text(m)}")
    return "\n".join(lines)


def _recent_context(state: CircuitAgentState, *, max_chars: int = 3000) -> str:
    """渲染最近对话上下文（不含本轮提问，即去掉 messages 最后一条）。

    供意图判定与生成节点拼进 prompt 以支持指代消解；超出 max_chars 截断
    （中文字符粗估兜底，正常窗口远小于该值）。
    """
    msgs = list(state.get("messages") or [])
    if not msgs:
        return ""
    text = _dialogue_text(msgs[:-1]).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = "…（更早内容略）\n" + text[-max_chars:]
    return text


def _summary_prefix(state: CircuitAgentState) -> str:
    """历史摘要区块文本（无摘要时返回空串）。"""
    summary = (state.get("history_summary") or "").strip()
    return f"（此前对话摘要：{summary}）\n" if summary else ""


def _context_block(state: CircuitAgentState) -> str:
    """拼装完整「对话背景」区块：此前摘要 + 最近若干轮（不含本轮提问）。"""
    summary = _summary_prefix(state)
    recent = _recent_context(state)
    if not summary and not recent:
        return ""
    return f"【对话背景（此前交流，供理解指代）】：\n{summary}{recent}\n"


def _stream_answer(llm, prompt: str) -> str:
    """以流式调用生成完整回答并返回拼接文本。

    节点内用 llm.stream 逐块产出：LangGraph 会把每个增量块作为
    stream_mode="messages" 的 token 推送（前端/CLI 据此逐字打印）；
    此处只负责把全部块拼回全文，写入 final_answer 与 AIMessage。
    """
    out: List[str] = []
    for chunk in llm.stream(prompt):
        content = getattr(chunk, "content", None)
        if isinstance(content, str):
            out.append(content)
    return "".join(out)


def create_circuit_agent(
    vector_db: Chroma,
    graph_db: ScienceGraphStore,
    checkpointer: Any = None,
) -> Any:
    """创建并编译初中理科全科问答 Agent 工作流图。

    推理模型固定由 config.py + sida-agent/.env 的 REASONING_* 配置决定。
    checkpointer：传 SqliteSaver 等 checkpointer 后支持 chat 多轮会话
    （消息按 thread_id 持久化、可续聊）。单轮 ask/all 模式可不传。
    """
    log.info("[workflow] 构建全科问答 Agent 工作流")
    # 意图判定：只输出一行 JSON，追求短平快 —— 低温、小 max_tokens、关思考。
    intent_llm = get_reasoning_llm(temperature=0.0, max_tokens=128, enable_thinking=False)
    # 最终讲解：需要长输出与推理质量 —— 沿用模型默认思考与较大 token 预算。
    answer_llm = get_reasoning_llm(temperature=0.3)
    # 上下文摘要：短输出压缩（chat 模式上下文管理用）。
    summary_llm = get_reasoning_llm(temperature=0.0, max_tokens=_CHAT_SUMMARY_MAX_TOKENS,
                                    enable_thinking=False)

    def manage_context_node(state: CircuitAgentState):
        """chat 上下文管理：messages 超预算时截断旧消息并增量压缩摘要。

        messages 是累积 channel：本节点只返回 RemoveMessage（删除被丢弃
        的旧消息）与 history_summary（覆盖式更新），窗口内保留消息不动。
        被丢弃的对话压进 history_summary，供后续节点以「对话背景」形式
        注入 prompt，保证每次 LLM 输入 = 摘要 + 最近窗口 + 本轮资料，均有界。
        单轮模式（无 messages）直接跳过。
        """
        msgs = list(state.get("messages") or [])
        if not msgs:
            return {}
        total = sum(len(_msg_text(m)) for m in msgs)
        if total <= _CHAT_HISTORY_BUDGET_CHARS:
            return {}
        # 从末尾往回尽量保留消息直到预算；保底保留最近 1 条（本轮提问）。
        kept: List[AnyMessage] = []
        acc = 0
        for m in reversed(msgs):
            if kept and acc + len(_msg_text(m)) > _CHAT_HISTORY_BUDGET_CHARS:
                break
            kept.append(m)
            acc += len(_msg_text(m))
        kept.reverse()
        dropped = msgs[: len(msgs) - len(kept)]
        if not dropped:
            return {}
        old_summary = (state.get("history_summary") or "").strip()
        dropped_text = _dialogue_text(dropped)
        log.info("[workflow.manage_context] 历史 %d 字符超预算，截断 %d 条旧消息"
                 "（保留最近 %d 条/%d 字符）并增量压缩摘要",
                 total, len(dropped), len(kept), acc)
        prompt = (
            "把下面的「旧对话」压缩成一段简洁的中文会话摘要。\n"
            "覆盖要点：学生问过的知识点/题型、已讲解的结论与例题、学生薄弱点、"
            "有待继续追问的话题。"
            + (f"\n【旧摘要（保留其要点，只增补/修正新信息）】\n{old_summary}"
               if old_summary else "")
            + f"\n【旧对话】\n{dropped_text}\n"
            "直接输出更新后的摘要正文，不要任何前缀解释。"
        )
        try:
            new_summary = str(summary_llm.invoke(prompt).content).strip()
        except Exception:  # 摘要失败不阻断主链路：保留旧摘要继续走
            log.warning("[workflow.manage_context] 摘要生成失败，保留原摘要",
                        exc_info=True)
            return {"messages": [RemoveMessage(id=mid) for m in dropped
                                 if (mid := getattr(m, "id", None))]}
        removals = [RemoveMessage(id=mid) for m in dropped
                    if (mid := getattr(m, "id", None))]
        log.info("[workflow.manage_context] 摘要完成 %d 字", len(new_summary))
        return {"messages": removals, "history_summary": new_summary}

    def analyze_intent_node(state: CircuitAgentState):
        query = state["query"]
        background = _context_block(state)
        prompt = (
            f"判断学生提问的意图、所属初中学科与检索锚点。\n"
            f"学科仅限三选一：physics(物理)/chemistry(化学)/math(数学)。\n"
            f"intent 三选一：\n"
            f"- concept：学生在问某个知识点/公式/题型/方法本身（想要讲解、分析思路、解题方法）。\n"
            f"- find_problem：学生在「找一道题」——用题目的原文片段/内容特征来描述，希望定位到"
            f"包含该内容的那道题（如\"查询一道题，内容包含'甲、乙两瓶等量煤油'\"\"有没有讲XX的那道题\"）。\n"
            f"- offtopic：寒暄、闲聊、夸奖或与理科知识学习无关的提问（如\"你好\"\"谢谢\"\"你是谁\"）。\n"
            f"intent=concept 时：concept 必须是知识点名词本身（如\"可变电路\"\"欧姆定律\"\"电功率\"），\n"
            f"严格禁止拼接教学修饰或请求后缀（如\"的分析\"\"的思路\"\"的方法\"\"的讲解\"\"怎么做\"\"如何解\"），\n"
            f"提问是\"讲解XX的分析思路/解题方法\"时，concept 只填 XX 本身；search_text 留空。\n"
            f"intent=find_problem 时：search_text 填学生描述的题目内容特征（尽量保留原文关键名词、数字、装置等，"
            f"去掉\"我想查询一道题\"\"内容包含\"这类请求前缀）；concept 若能判断题目所属知识点则填，否则留空。\n"
            f"intent=offtopic 时：concept 与 search_text 均留空。\n"
            f"提问可能存在省略与指代（如\"那第二题呢\"\"上面说的那个概念\"），"
            f"可结合下方对话背景理解，必要时补全 concept，但不要臆造背景中没有的知识点。\n"
            f"只输出一行严格 JSON，不要解释："
            f"{{\"subject\": \"physics\", \"intent\": \"concept\", \"concept\": \"知识点名\", \"search_text\": \"\"}}\n"
            + (f"\n【对话背景】\n{background}" if background else "")
            + f"\n提问：{query}"
        )
        log.debug("[workflow.analyze_intent] 调用 LLM 判定意图与锚点, query=%r", query)
        subject, concept, intent, search_text = _parse_intent(
            str(intent_llm.invoke(prompt).content))
        log.info("[workflow.analyze_intent] 判定结果: subject=%s, intent=%s, concept=%s, "
                 "search_text=%r",
                 _SUBJECT_LABEL.get(subject, subject), intent, concept, search_text)
        return {"target_subject": subject, "target_concept": concept,
                "intent": intent, "search_text": search_text}

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
            # 第二级兜底——非概念实体锚点：意图 LLM 给出的 concept 可能不是概念名，
            # 而是公式名/题型名/方法名（如问"三角函数的倍角公式"，锚点其实是公式）。
            # 此时把锚点当作各类实体名依次解析，命中后以该实体为中心聚合其内容与
            # 关联概念；这一级必须在旧的 get_by_name 反查之前——get_by_name 要求
            # 实体已挂到概念上，而孤立公式（历史建库遗留的孤儿节点）恰恰挂不上，
            # 会直接漏掉。
            log.warning("[workflow.graph_traversal] 概念节点未命中，尝试实体锚点定位")
            entity_sub = graph_db.get_entity_subgraph(subject, concept)
            if entity_sub is not None:
                subgraph = entity_sub
            else:
                # 第三级兜底——题型/方法/例题反查：实体名未直接命中时，按
                # 题型 -> 方法 -> 例题 的优先级反查图谱（get_by_name 会顺带返回其
                # 相邻 Concept 作为锚定概念），找到就改用该概念重新聚合。
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

    def search_problems_node(state: CircuitAgentState):
        """按题目内容原文检索讲义页（find_problem 意图专用），两级策略：

        1. 逐字命中：Chroma where_document $contains 对整页原文做子串匹配
           （实测中文可用；「短引文 vs 长页」的精确找题场景远比向量距离可靠，
           向量距离区分度可低至 0.006 而把目标页排到第 8）；
        2. 语义兜底：描述与原文有出入（改写/错字）时走向量检索取 top-N，
           再按 search_text 字符二元组在页内的重合度重排——纯语义会把目标页
           淹没在"同主题不同题"的页面里，二元组重合能把含原文片段的页捞回前排。
        返回整页切片（含该题及同页其他题），由生成节点定位具体题目。
        """
        subject = state.get("target_subject") or "physics"
        text = (state.get("search_text") or "").strip() or (state.get("query") or "").strip()
        log.debug("[workflow.search_problems] 讲义页搜题, subject=%s, text=%r",
                  subject, text[:80])
        problem_chunks: List[str] = []
        if not text or vector_db is None:
            log.info("[workflow.search_problems] 无有效检索文本，跳过")
            return {"problem_chunks": []}
        # filter 顶层多字段 AND 必须显式 $and，否则 Chroma 抛
        # "Expected where to have exactly one operator"（同 _gather_known_context）。
        where: dict = {"$and": [{"subject": subject}, {"type": "Page"}]}
        try:
            got = vector_db.get(where=where, where_document={"$contains": text})
            docs = (got or {}).get("documents") or []
            if docs:
                problem_chunks = [d for d in docs if d]
                log.info("[workflow.search_problems] 原文逐字命中讲义页 %d 页",
                         len(problem_chunks))
        except Exception:  # noqa: BLE001
            log.warning("[workflow.search_problems] $contains 检索失败，转语义兜底",
                        exc_info=True)
        if not problem_chunks:
            try:
                hits = vector_db.similarity_search_with_score(
                    text, k=_SEARCH_PROBLEM_RERANK_K, filter=where)
            except Exception:  # noqa: BLE001
                log.warning("[workflow.search_problems] 语义搜题失败", exc_info=True)
                hits = []
            ranked = sorted(
                ((_bigram_overlap(text, doc.page_content), dist, doc)
                 for doc, dist in hits),
                key=lambda x: (-x[0], x[1]))
            problem_chunks = [doc.page_content for _, _, doc in
                              ranked[:_SEARCH_PROBLEM_TOP_K] if doc.page_content]
            log.info("[workflow.search_problems] 语义兜底重排后取 %d 页（候选 %d 页）",
                     len(problem_chunks), len(hits))
        log.info("[workflow.search_problems] 命中讲义页 %d 页", len(problem_chunks))
        return {"problem_chunks": problem_chunks}

    def generate_problem_response_node(state: CircuitAgentState):
        """搜题专用生成：从命中的整页讲义原文里定位目标题，原题呈现并简要讲解。"""
        subject = state.get("target_subject") or "physics"
        subject_label = _SUBJECT_LABEL.get(subject, subject)
        guide = _SUBJECT_ANSWER_GUIDE.get(subject, "")
        query = state["query"]
        background = _context_block(state)
        search_text = (state.get("search_text") or "").strip() or query
        chunks = state.get("problem_chunks", [])
        pages_text = "\n\n".join(chunks)
        pages_hit = sorted({int(n) for c in chunks
                            for n in re.findall(r"--- 第 (\d+) 页 ---", c)})
        pages_label = ("、".join(map(str, pages_hit)) + " 页") if pages_hit else "无"

        # 对话背景块（f-string 表达式内不允许反斜杠，故先拼好再插值）
        background_block = ("【对话背景（此前交流，供理解指代，"
                            + '如"第二题"指代上一条内容）】：\n'
                            + background + "\n") if background else ""
        final_prompt = f"""你是一位金牌初中{subject_label}教研老师。学生不是在问知识点，而是在「找一道题」：
"{query}"
{background_block}
学生的题目内容描述（用于在下方讲义页里定位）："{search_text}"

【检索到的讲义页原文（整页，含该题及同页其他题）】：
{pages_text or "（未在教材讲义中检索到与该描述匹配的页面）"}

【命中页码】：{pages_label}

{guide}

【任务与输出规范】：
1. 定位题目：在上述讲义页原文里找出与学生描述匹配的那道题（按题干关键名词、数字、装置比对）。
   - 若明确命中，先原样完整呈现该题（题号、题干、所有选项/条件，公式用 $...$ 或独立 $$ 块级公式，
     禁止用 \\[ \\] 或 \\( \\) 包裹）；
   - 若一页里有多个候选题或匹配不确定，把它们分别列出并说明各自与描述的吻合点，让学生确认；
   - 若讲义页与描述都对不上，如实说明"教材中未检索到与该描述匹配的题目"，不要编造题目。
2. 出处标注：每道题标注其所在页码，格式「（见教材第 X 页）」，页码取该题所在切片头部
   「--- 第 N 页 ---」中的 N；严禁编造未出现的页码。
3. 简要讲解：定位到题目后，给出该题的答案与简明解析（依据讲义页里出现的内容与{subject_label}
   学科常识），解析要精炼，不展开与本题无关的知识。
4. 只依据上方讲义页原文作答，不得虚构教材里没有的题目内容。
"""
        response = _stream_answer(answer_llm, final_prompt)
        log.info("[workflow.generate_problem_response] 搜题解答生成完成, 长度=%d 字符",
                 len(response))
        # final_answer 供单轮 ask/all 复用（main 保存 md）；AIMessage 供
        # chat 模式把回答写回 messages 会话历史（由 checkpointer 持久化）。
        return {"final_answer": response, "messages": [AIMessage(content=response)]}

    def generate_response_node(state: CircuitAgentState):
        g_ctx = state.get("graph_context", {})
        subject = g_ctx.get("subject") or state.get("target_subject") or "physics"
        subject_label = _SUBJECT_LABEL.get(subject, subject)
        guide = _SUBJECT_ANSWER_GUIDE.get(subject, "")
        background = _context_block(state)

        # 教材名注册表：pdf_id -> 教材显示名（建库时 --book / 文件名登记，见 ingestion）。
        # 图谱实体（概念/公式/实验/题型/方法）节点携带 sources=[pdf_id,...]，据此把
        # 「图谱收录」标注升级为「收录于《具体教材名》」；未登记时回退旧标注。
        pdf_names = graph_db.pdf_names()

        def _books_of(sources: Any) -> str:
            """把实体的 sources（pdf_id 列表）解析为《教材名》顿号串；无则空串。"""
            names: List[str] = []
            for pid in (sources or []):
                nm = pdf_names.get(str(pid))
                if nm and nm not in names:
                    names.append(nm)
            return "、".join(f"《{n}》" for n in names)

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
                bk = _books_of(cd_.get("sources"))
                if bk:
                    lines.append(f"- 收录教材：{bk}")
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
            bk = _books_of(concept.get("sources"))
            if bk:
                lines.append(f"- 收录教材：{bk}")
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
            + (f"〔收录：{_books_of(f.get('sources'))}〕" if _books_of(f.get("sources")) else "")
            for f in g_ctx.get("formulas", []))

        experiment_block = "\n".join(
            f"- {e['name']}：目的：{e.get('purpose', '')}"
            + (f"\n  器材：{'、'.join(e.get('apparatus', []))}" if e.get("apparatus") else "")
            + (f"\n  现象：{e.get('phenomenon', '')}" if e.get("phenomenon") else "")
            + (f"\n  结论：{e.get('conclusion', '')}" if e.get("conclusion") else "")
            + (f"\n  装置图解：{e.get('diagram', '')}" if e.get("diagram") else "")
            + (f"\n  收录教材：{_books_of(e.get('sources'))}" if _books_of(e.get("sources")) else "")
            for e in g_ctx.get("experiments", []))

        qtype_block = "\n".join(
            f"- {q['name']}：识别特征：{'、'.join(q.get('identify_features', []))}"
            + (f"；解题模板：{' -> '.join(q.get('template', []))}" if q.get("template") else "")
            + (f"；陷阱：{'、'.join(q.get('traps', []))}" if q.get("traps") else "")
            + (f"〔收录：{_books_of(q.get('sources'))}〕" if _books_of(q.get("sources")) else "")
            for q in g_ctx.get("question_types", []))

        method_block = "\n".join(
            f"- {m['name']}：{' -> '.join(m.get('steps', []))}"
            + (f"（适用：{m.get('scope', '')}）" if m.get("scope") else "")
            + (f"〔收录：{_books_of(m.get('sources'))}〕" if _books_of(m.get("sources")) else "")
            for m in g_ctx.get("methods", []))

        chunks = state.get("vector_chunks", [])
        # 页码 -> 该页所属教材名（来自命中例题的 pdf_id），用于把「见教材第X页」
        # 升级为「见《教材名》第X页」；一页只对应一本已登记教材时才敢标书名。
        page_books: dict[int, List[str]] = {}
        for ex in g_ctx.get("examples", []):
            pg = (ex.get("source") or {}).get("page")
            nm = pdf_names.get(str(ex.get("pdf_id") or ""))
            if pg is not None and nm:
                try:
                    pg = int(pg)
                except (TypeError, ValueError):
                    continue
                page_books.setdefault(pg, [])
                if nm not in page_books[pg]:
                    page_books[pg].append(nm)

        def _tag_chunk(c: str) -> str:
            """把讲义页切片头「--- 第 N 页 ---」补成含书名的出处标记。"""
            m = re.search(r"--- 第 (\d+) 页 ---", c)
            if m:
                bs = page_books.get(int(m.group(1)))
                if bs and len(bs) == 1:
                    return c.replace(
                        m.group(0), f"--- 第 {m.group(1)} 页（《{bs[0]}》） ---", 1)
            return c

        examples_text = "\n\n".join(_tag_chunk(c) for c in chunks)
        # 讲义页切片自带「--- 第 N 页 ---」头，据此列出命中页码供模型标注来源；
        # 图谱实体（公式/实验/题型/方法）抽取时不记页码，只能标注到「知识图谱」粒度。
        pages_hit = sorted({int(n) for c in chunks for n in re.findall(r"--- 第 (\d+) 页 ---", c)})
        if pages_hit:
            uniq_books = {page_books[p][0] for p in pages_hit
                          if len(page_books.get(p, [])) == 1}
            if len(uniq_books) == 1:
                orig_label = (f"《{uniq_books.pop()}》第 "
                              + "、".join(map(str, pages_hit)) + " 页")
            else:
                orig_label = "第 " + "、".join(map(str, pages_hit)) + " 页"
        else:
            orig_label = "无"
        # "图谱命中"判据必须覆盖全部实体桶：非概念锚点（公式/题型/方法/例题）
        # 命中时 concept/concepts 恒为空，若只看这两个会把命中误报成"未命中"，
        # 模型随即按输出规范第 6 条拒答（问"三角函数的倍角公式"即踩此坑）。
        graph_hit = bool(concept or concepts or g_ctx.get("formulas")
                         or g_ctx.get("experiments") or g_ctx.get("question_types")
                         or g_ctx.get("methods") or g_ctx.get("examples"))
        retrieval_status = (
            f"知识图谱：{'命中' if graph_hit else '未命中'}；"
            f"讲义原文：{orig_label}"
        )

        background_block = ("【对话背景（此前交流，供理解指代与衔接）】：\n"
                            + background + "\n") if background else ""
        final_prompt = f"""你是一位金牌初中{subject_label}名师。请系统回答学生提问："{state['query']}"。
{background_block}
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
6. 来源标注（重要，帮助学生判断内容出自哪本教材）：
   - 内容取自【典型例题原文】的，句末标注「（见《教材名》第 X 页）」，书名与页码取该段
     原文头部「--- 第 N 页（《教材名》） ---」标记中的信息；标记中无书名时才写「（见教材第 X 页）」；
   - 内容取自知识图谱各区块（概念拆解/公式/实验/题型/方法）的，若该条目带「收录教材：《…》」
     或「〔收录：《…》〕」，标注「（《教材名》知识点，图谱收录）」（多本共收时书名用顿号并列）；
     条目未带收录教材时，标「（教材知识点，图谱收录）」；
   - 不得出现无出处的内容；不得编造区块中不存在的书名或页码；确实需要提示资料局限时，
     另起一段以「【资料说明·教材未涉及】」
     开头，只说明"该部分内容当前教材未收录"，不要补充具体知识；
   - 当检索命中情况显示图谱"未命中"且讲义原文为"无"时，不要作答，只输出一句：
     「当前教材资料未收录与该提问相关的知识点，无法基于教材作答。」
   - 图谱"命中"即表示上述六个区块中至少有一个含实际内容：此时必须基于这些区块
     作答，不得因为【知识点定位】为空（提问锚点是公式/题型名而非概念名时属正常）
     就判定为未收录；讲义原文为"无"时，图谱内容仍按上述"图谱收录"规则标注（有
     收录教材信息则带上《教材名》），只是不得引用具体页码；宁可说明不确定，
     也不要编造页码。
"""
        response = _stream_answer(answer_llm, final_prompt)
        log.info("[workflow.generate_response] 解答生成完成, 长度=%d 字符", len(response))
        # final_answer 供单轮 ask/all 复用（main 保存 md）；AIMessage 供
        # chat 模式把回答写回 messages 会话历史（由 checkpointer 持久化）。
        return {"final_answer": response, "messages": [AIMessage(content=response)]}

    def respond_chitchat_node(state: CircuitAgentState):
        """闲聊/与知识库无关话题的轻量直答：不触发图谱/向量检索。

        chat 模式下学生寒暄（你好/谢谢/你是谁）不至于空跑整条检索链路，
        仅给一句简短自然回应并把话题引导回学习；该轮也写入会话历史。
        """
        query = state["query"]
        background = _context_block(state)
        prompt = (
            "你是初中理科学习助教，背后知识库是教材讲义。学生刚才说的是与具体"
            "理科知识点学习无关的话（寒暄/闲聊/致谢/闲聊式提问）。请用一两句简短、"
            "自然、友好的话回应（结合对话背景，避免机械重复），并在结尾自然地把"
            "学生引导回物理/化学/数学知识点提问。不要编造教材内容，不要长篇大论。\n"
            + (f"【对话背景】\n{background}\n" if background else "")
            + f"学生说：{query}"
        )
        response = _stream_answer(answer_llm, prompt)
        log.info("[workflow.respond_chitchat] 闲聊回复完成, 长度=%d 字符", len(response))
        return {"final_answer": response, "messages": [AIMessage(content=response)]}

    # 组装状态机工作流
    def route_by_intent(state: CircuitAgentState) -> str:
        """按意图分流：find_problem 走语义搜题链路，offtopic 走轻量直答，其余走图谱链路。"""
        intent = state.get("intent")
        if intent == "find_problem":
            return "search_problems"
        if intent == "offtopic":
            return "respond_chitchat"
        return "graph_traversal"

    workflow = StateGraph(CircuitAgentState)
    # chat 模式上下文管理：置于入口，analyze_intent 之前先做截断/摘要
    workflow.add_node("manage_context", manage_context_node)
    workflow.add_node("analyze_intent", analyze_intent_node)
    workflow.add_node("graph_traversal", graph_traversal_node)
    workflow.add_node("fetch_chunks", fetch_chunks_node)
    workflow.add_node("generate_response", generate_response_node)
    workflow.add_node("search_problems", search_problems_node)
    workflow.add_node("generate_problem_response", generate_problem_response_node)
    workflow.add_node("respond_chitchat", respond_chitchat_node)

    workflow.add_edge(START, "manage_context")
    workflow.add_edge("manage_context", "analyze_intent")
    workflow.add_conditional_edges(
        "analyze_intent", route_by_intent,
        {"graph_traversal": "graph_traversal",
         "search_problems": "search_problems",
         "respond_chitchat": "respond_chitchat"})
    workflow.add_edge("graph_traversal", "fetch_chunks")
    workflow.add_edge("fetch_chunks", "generate_response")
    workflow.add_edge("generate_response", END)
    workflow.add_edge("search_problems", "generate_problem_response")
    workflow.add_edge("generate_problem_response", END)
    workflow.add_edge("respond_chitchat", END)

    return workflow.compile(checkpointer=checkpointer)