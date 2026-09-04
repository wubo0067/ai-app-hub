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
    uv run python main.py --stage ask   --query "讲解可变电路的分析思路"
    uv run python main.py                          # 不带参数 = 下方默认值，等价旧行为
--stage: all=提取+建库+问答（默认）；build=仅提取并累加进双库；ask=仅复用已持久化双库问答。
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

from agent.workflow import create_circuit_agent
from ingestion import _normalize_subject, build_knowledge_bases
from logger import get_logger
from pdf_processor import extract_pdf_pages_as_markdown
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

    复用 ingestion._normalize_subject 作为学科别名的唯一事实源（中文/拼音亦可），
    配合 argparse choices 把取值固定为三种。
    """
    try:
        return _normalize_subject(value)
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
        pages_data = extract_pdf_pages_as_markdown(
            pdf_path=args.pdf,
            start_page=args.start_page,
            end_page=args.end_page,
        )
        # 同一 vector_db/graph_db 多次调用即跨学科累积全科知识库。
        vector_db, graph_db = build_knowledge_bases(
            pages_data=pages_data,
            subject=args.subject,
            vector_db=vector_db,
            graph_db=graph_db,
        )
        log.info("[main] 建库完成（stage=%s）", args.stage)

    # ---- 问答阶段（all / ask）：先判定学科再检索，生成分层讲解
    if args.stage in ("all", "ask"):
        agent = create_circuit_agent(vector_db=vector_db, graph_db=graph_db)
        log.info("[main] Agent 正在处理学生提问: %s", args.query)
        try:
            result = agent.invoke({"query": args.query})
        except Exception:
            log.exception("[main] Agent 执行失败")
            raise
        # 公式定界符兜底归一化：\[ \] / \( \) -> $$ / $，保证 md 跨阅读器可读
        result["final_answer"] = _normalize_math_delims(result["final_answer"])
        print("=" * 60)
        print("【解答生成结果】:\n")
        print(result["final_answer"])
        print("=" * 60)
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