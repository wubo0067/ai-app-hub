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
- examples         例题：编号 + 小标题 + 归属题型 + 结构化出处 source(含 page) + 考点概念
  （例题原文不再由 LLM 抄写，改由 page 页码到向量库回表取讲义页原文）
- methods          通法技巧
- extra_relations  补充关系

入库策略：
1. 图库（ScienceGraphStore）：实体为节点（subject:Kind:name 学科命名空间隔离），
   依据 prerequisites / related_concepts / question_type 字段自动建边，
   形成「概念拆解链 -> 公式/实验 -> 题型 -> 例题」的可溯源检索结构；
2. 向量库（Chroma）：各类实体各存一份可检索文本，并额外写入「讲义页切片」
   （metadata["id"]=subject:Page:页码），供图谱命中例题后按 page 精确回表取原文。

长文档增量建库（2026-09-05 起）：不再要求整段页码范围一次喂给推理 LLM。
build_knowledge_bases 内部按 max_chars 字符预算把输入页自动切成若干子块
（优先在章节标题页之间切），逐块做两批串行抽取并立即写双库、落盘图谱；
处理每个新子块前注入「滚动上下文」（该学科已建章节标题 + 向量库检索到的
相关概念），使跨子块抽取的概念/章节命名保持一致，避免隐性重复。每子块
抽取缓存只由该子块自身内容决定，重复执行同命令即自动跳过已处理子块
（断点续跑，不重复计费）；max_chunks 可限制单次最多处理的新子块数。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import get_reasoning_llm
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
    bare_name,
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


def normalize_subject(subject: str) -> str:
    """把用户传入的学科写法归一化为内部标识，非法值直接报错。

    公开导出：学科别名的唯一事实源，供 CLI（main.py）与建库流程共同复用。
    """
    key = str(subject).strip().lower()
    if key not in _VALID_SUBJECT_ALIASES:
        raise ValueError(f"不支持的学科 '{subject}'，可选：physics/chemistry/math（或中文 物理/化学/数学）")
    return _VALID_SUBJECT_ALIASES[key]


def _strip_code_fence(text: str) -> str:
    """去掉 LLM 可能附带的三反引号围栏。"""
    return text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()


# JSON 字符串中的合法转义序列：\" \\ \/ \b \f \n \r \t \uXXXX
_JSON_VALID_ESCAPE_RE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _repair_json_escapes(text: str) -> str:
    """修复 JSON 文本里的非法反斜杠转义（仅 json.loads 失败后兜底调用）。

    模型常把讲义中的 LaTeX 公式记法（反斜杠后接中文/字母，如 raw backslash
    加汉字）原样抄进字符串字段，导致解析报 Invalid escape。本函数逐个扫描：
    合法的 JSON 转义（引号/反斜杠/斜杠/b/f/n/r/t 及 u 加 4 位十六进制）原样
    保留；其余反斜杠补成双反斜杠，使整体可被重新解析。合法转义不受影响，
    不会对已是合法 JSON 的文本造成二次破坏。
    """
    parts: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch != "\\" or i + 1 >= n:
            parts.append(ch)
            i += 1
            continue
        m = _JSON_VALID_ESCAPE_RE.match(text, i)
        if m is not None:
            parts.append(m.group(0))
            i = m.end()
            continue
        # 非法转义：补一个反斜杠（字符串字面 "\\\\" 求值为两个反斜杠字符）
        parts.append("\\\\")
        i += 1
    return "".join(parts)


# 抽取 schema 版本：prompt/结构变更时递增，用于失效旧缓存
# v3：增量建库（自动分块 + 已建章节/概念滚动上下文注入 + 跨块题型回退挂边）
# 语义变化使旧版整批抽取缓存不再适用，故递增使旧缓存整体失效。
_EXTRACT_SCHEMA_VERSION = "v3"
# 抽取结果缓存目录（按输入哈希落盘，相同输入二次构建直接跳过 LLM）
_CACHE_DIR = Path(__file__).resolve().parent / "output" / "extract_cache"

# 知识抽取单子块字符预算：整本教材按预算自动切成若干子块，每次只把一个
# 子块的 Markdown 交给推理 LLM（两批串行），避免单次输入/输出超上下文。
_CHUNK_MAX_CHARS_DEFAULT = 6000
# 子块已攒到该比例且下一页是章节标题时，提前切块，避免把新章节标题留在块尾。
_CHUNK_HEADING_FACTOR = 0.6
# 滚动上下文：处理子块前从向量库检索的「已建相关概念」条数上限（含去重后）
_KNOWN_CONTEXT_TOP_K = 12
# 滚动上下文：注入的已建章节标题条数上限（同科跨多册教材长时间累积时兜底）
_KNOWN_CONTEXT_CHAPTERS_CAP = 120
# 审计疑似重复概念对的名称相似度下限（difflib.SequenceMatcher，0~1）
_DUP_CONCEPT_RATIO = 0.82

# 顺序敏感字段：值为「步骤序列」（推导步骤 / 解题模板 / 实验·方法操作步骤）。
# 跨来源（跨子块 / 跨 PDF）合并时若 union，两套序列会被错乱串成一套
# （3 步模板 + 4 步模板 = 7 步乱序流程），故这类字段与标量一致按
# 「保留更长一份」合并；其余列表字段（breakdown / common_mistakes / traps /
# sources 等无序要点集合）才做 union 去重合并。
_SEQUENCE_FIELDS = frozenset({"derivation", "template", "steps"})


def _cache_key(subject: str, full_markdown: str) -> str:
    """抽取结果缓存键：学科 + schema 版本 + 输入全文的 sha256 前 16 位。"""
    raw = f"{subject}|{_EXTRACT_SCHEMA_VERSION}|{full_markdown}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _load_extract_cache(key: str) -> Optional[Dict[str, Any]]:
    """读取抽取缓存；不存在或损坏返回 None。"""
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("[ingestion] 抽取缓存损坏，忽略: %s", path)
        return None
    if not isinstance(data, dict):
        return None
    log.info("[ingestion] 命中抽取缓存: %s（跳过 LLM 抽取）", key)
    return data


def _save_extract_cache(key: str, data: Dict[str, Any]) -> None:
    """把抽取结果写入缓存目录，供相同输入下次复用。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("[ingestion] 抽取缓存写入失败: %s", e)


# ------------------------------------------------------------- 增量建库：分块
def _split_into_chunks(pages_data: List[Dict[str, Any]],
                       *, max_chars: int = _CHUNK_MAX_CHARS_DEFAULT
                       ) -> List[List[Dict[str, Any]]]:
    """把页序列按字符预算切成若干子块，优先在 Markdown 标题行处断开。

    页面是原子单位（每页讲义需以 subject:Page:页码 独立入向量库供例题回表），
    因此只在「页与页之间」切：
    - 累加超过预算，或已攒到预算 60% 且下一页是章节标题（###/##/#）时切一刀；
    - 单页内容超预算时强制单独成块（不跨页拆内容）。
    每个子块内的页保持原顺序、页码连续。返回 chunks: [ [页dict, ...], ... ]。
    """
    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_len = 0
    for p in pages_data:
        content = str(p.get("content") or "")
        starts_new_section = bool(re.match(r"^#{1,3}\s", content.lstrip()))
        if cur and (
            cur_len + len(content) > max_chars
            or (starts_new_section and cur_len > max_chars * _CHUNK_HEADING_FACTOR)
        ):
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(content)
    if cur:
        chunks.append(cur)
    return chunks


def _chunk_markdown(chunk: List[Dict[str, Any]]) -> str:
    """把一个子块的页列表拼成带页标记的 Markdown（缓存 key 的输入全文）。"""
    return "\n\n".join(
        f"--- 第 {p['page']} 页 ---\n{str(p.get('content') or '')}" for p in chunk)


def _chunk_label(chunk: List[Dict[str, Any]], index: int, total: int) -> str:
    """子块日志标签：子块 i/n（页 x-y）。"""
    pages = sorted(int(p["page"]) for p in chunk if p.get("page") is not None)
    span = f"页 {pages[0]}-{pages[-1]}" if pages else f"{len(chunk)} 页"
    return f"子块 {index}/{total}（{span}）"


# 解析失败时的纠错追加指令：要求只输出严格 JSON 后重试一次
_JSON_RETRY_HINT = (
    "\n\n【纠错要求】你上一次的输出无法被解析为合法 JSON。"
    "请重新输出：只输出一个严格合法的 JSON 对象，"
    "不要任何解释文字、不要 markdown 代码围栏。"
)

# 各实体类型中「必须为数组」的字段（LLM 偶尔把数组写成字符串，
# 无脑 list() 会静默拆成单字符污染图谱）
_ENTITY_KINDS = (
    "chapters", "concepts", "formulas", "experiments",
    "question_types", "examples", "methods", "extra_relations",
)
_LIST_FIELDS: Dict[str, Tuple[str, ...]] = {
    "concepts": ("breakdown", "common_mistakes", "prerequisites"),
    "formulas": ("symbols", "derivation", "related_concepts"),
    "experiments": ("apparatus", "steps", "exam_focus", "related_concepts"),
    "methods": ("steps", "related_concepts"),
    "question_types": ("identify_features", "template", "traps", "related_concepts"),
    "examples": ("related_concepts",),
}
# 各实体中「必须为字符串」的字段：LLM 可能输出数字/对象，统一 str 化防 .strip() 崩溃
_STR_FIELDS: Dict[str, Tuple[str, ...]] = {
    "chapters": ("title", "summary"),
    "concepts": ("name", "description", "chapter"),
    "formulas": ("name", "expression", "applicable_scope"),
    "experiments": ("name", "purpose", "phenomenon", "conclusion", "diagram"),
    "methods": ("name", "scope"),
    "question_types": ("name",),
    "examples": ("id", "title", "question_type"),
    "extra_relations": ("from", "rel", "to"),
}


def _as_list(value: Any, label: str, field: str, stringify: bool = True) -> List[Any]:
    """应为数组的字段安全取值：非数组记警告并返回 []，杜绝字符串被拆成单字符。"""
    if value is None:
        return []
    if isinstance(value, list):
        if stringify:
            return [v if isinstance(v, str) else str(v) for v in value]
        return value
    log.warning("[ingestion] %s.%s 期望数组，实际为 %s，已丢弃: %r",
                label, field, type(value).__name__, str(value)[:80])
    return []


def _normalize_extracted(data: Dict[str, Any]) -> None:
    """就地归一化抽取结果（含旧缓存脏数据）：实体须为对象、字符串字段 str 化、
    数组字段校验类型。

    在写库与建向量之前统一执行一次，覆盖 _write_graph / _build_vector_docs
    两个消费方，避免逐点防御。
    """
    for kind in _ENTITY_KINDS:
        items = data.get(kind)
        if items is None:
            data[kind] = []
            continue
        if not isinstance(items, list):
            log.warning("[ingestion] %s 顶层期望数组，实际为 %s，已置空",
                        kind, type(items).__name__)
            data[kind] = []
            continue
        cleaned: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                log.warning("[ingestion] %s 存在非对象条目，已丢弃: %r",
                            kind, str(it)[:80])
                continue
            for f in _STR_FIELDS.get(kind, ()):
                v = it.get(f)
                if v is not None and not isinstance(v, str):
                    it[f] = str(v)
            for f in _LIST_FIELDS.get(kind, ()):
                lab = f"{kind}[{it.get('name') or it.get('id') or '?'}]"
                got = _as_list(it.get(f), lab, f, stringify=(f != "symbols"))
                if f == "symbols":  # 符号表元素必须是对象
                    got = [s for s in got if isinstance(s, dict)]
                it[f] = got
            if kind == "examples":
                src = it.get("source")
                if src is None:
                    it["source"] = {}
                elif not isinstance(src, dict):
                    log.warning("[ingestion] 例题 %s 的 source 期望对象，实际为 %s，已置空",
                                it.get("id", "?"), type(src).__name__)
                    it["source"] = {}
            cleaned.append(it)
        data[kind] = cleaned


def _extract_json(llm: Any, prompt: str, label: str, max_attempts: int = 2,
                  meter: Any | None = None) -> Dict[str, Any]:
    """调用 LLM 并解析为 JSON 对象；解析失败时追加纠错提示重试一次，仍失败才抛错。

    抽取是整本书级别的高成本操作（两批 LLM 调用），不应因一次格式错误全部作废；
    与 pdf_processor._invoke_llm 的重试策略对齐。meter 为可选 TokenMeter 兼容
    对象（.add(response)），累计真实 token 消耗。
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        log.debug("[ingestion] %s：调用 LLM 抽取（第 %d/%d 次）...",
                  label, attempt, max_attempts)
        res = ""
        clean = ""
        try:
            response = llm.invoke(prompt if attempt == 1 else prompt + _JSON_RETRY_HINT)
            if meter is not None:
                meter.add(response)
            res = str(response.content).strip()
            clean = _strip_code_fence(res)
            data = json.loads(clean)
            if not isinstance(data, dict):
                raise ValueError(f"返回的不是 JSON 对象: {type(data)}")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            # 最后一道保险：若是字符串里夹带了未转义反斜杠（模型把 LaTeX 记法
            # 原样抄进字段所致），先就地修复转义再解析一次；成功即返回，
            # 失败才落到警告并追加纠错提示重试。
            if isinstance(e, json.JSONDecodeError):
                try:
                    fixed = json.loads(_repair_json_escapes(clean))
                    if isinstance(fixed, dict):
                        log.warning("[ingestion] %s 第 %d 次输出含非法转义反斜杠，已就地修复后解析成功",
                                    label, attempt)
                        return fixed
                except (json.JSONDecodeError, ValueError):
                    pass  # 修复无效：按原流程警告并进入下一轮重试
            last_err = e
            log.warning("[ingestion] %s 第 %d 次输出解析失败: %s（前 200 字符: %s）",
                        label, attempt, e, clean[:200] or res[:200])
    log.error("[ingestion] %s 连续 %d 次解析失败，放弃", label, max_attempts)
    raise RuntimeError(f"[ingestion] {label} JSON 抽取失败: {last_err}")


def _build_context_block(label: str, chapters: List[str],
                         known_concepts: List[str]) -> str:
    """滚动上下文注入块：全书已有章节 + 已建相关概念。

    增量建库时每次只喂一个子块，模型看不到此前抽过什么；不注入已知信息就会
    出现「同一概念被起不同名字」「同一章节反复开新章」导致图谱隐性重复。
    两个列表为空（全书第一批内容）时返回空串，不干扰正常建库。
    """
    parts: List[str] = []
    if chapters:
        parts.append(
            f"【{label}全书已有章节（若本批文本属于这些章节之一，其 title 必须逐字复用"
            "下列标题，严禁另开新章；下列之外的本章新章节按原文正常新建）】\n"
            + "\n".join(f"- {t}" for t in chapters))
    if known_concepts:
        parts.append(
            "【已建库的相关概念（若本批文本出现的是同一概念，name 必须逐字复用下列名称，"
            "严禁另起同义新名；未列出的新概念按原文标准名词正常新建）】\n"
            + "\n".join(f"- {c}" for c in known_concepts))
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts) + "\n"


def _build_knowledge_prompt(subject: str, markdown: str,
                            chapters: Optional[List[str]] = None,
                            known_concepts: Optional[List[str]] = None) -> str:
    """第一批：知识体系（章节/概念/公式/实验/方法）抽取提示词。

    chapters / known_concepts 为滚动上下文（见 _gather_known_context），
    用于保证跨子块命名一致；缺省为空列表表示全书首批内容。
    """
    label = SUBJECT_META[subject]["label"]
    guide = SUBJECT_META[subject]["guide"]
    context = _build_context_block(label, list(chapters or []), list(known_concepts or []))
    return f"""你是深耕初中{label}教学的教研专家。请从下面的教材/讲义文本中提取【知识体系】，
只输出一个严格的 JSON 对象（不要 ```json 围栏、不要任何解释文字）：

{markdown}

【学科】{label}
{guide}
{context}
【JSON 结构（所有字段均可选，没有的内容填空数组/空串，严禁编造教材里不存在的实体）】
注意：字符串值若含公式反斜杠（如 LaTeX 记法），须转义为双反斜杠，或改用纯文本描述公式，避免整批输出解析失败。
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
  "methods": [
    {{"name": "方法名", "scope": "适用场景", "steps": ["步骤"], "related_concepts": ["关联概念名"]}}
  ]
}}

【要求】
1. name 使用原文中的标准名词。
2. 这是教研知识库抽取，请把核心价值做足：概念给出 breakdown 拆解要点与易错点，
   公式给出推导/变形思路与适用条件，实验写全器材/步骤/现象/结论。
3. 文本中根本没有实验时 experiments 返回 []，不要编造。
4. 本批只做知识体系，不要输出题型/例题/补充关系。
5. 若本批实体与【已建库/全书已有章节】列表中的名称指同一概念或同一章节，
   name/title 必须逐字复用，不得另起同义名；复用不等于补写，只抽取本批
   文本里实际出现的内容。
"""


def _build_question_prompt(subject: str, markdown: str, concept_names: List[str],
                           chapters: Optional[List[str]] = None,
                           known_concepts: Optional[List[str]] = None) -> str:
    """第二批：题型与例题抽取提示词（注入第一批概念名，保证引用一致、原文不回抄）。

    chapters / known_concepts 为滚动上下文（同 _build_knowledge_prompt），
    使题型/例题能引用到此前子块已建的概念，避免跨块引用被本批概念名列表挡掉。
    """
    label = SUBJECT_META[subject]["label"]
    names_json = json.dumps(concept_names, ensure_ascii=False)
    context = _build_context_block(label, list(chapters or []), list(known_concepts or []))
    return f"""你是深耕初中{label}教学的教研专家。请从下面的教材/讲义文本中提取【题型与例题】，
只输出一个严格的 JSON 对象（不要 ```json 围栏、不要任何解释文字）：

{markdown}

【学科】{label}
{context}
【本批已抽取的概念名（related_concepts / extra_relations 可引用下列名称，不得杜撰）】
{names_json}

【JSON 结构（所有字段均可选，没有的内容填空数组/空串，严禁编造教材里不存在的实体）】
注意：字符串值若含公式反斜杠（如 LaTeX 记法），须转义为双反斜杠，或改用纯文本描述公式，避免整批输出解析失败。
{{
  "question_types": [
    {{"name": "题型名", "identify_features": ["题干识别特征"], "template": ["解题模板步骤"],
      "traps": ["常见陷阱"], "related_concepts": ["考点概念名"]}}
  ],
  "examples": [
    {{"id": "原题编号如 例17", "title": "小标题",
      "question_type": "归属题型名（与本批 question_types.name 对应，无则填空串）",
      "source": {{"book": "教材/册", "chapter": "章节", "page": 页码, "number": "题号"}},
      "related_concepts": ["考点概念名"]}}
  ],
  "extra_relations": [{{"from": "概念名", "rel": "关系英文名", "to": "概念名"}}]
}}

【要求】
1. 例题只输出编号/小标题/归属题型/出处(含 page)/考点概念，【不要】抄写题干原文、答案或解析
   （原文由 page 页码到向量库回表获取，抄写会严重拖慢抽取）。
2. page 必须填该例题所在讲义页码（正文 "--- 第 N 页 ---" 标记里的 N），这是回表取原文的关键，务必准确。
3. related_concepts 只能取自「本批概念名列表」或【已建库的相关概念】中的名称；
   无对应实体的字段填 []。
4. extra_relations 是【概念与概念】之间的补充关联，from/to 必须取自「本批概念名列表」
   或【已建库的相关概念】，不得填公式/实验/题型/方法名。
5. 归属题型若是此前子块已建的题型（本批未重复声明），question_type 填其原题型名，
   不要因为不在本批就填空串。
"""


def _ensure_entity(graph_db: ScienceGraphStore, subject: str, kind: str,
                   name: str, **attrs: Any) -> str:
    """确保实体节点存在并返回其 node_key；已存在时按「越建越全」策略合并属性。

    旧实现为 setdefault（先到先得）：同名概念第二次写入时新内容被静默丢弃，
    且空串/空列表旧值永不更新（空壳节点永远补不全）。两本不同 PDF 讲同一
    知识点（同名概念是真同一知识点）时，内容应当越建越全而非被第一本锁死。
    合并规则（仅对已存在节点逐属性生效）：
    - 旧值缺失或为空（None/""/[]/{}）→ 补上新值；
    - 本次传入值为空 → 不动旧值（空抽取不冲刷已收录内容）；
    - 无序集合类列表（breakdown/common_mistakes/sources...）→ union 去重保序；
    - 顺序敏感字段（_SEQUENCE_FIELDS：derivation/template/steps）与标量字符串
      → 保留更长的一份（更详细的表述），避免两套步骤序列被错乱拼接。
    """
    key = node_key(subject, kind, name)
    if key not in graph_db.graph:
        graph_db.add_entity(subject, kind, name, **attrs)
    else:
        cur = graph_db.graph.nodes[key]
        for k_, v_ in attrs.items():
            if v_ is None or v_ == "" or v_ == [] or v_ == {}:
                continue  # 本次是空值：保留节点已有内容
            old = cur.get(k_)
            if old is None or old == "" or old == [] or old == {}:
                cur[k_] = v_  # 旧值缺失/为空：补全（修掉空壳永不更新的锁定）
                continue
            if isinstance(old, list) and isinstance(v_, list):
                if k_ in _SEQUENCE_FIELDS:
                    if len(v_) > len(old):
                        cur[k_] = v_
                else:
                    cur[k_] = old + [x for x in v_ if x not in old]
            elif isinstance(old, str) and isinstance(v_, str) and len(v_) > len(old):
                cur[k_] = v_
    return key


def _write_graph(graph_db: ScienceGraphStore, subject: str, data: Dict[str, Any], *,
                 pdf_id: Optional[str] = None) -> None:
    """把抽取出的实体与关系写入图库（核心编排逻辑）。

    pdf_id：PDF 内容哈希前 16 位。仅用于两类需要「来源维度」的键：
    - 概念节点的 sources 累积（多本教材共同收录 = 更值得重点讲的核心考点信号）；
    - 例题节点键前缀（不同书的「例17」是不同题目，须隔离，否则同名互相锁死）。
    概念/公式/实验/题型/方法等知识实体同名=真同一知识点，不做来源隔离，
    靠 _ensure_entity 的越建越全合并累积两本书的内容。
    """
    # --- 章节 ---
    # 章节同样走 _ensure_entity 而非 add_entity：分块只在标题页之间切，不保证单章
    # 不超过 max_chars，长章节跨子块是常态；后续子块经滚动上下文会"逐字复用"同一
    # 章节标题，但其 summary 只含本批片段（甚至空串）。若用 add_entity，networkx 的
    # add_node 会覆盖同名 key，片段/空 summary 会直接顶掉先前子块写好的完整概要。
    # 走 _ensure_entity 后：空值不冲刷、标量字符串保留更长，与其它实体合并策略一致。
    for ch in data.get("chapters", []):
        title = ch.get("title", "").strip()
        if title:
            _ensure_entity(graph_db, subject, "Chapter", title,
                           title=title, summary=ch.get("summary", ""))

    # --- 概念（含先修链）---
    concept_names: List[str] = []
    for c in data.get("concepts", []):
        name = c.get("name", "").strip()
        if not name:
            continue
        concept_names.append(name)
        c_attrs: Dict[str, Any] = dict(
            description=c.get("description", ""),
            breakdown=list(c.get("breakdown", [])),
            common_mistakes=list(c.get("common_mistakes", [])),
            chapter=c.get("chapter", ""))
        if pdf_id:
            # 收录来源：同名概念跨 PDF 累积时 union 合并（先建者的 sources 不清空）
            c_attrs["sources"] = [pdf_id]
        _ensure_entity(graph_db, subject, K_CONCEPT, name, **c_attrs)
        for pre in c.get("prerequisites", []):
            pre = str(pre).strip()
            if pre and pre != name:
                pre_key = _ensure_entity(graph_db, subject, K_CONCEPT, pre)
                graph_db.relate(pre_key, REL_PREREQUISITE_OF,
                                node_key(subject, K_CONCEPT, name))

    def _link_concept_refs(refs: Any, rel: str, key: str) -> None:
        """把 related_concepts 等引用转成概念节点出/入边；引用不存在的概念
        告警跳过，不新建同名空壳（防幽灵节点）。"""
        for ref in refs or []:
            ref = str(ref).strip()
            if not ref:
                continue
            ckey = node_key(subject, K_CONCEPT, ref)
            if ckey not in graph_db.graph:
                log.warning("[ingestion] %s 的 %s 引用了不存在的概念，跳过: %s",
                            bare_name(key), rel, ref)
                continue
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
        # 例题编号跨 PDF 大概率重复（教辅按章节从 1 编：例17/例1/型1...），
        # 不同书的同名例题是不同题目；key 带 pdf_id 前缀隔离，否则会被当作
        # 同一节点，title/answer 被先建者锁死且挂到错误题型/概念下。
        ex_key = _ensure_entity(graph_db, subject, K_EXAMPLE,
                                f"{pdf_id}:{ex_name}" if pdf_id else ex_name,
                                title=ex.get("title", ""),
                                answer=ex.get("answer", ""),
                                analysis=ex.get("analysis", ""),
                                question_type=ex.get("question_type", ""),
                                source=src,
                                pdf_id=pdf_id or "")
        qt_name = str(ex.get("question_type") or "").strip()
        if qt_name:
            qt_key = qt_keys.get(qt_name)
            if qt_key is None:
                # 跨块兜底：题型可能在前置子块已定义（本批未重复声明题型），
                # 不能只查本批 qt_keys，否则 chunk A 定义题型、chunk B 出例题
                # 的场景会永久丢边。本批查不到就查全局持久化图。
                candidate = node_key(subject, K_QUESTION_TYPE, qt_name)
                if candidate in graph_db.graph:
                    qt_key = candidate
                    log.info("[ingestion] 例题 %s 归属题型 %s 已在全局图存在（跨块），补挂边",
                             ex_name, qt_name)
                else:
                    log.warning("[ingestion] 例题 %s 的归属题型 %s 不在本批题型中，"
                                "全局图亦不存在，跳过挂边", ex_name, qt_name)
            if qt_key is not None:
                graph_db.relate(qt_key, REL_EXEMPLIFIED_BY, ex_key)
        _link_concept_refs(ex.get("related_concepts"), REL_TESTS, ex_key)

    # --- 补充关系：两端必须是已抽取的概念节点，禁止新建同名空壳（防幽灵节点）---
    for r in data.get("extra_relations", []):
        src, dst = str(r.get("from", "")).strip(), str(r.get("to", "")).strip()
        rel = str(r.get("rel") or "").strip() or REL_EXTRA
        if not src or not dst or src == dst:
            continue
        src_key = node_key(subject, K_CONCEPT, src)
        dst_key = node_key(subject, K_CONCEPT, dst)
        if src_key not in graph_db.graph or dst_key not in graph_db.graph:
            log.warning("[ingestion] extra_relations 端点不是已抽取概念，跳过: %s -[%s]-> %s",
                        src, rel, dst)
            continue
        graph_db.relate(src_key, rel, dst_key)


def _audit_graph(graph_db: ScienceGraphStore, subject: str) -> None:
    """构建后审计：列出本学科无描述的空壳概念节点（幽灵节点），并报告疑似重复概念对。

    疑似重复由概念名相似度（difflib.SequenceMatcher）扫描得出，阈值 _DUP_RATIO，
    只扫有描述的概念（空壳节点不参与，避免先修引用噪音）；仅报告不动库，
    用户核对后可用 graph_db.merge_concepts(subject, 规范名, 冗余名) 显式合并。
    """
    concepts = {bare_name(key): nd for key, nd in graph_db.graph.nodes(data=True)
                if nd.get("subject") == subject and nd.get("type") == K_CONCEPT}
    shells = [name for name, nd in concepts.items()
              if not str(nd.get("description", "")).strip()]
    if shells:
        log.warning("[ingestion] 审计: %s 科存在 %d 个空壳概念节点（无描述，"
                    "多为 prerequisites 前置引用或历史脏数据）: %s",
                    subject, len(shells), "、".join(shells[:20]))
    else:
        log.info("[ingestion] 审计: %s 科无空壳概念节点", subject)

    # 疑似重复概念对：同科内两两比较名称相似度，报告最像的一批
    dup_pairs: List[Tuple[str, str, float]] = []
    described = sorted(n for n in concepts
                       if str(concepts[n].get("description", "")).strip())
    for i, a in enumerate(described):
        for b in described[i + 1:]:
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio >= _DUP_CONCEPT_RATIO:
                dup_pairs.append((a, b, ratio))
    dup_pairs.sort(key=lambda t: t[2], reverse=True)
    if dup_pairs:
        shown = dup_pairs[:10]
        log.warning("[ingestion] 审计: %s 科发现 %d 对疑似重复概念"
                    "（名称相似度 ≥ %.2f，多为增量建库前的历史命名漂移）: %s",
                    subject, len(dup_pairs), _DUP_CONCEPT_RATIO,
                    " | ".join(f"{a} ≈ {b} ({r:.2f})" for a, b, r in shown))
        log.warning("[ingestion] 审计: 如需合并请显式执行 "
                    "graph_db.merge_concepts(subject='%s', canonical=规范名, alias=冗余名)；"
                    "合并会重定向该概念全部关系并删除冗余名节点。", subject)
    else:
        log.info("[ingestion] 审计: %s 科概念命名一致，无疑似重复", subject)


def _build_vector_docs(subject: str, data: Dict[str, Any], *,
                       pdf_id: Optional[str] = None) -> List[Document]:
    """把各类实体各转为一条可检索文本（metadata["id"] 与图节点键一致）。"""
    docs: List[Document] = []

    def _push(kind: str, name: str, content: str, **extra_meta: Any) -> None:
        """追加一条向量切片：正文为拼接后的实体文本，metadata 携带回表标识。

        - name 为空（LLM 漏填）时直接跳过，避免产生无主切片；
        - metadata["id"] 复用 node_key 生成，与图节点键完全一致，
          检索命中后可凭此 id 精确回图数据库取实体全文；
        - extra_meta 传入各实体特有的补充字段（如 chapter、page）。
        """
        if not name.strip():  # 无名实体不入向量库
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
        # 切片 id 与图节点键一致：例题节点键带 pdf_id 前缀（跨书同名编号隔离），
        # 向量切片 id 必须同步，否则检索/审计回不到同一实体；正文仍展示裸编号
        key_name = f"{pdf_id}:{name}" if pdf_id else name
        lines = [f"【{name}】{ex.get('title', '')}"]
        if ex.get("question_type"):
            lines.append("归属题型：" + ex["question_type"])
        if src.get("page") is not None:
            lines.append(f"出处页码：{src.get('page')}")
        _push(K_EXAMPLE, key_name, "\n".join(lines),
              title=ex.get("title", ""), page=src.get("page"))

    return docs


def _build_page_docs(subject: str, pages_data: List[Dict[str, Any]], *,
                     pdf_id: Optional[str] = None) -> List[Document]:
    """把每页讲义原文转为「页切片」向量文档（metadata["id"]=subject:Page:页码）。

    例题不再抄写原文，问答时按例题 source.page 回表取对应页全文。
    pdf_id 并入切片键：裸页码跨 PDF 会撞车（两本书的第 15 页互相覆盖、
    回表张冠李戴），键改为 subject:Page:{pdf_id}:{页码}；pdf_id 为空时
    保持旧键 subject:Page:{页码}（兼容未传 pdf_id 的旧调用路径）。
    """
    docs: List[Document] = []
    for p in pages_data:
        page = p.get("page")
        content = (p.get("content") or "").strip()
        if page is None or not content:
            continue
        page_token = f"{pdf_id}:{page}" if pdf_id else str(page)
        meta: Dict[str, Any] = {"id": node_key(subject, "Page", page_token),
                                "subject": subject, "type": "Page", "page": page}
        if pdf_id:
            meta["pdf_id"] = pdf_id
        docs.append(Document(page_content=f"--- 第 {page} 页 ---\n{content}",
                             metadata=meta))
    return docs


def _gather_known_context(vector_db: Chroma, graph_db: ScienceGraphStore,
                          subject: str, chunk_markdown: str,
                          top_k: int = _KNOWN_CONTEXT_TOP_K
                          ) -> Tuple[List[str], List[str]]:
    """滚动上下文：返回 (已建章节标题列表, 已建相关概念摘要列表)。

    增量建库时模型只能看到当前子块，看不到此前建过的内容；这两个列表让 LLM
    「记得」全书已建章节与相关概念，解决跨子块命名不一致 / 章节重复开章问题：
    - 章节：轻量全量（graph_db 里该学科所有 Chapter 节点 title），全书量级小；
    - 相关概念：用当前子块前 2000 字符去向量库做相似度检索（限定 subject +
      Concept），只取 top_k 条 name + 一句话描述，控制 prompt 体积不随全书
      概念总数线性增长。
    """
    chapters: List[str] = sorted({
        nd.get("title") or bare_name(key)
        for key, nd in graph_db.graph.nodes(data=True)
        if nd.get("subject") == subject and nd.get("type") == "Chapter"
    })[:_KNOWN_CONTEXT_CHAPTERS_CAP]

    concepts: List[str] = []
    query = chunk_markdown[:2000].strip()
    if query and vector_db is not None:
        try:
            # 注意：Chroma where 顶层只能有一个字段条件或一个逻辑操作符，
            # 多字段 AND 必须显式写 $and 数组，否则抛
            # "Expected where to have exactly one operator"（滚动上下文会静默失效）。
            # filter 形参类型存根过窄（dict[str, str]），此处用显式 dict 绕开
            # 静态检查，运行时原样透传给 ChromaDB 的 where。
            where: Dict[str, Any] = {"$and": [{"subject": subject}, {"type": K_CONCEPT}]}
            hits = vector_db.similarity_search(query, k=top_k, filter=where)
        except Exception:  # noqa: BLE001
            log.warning("[ingestion] 检索已建相关概念失败，忽略并继续抽取", exc_info=True)
            hits = []
        for h in hits:
            meta = h.metadata or {}
            name = str(meta.get("id") or "").rsplit(":", 1)[-1].strip()
            if not name:
                continue
            body_lines = (h.page_content or "").splitlines()
            desc = body_lines[1].strip() if len(body_lines) > 1 else ""
            concepts.append(f"{name}：{desc[:40]}" if desc else name)
    return chapters, concepts


def _extract_chunk_data(subject: str, md: str, label: str,
                        vector_db: Chroma, graph_db: ScienceGraphStore,
                        meter: Any | None = None) -> Dict[str, Any]:
    """对单个子块做两批串行抽取（带滚动上下文注入），返回合并后的实体 dict。

    第二批会拿到第一批抽出的概念名（块内引用一致）；两批共用同一份
    chapters / known_concepts（块外已建内容，块间命名一致）。
    """
    chapters, concepts = _gather_known_context(vector_db, graph_db, subject, md)
    if chapters:
        log.info("[ingestion] %s：已知全书章节 %d 个（滚动上下文）", label, len(chapters))
    if concepts:
        log.info("[ingestion] %s：检索到已建相关概念 %d 条（滚动上下文）", label, len(concepts))
    llm = get_reasoning_llm(enable_thinking=False)
    k_data = _extract_json(
        llm, _build_knowledge_prompt(subject, md, chapters, concepts),
        f"{label}·知识体系", meter=meter)
    concept_names = [c.get("name", "").strip()
                     for c in k_data.get("concepts", []) if c.get("name", "").strip()]
    q_data = _extract_json(
        llm, _build_question_prompt(subject, md, concept_names, chapters, concepts),
        f"{label}·题型例题", meter=meter)
    return {**k_data, **q_data}


def _persist_chunk(chunk: List[Dict[str, Any]], subject: str,
                   vector_db: Chroma, graph_db: ScienceGraphStore,
                   data: Dict[str, Any], *, pdf_id: Optional[str] = None) -> int:
    """归一化 + 写图 + 写向量（实体切片 + 本块讲义页切片）+ 图谱落盘。

    每处理完一个子块就 graph_db.save() 一次：一次 CLI 调用可能跑几十次 LLM，
    中途崩溃也只丢当前子块（已处理子块均已持久化），配合按子块内容的抽取
    缓存即天然获得断点续跑。返回本块写入的向量切片条数。
    """
    # 归一化：校验实体/字段类型（含旧缓存脏数据），杜绝字符串被静默拆成单字符
    _normalize_extracted(data)
    n_kind = {k: len(data.get(k, [])) for k in
              ("chapters", "concepts", "formulas", "experiments",
               "question_types", "examples", "methods", "extra_relations")}
    log.info("[ingestion] JSON 抽取成功: %s",
             ", ".join(f"{k}={v}" for k, v in n_kind.items()))

    # 1. 写入 Graph DB
    _write_graph(graph_db, subject, data, pdf_id=pdf_id)

    # 2. 写入 Vector DB（实体切片 + 本块讲义页切片，按 metadata["id"] 幂等 upsert）
    docs = (_build_vector_docs(subject, data, pdf_id=pdf_id)
            + _build_page_docs(subject, chunk, pdf_id=pdf_id))
    if docs:
        vector_db.add_documents(docs, ids=[d.metadata["id"] for d in docs])

    # 3. 图谱落盘：向量库由 Chroma 自动持久化，图谱需显式 save 才能跨进程累积
    graph_db.save()
    return len(docs)


def build_knowledge_bases(
    pages_data: List[Dict[str, Any]],
    subject: str = "physics",
    *,
    vector_db: Optional[Chroma] = None,
    graph_db: Optional[ScienceGraphStore] = None,
    max_chars: int = _CHUNK_MAX_CHARS_DEFAULT,
    max_chunks: Optional[int] = None,
    meter: Any | None = None,
    pdf_id: Optional[str] = None,
) -> Tuple[Chroma, ScienceGraphStore]:
    """从 Markdown 中提取结构化知识网络，写入 Vector DB 与 Graph DB。

    同一 vector_db / graph_db 可跨多次调用、跨学科累积（全科知识库）。

    支持长文档增量建库（增量，而非一次性抽全书）：
    - 输入页数超过单子块预算（max_chars，默认 6000 字符）时，先按
      _split_into_chunks 自动切成若干子块（优先在章节标题页之间切），
      逐块做两批串行抽取并写库；每个子块处理完即 graph_db.save() 一次，
      崩溃/中断不丢已处理子块；
    - 每个子块的抽取缓存 key 只取决于该子块自身内容，相同输入重跑自动
      命中缓存（0 次 LLM 调用）——CLI 重复执行同一条命令即断点续跑，
      已处理子块不会被重复计费；
    - 处理子块前注入滚动上下文（已建章节 + 向量库检索到的相关概念），
      让跨子块/跨 CLI 调用时概念命名保持一致，避免图谱隐性重复；
    - max_chunks 限制本次最多处理 N 个「未命中缓存的新子块」（缓存命中
      不占额度），达到上限主动停，剩余子块下次执行同命令续跑。

    多本不同 PDF 累积进同一知识库时（如两本教材都讲「比热容」），须传 pdf_id
    （PDF 内容 SHA-256 前 16 位，与 pdf_processor._pdf_id 同算法）以隔离两类
    无来源维度的键：讲义页切片（subject:Page:页码 → 带 pdf_id 前缀）与例题节点
    （不同书的「例17」是不同题目）。不传 pdf_id 保持旧键（向后兼容，单书场景
    行为不变）。概念/公式/实验/题型/方法等知识实体同名=真同一知识点，不做来源
    隔离，节点属性按「越建越全」策略合并（无序列表 union、标量与步骤序列保留
    更长一份、concepts 额外累积 sources 字段记录收录来源）。

    推理模型固定由 config.py + sida-agent/.env 的 REASONING_* 配置决定。
    meter 为可选 TokenMeter 兼容对象（.add(response)），累计真实 token 消耗。
    """
    subject = normalize_subject(subject)
    log.info("[ingestion] 开始构建知识库: subject=%s, 输入页数=%d",
             SUBJECT_META[subject]["label"], len(pages_data))
    vector_db = vector_db or get_vector_store()
    graph_db = graph_db or ScienceGraphStore.load()
    if node_key(subject, K_SUBJECT, subject) not in graph_db.graph:
        graph_db.add_entity(subject, K_SUBJECT, subject, label=SUBJECT_META[subject]["label"])

    chunks = _split_into_chunks(pages_data, max_chars=max_chars)
    log.info("[ingestion] 输入 %d 页自动切分为 %d 个子块（子块预算 %d 字符）",
             len(pages_data), len(chunks), max_chars)
    if not chunks:
        log.warning("[ingestion] 输入页为空，无可构建内容")
        graph_db.save()
        return vector_db, graph_db

    done_new = 0          # 实际新调用 LLM 的子块数（缓存命中的不占 --max-chunks 额度）
    total_docs = 0        # 全部子块写入的向量切片总数
    for idx, chunk in enumerate(chunks, start=1):
        label = _chunk_label(chunk, idx, len(chunks))
        cache_key = _cache_key(subject, _chunk_markdown(chunk))
        data = _load_extract_cache(cache_key)
        if data is None:
            if max_chunks is not None and done_new >= max_chunks:
                remain = len(chunks) - idx + 1
                log.info("[ingestion] 已达 --max-chunks=%d 上限，剩余 %d 个子块未处理；"
                         "已完成子块均已持久化并缓存，重新执行相同命令即可续跑"
                         "（已缓存块不再计费）", max_chunks, remain)
                break
            done_new += 1
            data = _extract_chunk_data(subject, _chunk_markdown(chunk), label,
                                       vector_db, graph_db, meter=meter)
            _save_extract_cache(cache_key, data)
        else:
            log.info("[ingestion] %s 命中抽取缓存，直接写库（不调用 LLM）", label)
        total_docs += _persist_chunk(chunk, subject, vector_db, graph_db, data,
                                     pdf_id=pdf_id)

    # 构建后审计：暴露空壳概念节点（幽灵节点）；审计放整轮结束后，避免逐块刷屏
    _audit_graph(graph_db, subject)
    # 兜底落盘：覆盖 max_chunks 提前退出等路径
    graph_db.save()

    log.info("[ingestion] 构建完成（本轮新抽取子块 %d 个）: 图节点 %d 个, 向量切片 %d 条。",
             done_new, graph_db.graph.number_of_nodes(), total_docs)
    return vector_db, graph_db