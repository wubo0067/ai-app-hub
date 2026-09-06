#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初中理科全科知识库 Agent 入口。

流程：
1. extract_pdf_pages_as_markdown：用多模态视觉模型将 PDF
   指定物理页提取为 Markdown；
2. build_knowledge_bases(subject=...)：对页文本提炼全科知识网络
   （概念拆解/公式推导/实验图解/题型溯源），写入向量库 + 图谱库；
   可对 physics/chemistry/math 分别喂入对应教材并共享同一份双库，
   累积成三科知识库；
3. create_circuit_agent：构建全科问答 Agent（自动判定学科并检索）；
4. invoke：对学生提问生成分层讲解并打印。

模型服务（base_url / api_key / model_name）统一在 sida-agent/.env 中配置，
见 config.py。

命令行用法（换材料无需改源码）：
    uv run python main.py --stage build --pdf 教材.pdf --start-page 11 --end-page 12 --subject physics
    uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-chunks 20
    uv run python main.py --stage build --pdf 整本教材.pdf --start-page 13 --end-page 320 --subject math --max-new-calls 20
    uv run python main.py --stage ask   --query "讲解可变电路的分析思路"
    uv run python main.py                          # 不带参数 = 下方默认值，等价旧行为
--stage: all=提取+建库+问答（默认）；build=仅提取并累加进双库；ask=仅复用已持久化双库问答。
长文档：页码区间可以开很大（整本书），build_knowledge_bases 会按 --max-chars
预算自动切子块增量抽取；建库前会先打印规模预估并请求确认（--yes 跳过）。
--max-chunks 限制推理抽取侧每轮新子块数，--max-new-calls 限制视觉提取侧每轮新页数，
两者相互独立：已达其一即停，已完成部分已逐页/逐块落盘缓存，重跑同命令续跑。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import pymupdf

from agent.workflow import create_circuit_agent
from ingestion import (
    _CHUNK_MAX_CHARS_DEFAULT,
    _cache_key,
    _chunk_markdown,
    _load_extract_cache,
    _split_into_chunks,
    build_knowledge_bases,
    normalize_subject,
)
from logger import get_logger
from pdf_processor import (_load_cached_pages, _pdf_id,
                           extract_pdf_pages_as_markdown)
from storage.graph_store import ScienceGraphStore
from storage.vector_store import get_vector_store

log = get_logger()

# CLI 默认值：不带参数运行即等价于旧版硬编码流水线，便于快速回归。
DEFAULT_PDF = "L:/vivi/初三/物理/9S合并PDF-完整.pdf"
DEFAULT_START_PAGE = 11
DEFAULT_END_PAGE = 12
DEFAULT_SUBJECT = "physics"
DEFAULT_QUERY = "请帮我系统讲解可变电路的分析思路，并用具体的典型例题带我推导一遍"


def _subject_choice(value: str) -> str:
    """--subject 参数解析：归一化到 physics/chemistry/math，非法值报命令行错误。

    复用 ingestion.normalize_subject 作为学科别名的唯一事实源（中文/拼音亦可），
    配合 argparse choices 把取值固定为三种。
    """
    try:
        return normalize_subject(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args() -> argparse.Namespace:
    """解析命令行参数；缺省时回退到模块顶部的默认值。"""
    parser = argparse.ArgumentParser(
        description="初中理科全科知识库 Agent：PDF 提取 → 建库 → 问答。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--stage", choices=("all", "build", "ask"), default="all",
        help="all=提取+建库+问答；build=仅提取并累加进双库；ask=仅复用已持久化双库问答。",
    )
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="教材 PDF 路径（build/all 阶段使用）。")
    parser.add_argument("--start-page", type=int, default=DEFAULT_START_PAGE,
                        help="起始页码（从 1 计）。")
    parser.add_argument("--end-page", type=int, default=DEFAULT_END_PAGE,
                        help="结束页码（含），超出总页数自动截断。")
    parser.add_argument("--subject", type=_subject_choice, default=DEFAULT_SUBJECT,
                        choices=("physics", "chemistry", "math"),
                        help="学科，仅限三种：physics/物理、chemistry/化学、math/数学"
                             "（接受中文或拼音别名，自动归一化）。")
    parser.add_argument("--max-chars", type=int, default=_CHUNK_MAX_CHARS_DEFAULT,
                        help="知识抽取单子块字符预算（build/all）：输入页超过预算即自动"
                             "切块增量抽取，避免整本书一次喂给推理 LLM 超上下文。")
    parser.add_argument("--max-chunks", type=int, default=None, metavar="N",
                        help="本次建库最多处理 N 个未命中缓存的新子块（缓存命中不占额度），"
                             "达到即主动停，已完成子块已落盘/缓存，重跑同命令续跑。")
    parser.add_argument("--max-new-calls", type=int, default=None, metavar="N",
                        help="本次视觉提取最多新调用 N 次（已缓存页不占额度），达到即主动停；"
                             "已完成页已逐页缓存，重跑同命令续跑（控视觉模型成本，"
                             "与 --max-chunks 同套分批消费模式）。")
    parser.add_argument("--yes", action="store_true",
                        help="跳过建库前的规模预估确认（脚本/夜间批量自动放行）。")
    parser.add_argument("--query", default=DEFAULT_QUERY,
                        help="学生提问（ask/all 阶段使用）。")
    return parser.parse_args()


def _normalize_math_delims(text: str) -> str:
    """把模型偏好的 \\[ \\] / \\( \\) 公式定界符转成跨阅读器通用的 $$ / $。

    标准 Markdown 里 \\[ 与 \\] 是方括号的转义，会被渲染成字面 [ 和 ]，
    使公式显示成 "[ P_{\\text{总}}=P_1+P_2+\\cdots ]" 这种难读形式；
    而块级 $$ ... $$ 与行内 $ ... $ 在 VS Code 预览 / GitHub / Typora /
    Obsidian 等常见阅读器中均能渲染。本函数对模型输出做兜底归一化。
    """
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]",
                  lambda m: "$$\n" + m.group(1).strip() + "\n$$", text, flags=re.S)
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)",
                  lambda m: "$" + m.group(1).strip() + "$", text, flags=re.S)
    return text


def _save_answer_markdown(result: dict, fallback_subject: str) -> Path:
    """把解答结果写成 Markdown 文件（output/answers/），返回文件路径。

    文件名带时间戳与学科，多次提问互不覆盖；内容含元信息 + 提问 + 讲解，
    便于用任意 Markdown 阅读器查看（讲解正文中的「（见教材第 X 页）」等
    来源标注在渲染后同样可读）。
    """
    ts = datetime.now()
    subject = result.get("target_subject") or fallback_subject
    out_dir = Path("output") / "answers"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"answer_{ts:%Y%m%d_%H%M%S}_{subject}.md"
    lines = [
        "# 问答讲解结果",
        "",
        f"- 生成时间：{ts:%Y-%m-%d %H:%M:%S}",
        f"- 学科：{subject}",
    ]
    concept = result.get("target_concept") or ""
    if concept:
        lines.append(f"- 知识锚点：{concept}")
    lines += [
        "", "## 提问", "", result.get("query", ""),
        "", "## 讲解", "", result.get("final_answer", ""), "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class TokenMeter:
    """LLM token 累计器：从 ChatOpenAI 响应的 usage 元数据取数（实际消耗）。

    兼容 langchain AIMessage 的 usage_metadata（input/output_tokens），
    取不到时回退 response_metadata.token_usage（prompt/completion_tokens）；
    服务端未返回 usage 的调用不计入 calls。仅供建库前后打印真实成本，
    不影响任何业务逻辑。
    """

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def add(self, response: Any) -> None:
        um = getattr(response, "usage_metadata", None)
        if um:
            prompt = um.get("input_tokens") or 0
            completion = um.get("output_tokens") or 0
        else:
            usage = ((getattr(response, "response_metadata", None) or {})
                     .get("token_usage") or {})
            prompt = usage.get("prompt_tokens") or 0
            completion = usage.get("completion_tokens") or 0
        if not prompt and not completion:
            return
        self.prompt_tokens += int(prompt)
        self.completion_tokens += int(completion)
        self.calls += 1

    def report(self, label: str) -> None:
        if self.calls:
            log.info("[cost] %s 实际消耗：%d 次调用，输入 %d / 输出 %d tokens",
                     label, self.calls, self.prompt_tokens, self.completion_tokens)
        else:
            log.info("[cost] %s：无实际模型调用（全部命中缓存）", label)


# 无任何缓存页可参考时的单页平均字符估算值（仅用于切块预演，非计费依据）
_ESTIMATE_UNKNOWN_PAGE_CHARS = 2000


def _estimate_build(pdf_path: str, start_page: int, end_page: int,
                    subject: str, max_chars: int,
                    max_vision_calls: Optional[int] = None) -> dict:
    """建库规模预估（干跑）：只读逐页缓存 + 按预算预演切块，不调用任何模型。

    max_vision_calls：视觉提取侧分批上限（对应 extract_pdf_pages_as_markdown
    的 max_new_calls）。传入时按与真实运行完全相同的顺序遍历页：缓存页先纳入，
    未缓存页计数并纳入，达到上限即停——上限之后的页（含其间尚未提取的缓存页）
    本次不处理。因此预估/确认里的 new_vision_calls 与切块预演范围都反映「本批
    真实会做的工作」，而不是把整个大区间一次亮出来误导确认（分批模式下数字不虚高）。

    返回 dict：
    - range_pages：用户请求的页码范围总页数（可能大于本次实际处理数）
    - processed_pages / cached_pages / new_vision_calls / skipped_pages：视觉提取侧
      （逐页缓存，精确；skipped_pages 为本次因上限而未处理的剩余页）
    - plan_chunks / cached_chunks / new_chunks：推理抽取侧（每子块两批 LLM，
      仅对本次实际处理页预演切块）
    - vision_capped：本次视觉提取是否被上限截断（还有剩余页待续跑）
    - approx_len：未缓存页的估算平均字符数（内容未知时仅用于预演切块）
    含未提取页时，切块/缓存命中判断基于估算内容长度，可能与真实运行略有出入；
    真实消耗以结束时的 TokenMeter 为准。
    """
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 文件不存在：{pdf_path}")
    cached = _load_cached_pages(pdf_path)
    with pymupdf.open(pdf) as doc:
        total_pages = doc.page_count
    start = max(1, start_page)
    end = min(total_pages, end_page)
    if start > end:
        raise ValueError(f"页范围 {start}-{end} 无效（PDF 共 {total_pages} 页）。")

    page_nums = list(range(start, end + 1))
    # 与 extract_pdf_pages_as_markdown 同序遍历：缓存页不占额度直接纳入；未缓存页
    # 计数并纳入，达到 max_vision_calls 即停（其后的页本次不进切块/抽取预演）。
    processed: List[int] = []
    new_vision_calls = 0
    for p in page_nums:
        if p in cached:
            processed.append(p)
            continue
        if max_vision_calls is not None and new_vision_calls >= max_vision_calls:
            break
        new_vision_calls += 1
        processed.append(p)
    skipped_pages = len(page_nums) - len(processed)
    cached_pages = sum(1 for p in processed if p in cached)

    lengths = [len(cached[p]) for p in processed if p in cached]
    est_len = round(sum(lengths) / len(lengths)) if lengths else _ESTIMATE_UNKNOWN_PAGE_CHARS
    # 未缓存页用无标题占位文本（长度≈均值）预演切块：与真实运行共用同一套
    # _split_into_chunks，保证边界规则一致（仅内容未知导致的偏差不可避免）。
    synth = [
        {"page": p, "content": cached[p] if p in cached else "　" * est_len}
        for p in processed
    ]
    plan = _split_into_chunks(synth, max_chars=max_chars)

    cached_chunks = 0
    for chunk in plan:
        pages = [c["page"] for c in chunk]
        if all(pg in cached for pg in pages):  # 仅整块已缓存时缓存 key 才可精确判定
            real = [{"page": pg, "content": cached[pg]} for pg in pages]
            if _load_extract_cache(_cache_key(subject, _chunk_markdown(real))) is not None:
                cached_chunks += 1
    new_chunks = len(plan) - cached_chunks
    return {
        "range_pages": len(page_nums),
        "processed_pages": len(processed),
        "cached_pages": cached_pages,
        "new_vision_calls": new_vision_calls,
        "skipped_pages": skipped_pages,
        "plan_chunks": len(plan),
        "cached_chunks": cached_chunks,
        "new_chunks": new_chunks,
        "approx_len": est_len,
        "vision_capped": skipped_pages > 0,
    }


def _print_estimate(est: dict, max_chars: int) -> None:
    """打印规模预估（建库正式开始前、未调用任何模型时）。"""
    log.info("[main] 本次任务规模预估（只读缓存 + 本地统计，未调用任何模型）:")
    if est["vision_capped"]:
        # 视觉侧被 --max-new-calls 截断：明确告诉用户本批处理量与剩余待续跑页数
        log.info("  页码范围共 %d 页，本次视觉提取 %d 页（新调用 %d + 已缓存 %d），"
                 "剩余 %d 页留待下次续跑",
                 est["range_pages"], est["processed_pages"], est["new_vision_calls"],
                 est["cached_pages"], est["skipped_pages"])
    else:
        log.info("  页码范围共 %d 页 | 视觉提取：已缓存 %d 页，需新调用 %d 次",
                 est["range_pages"], est["cached_pages"], est["new_vision_calls"])
    log.info("  知识抽取：按 --max-chars=%d 自动切 %d 个子块，"
             "已缓存 %d 个，需新抽取 %d 个（约 %d 次推理 LLM 调用）",
             max_chars, est["plan_chunks"], est["cached_chunks"],
             est["new_chunks"], est["new_chunks"] * 2)
    if est["new_vision_calls"]:
        log.info("  （含本批未提取页 %d 页：内容未知，切块按平均页长 %d 字预演，"
                 "实际块数可能略有出入；真实 token 消耗以结束时统计为准）",
                 est["new_vision_calls"], est["approx_len"])


def _confirm_build(est: dict, yes: bool) -> bool:
    """建库放行确认：全部命中缓存直接放行；有新调用时交互确认或 --yes 放行。"""
    new_calls = est["new_vision_calls"] + est["new_chunks"]
    if new_calls <= 0:
        log.info("[main] 全部命中缓存，直接执行（本次不产生新模型调用）")
        return True
    if yes:
        log.info("[main] --yes：跳过确认直接执行（预估 %d 次新调用）", new_calls)
        return True
    if not sys.stdin.isatty():
        log.error("[main] 本次将产生约 %d 次新模型调用，且当前不是交互终端；"
                  "请确认规模后加 --yes 重新执行，或先用 --start-page/--end-page/"
                  "--max-chunks/--max-new-calls 缩小本次范围", new_calls)
        return False
    try:
        answer = input("是否继续？[y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _report_meters(vision_meter: TokenMeter, reasoning_meter: TokenMeter) -> None:
    """建库结束汇总：打印视觉/推理两路真实 token 消耗。"""
    vision_meter.report("视觉提取")
    reasoning_meter.report("知识抽取")
    total_in = vision_meter.prompt_tokens + reasoning_meter.prompt_tokens
    total_out = vision_meter.completion_tokens + reasoning_meter.completion_tokens
    log.info("[cost] 本次建库累计实际消耗：输入 %d / 输出 %d tokens",
             total_in, total_out)


def main() -> None:
    # 密钥优先从 .env / 环境读取（见 config.py）：
    # 视觉 VISION_* / 推理 REASONING_* / Embedding OPENAI_*
    args = parse_args()

    # 共享的双库实例：跨进程持久化，多学科教材可反复累积进同一份知识库
    vector_db = get_vector_store()          # Chroma 本地持久化，自动加载历史切片
    graph_db = ScienceGraphStore.load()     # 有历史图谱则加载，无则新建空库

    # ---- 建库阶段（all / build）：多模态提取 PDF 指定页 → 结构化抽取累积进双库
    if args.stage in ("all", "build"):
        log.info("[main] 流水线启动, PDF=%s, 页码=%d-%d, subject=%s",
                 args.pdf, args.start_page, args.end_page, args.subject)
        # 花钱前先亮规模：只读逐页缓存 + 按预算预演切块（不调用任何模型）；
        # 全部命中缓存时自动放行，有新调用时交互确认（--yes 跳过）。
        # max_vision_calls 传入 --max-new-calls：预估/确认只亮本批真实会做的量。
        est = _estimate_build(args.pdf, args.start_page, args.end_page,
                              args.subject, args.max_chars,
                              max_vision_calls=args.max_new_calls)
        _print_estimate(est, args.max_chars)
        if not _confirm_build(est, args.yes):
            log.info("[main] 已取消本次建库（未调用任何模型）")
            return
        vision_meter = TokenMeter()
        reasoning_meter = TokenMeter()
        pages_data = extract_pdf_pages_as_markdown(
            pdf_path=args.pdf,
            start_page=args.start_page,
            end_page=args.end_page,
            meter=vision_meter,
            max_new_calls=args.max_new_calls,
        )
        # 同一 vector_db/graph_db 多次调用即跨学科累积全科知识库。
        # 页码区间可开很大：内部按 max_chars 自动切子块增量抽取，
        # max_chunks 限制本次处理的新子块数（达上限主动停，重跑续跑）。
        # pdf_id = PDF 内容哈希前 16 位：并入讲义页切片 / 例题节点键，
        # 使不同 PDF 累积进同一知识库时「第 N 页」「例17」互不覆盖（跨书撞车隔离）
        pdf_id = _pdf_id(Path(args.pdf))
        vector_db, graph_db = build_knowledge_bases(
            pages_data=pages_data,
            subject=args.subject,
            vector_db=vector_db,
            graph_db=graph_db,
            max_chars=args.max_chars,
            max_chunks=args.max_chunks,
            meter=reasoning_meter,
            pdf_id=pdf_id,
        )
        _report_meters(vision_meter, reasoning_meter)
        log.info("[main] 建库完成（stage=%s）", args.stage)

    # ---- 问答阶段（all / ask）：先判定学科再检索，生成分层讲解
    if args.stage in ("all", "ask"):
        agent = create_circuit_agent(vector_db=vector_db, graph_db=graph_db)
        log.info("[main] Agent 正在处理学生提问: %s", args.query)
        print("=" * 60)
        print("【解答生成结果】:\n")
        result: dict = {}
        try:
            # 流式输出：messages 模式把节点内 LLM 调用逐 token 推送（按
            # langgraph_node 过滤，只打印 generate_response 的正文，意图判定
            # 等节点的输出不上屏）；values 模式每个节点后给一份状态快照，
            # 取最后一份作为最终结果供保存。
            for mode, chunk in agent.stream(
                {"query": args.query}, stream_mode=["messages", "values"],
            ):
                if mode == "messages":
                    msg, meta = chunk
                    if meta.get("langgraph_node") == "generate_response":
                        print(str(msg.content), end="", flush=True)
                else:
                    result = chunk
        except Exception:
            log.exception("[main] Agent 执行失败")
            raise
        print("\n" + "=" * 60)
        # 公式定界符兜底归一化：\[ \] / \( \) -> $$ / $，保证 md 跨阅读器可读
        # （仅作用于保存的 md 文件；控制台流式输出为模型原始 token 流）
        result["final_answer"] = _normalize_math_delims(result["final_answer"])
        out_path = _save_answer_markdown(result, args.subject)
        log.info("[main] 解答已保存: %s", out_path)
        print(f"\n[已保存] {out_path.resolve()}")

    log.info("[main] 流水线完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 顶层兜底：任何未捕获异常（含建库阶段 RuntimeError 等）都把完整
        # traceback 写入日志文件，便于离线排查；随后继续抛出让退出码/控制台
        # 堆栈保持不变。
        log.exception("[main] 流水线异常终止，完整堆栈:")
        raise