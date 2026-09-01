#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 教材页面 -> Markdown（印刷体、公式、图表、手写批注）。

流程：全页初稿 -> 高分辨率重叠切片核查 -> 最终逐项校对。
默认 quality=balanced；显存/时间足够可用 --quality max。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf  # PyMuPDF 旧版导入名
    except ImportError as exc:
        raise SystemExit(
            "缺少 PyMuPDF。请先运行：python -m pip install -U pymupdf"
        ) from exc
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

try:
    import httpx
except ImportError:
    import httpx2 as httpx  # type: ignore[no-redef]


PDF_PATH = r"H:\wechat_files\xwechat_files\calm-wu_9d75\msg\file\2026-08\9S合并PDF.pdf"
OUTPUT_PATH = "output/notes.md"
CACHE_DIR = "cache"
BASE_URL = "http://localhost:11636/v1"
MODEL_NAME = "qwen2.5vl:7b"
API_KEY = "ollama"

LLM_TIMEOUT = 900
LLM_RETRIES = 2
IMAGE_FORMAT = "png"
JPEG_QUALITY = 92
CACHE_VERSION = "v7-multipass-20260901"


@dataclass(frozen=True)
class QualityConfig:
    full_zoom: float
    full_long_edge: int
    detail_zoom: float
    tile_count: int
    tile_overlap: float
    draft_tokens: int
    detail_tokens: int
    final_tokens: int
    use_detail_pass: bool


QUALITY_CONFIGS = {
    "fast": QualityConfig(2.7, 2600, 3.2, 0, 0.0, 8192, 0, 0, False),
    "balanced": QualityConfig(3.2, 3200, 4.2, 3, 0.22, 8192, 4096, 10000, True),
    "max": QualityConfig(3.8, 3800, 5.0, 4, 0.25, 10000, 5000, 12000, True),
}


PROMPT_FULL_PAGE = r"""
你是“教材页面忠实转写器”。输入是一张完整的中文教材页，可能同时含印刷体、公式、
电路/机械图、表格和多色手写批注。请先生成一份 Markdown 初稿。

硬性规则：
1. 按从上到下、从左到右的阅读顺序，逐字保留标题、栏目名、正文、题干、全部选项、
   公式、图注；不要总结、改写或依据常识补句。
2. 选择题的 A/B/C/D 选项必须逐项独立成行。印刷选项与手写答案是两种信息，绝不能
   把手写答案写进印刷题干，也不能把一题的批注复制到另一题。
3. 每处手写在对应题目或段落后单独写成“【手写】...”。圈、勾、箭头、划线本身不是
   答案；只有字母被明确圈选/书写时，才可写“【手写】选：A、C”。看不清就写 [?]，
   禁止猜测。
4. 图中的手写计算式、物理量增减箭头也要逐项转写。对非文字图形补一行简短的
   “> 图示：...”，描述元件、连接关系和箭头，不做题目求解。
5. 公式使用 LaTeX；表格用列数一致的 Markdown 表格；忽略纯页码与商标。
6. 只输出 Markdown 正文，不使用代码围栏，不解释你的工作。
""".strip()


PROMPT_DETAIL_TILE = r"""
这是教材页的一个高分辨率纵向切片。你的任务是“核查证据”，不是求解题目。
切片与相邻切片有重叠，所以只记录这张图中能直接看见的内容。

特别检查：小号印刷字、上下标、分式、选项字母、手写字、圈选/勾选、箭头，以及手写
究竟属于哪一道题。严禁把附近另一题的答案挪过来。仅当字母清楚可见时才报告选择题
手写答案；只有空圈或划线时，selected_options 必须为空。无法辨认写 [?]，不要猜。

只输出一个合法 JSON 对象，不加代码围栏，格式如下：
{
  "visible_printed_markdown": "按阅读顺序的忠实转写；切片截断处注明[截断]",
  "questions": [
    {
      "label": "例17/题号；未知则空串",
      "printed_options": {"A": "...", "B": "..."},
      "handwritten_answer": [],
      "answer_evidence": "具体看见了什么；没有则空串"
    }
  ],
  "handwriting": [
    {"anchor": "所对应的题号/印刷文字", "text": "忠实转写", "confidence": "high/medium/low"}
  ],
  "figures": ["图中实际可见的元件、连接和箭头"],
  "uncertain": ["不能可靠辨认的局部"]
}
""".strip()


PROMPT_FINAL = r"""
你是教材数字化的终审校对员。你会看到：完整页图、第一遍 Markdown 初稿，以及若干
高分辨率切片的核查结果。请输出该页最终 Markdown。

校对优先级：高分辨率切片的直接视觉证据 > 完整页直接视觉证据 > 初稿文字。切片可能
重叠，重复内容只保留一次。

逐项验收：
- 所有标题、正文、例题题干、A/B/C/D 选项、公式、图表说明都完整且顺序正确；
- 每个手写内容只归属它旁边的题目/段落，绝不跨题复制；
- “圈/勾/划线”与“明确写出的答案字母”严格区分；证据冲突或字迹不清时写
  “【手写，低置信度】[?]”，不得用学科知识猜答案；
- 保留可读的手写计算过程、箭头与增减结论；
- 公式用 LaTeX，选择题选项逐行，表格列数一致；
- 不求解、不纠正教材或老师内容、不补充图外知识；
- 忽略纯页码与商标；只输出 Markdown 正文，不加代码围栏或说明。
""".strip()


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _strip_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md|json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"<think(?:\s[^>]*)?>.*?</think\s*>", "", text, flags=re.S | re.I)
    return text.strip()


def _safe_json(text: str) -> dict[str, Any] | None:
    text = _strip_fence(text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                pass
    return None


def _collapse_repeats(text: str, min_reps: int = 3, max_unit: int = 16) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        folded = False
        for size in range(min(max_unit, (len(lines) - i) // min_reps), 0, -1):
            unit = lines[i : i + size]
            j, reps = i + size, 1
            while lines[j : j + size] == unit:
                reps += 1
                j += size
            if reps >= min_reps:
                out.extend(unit)
                out.append(f"<!-- 已折叠 {reps - 1} 处模型复读 -->")
                i, folded = j, True
                break
        if not folded:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _pixmap_bytes(
    page: pymupdf.Page,
    *,
    zoom: float,
    max_long_edge: int | None = None,
    clip: pymupdf.Rect | None = None,
) -> bytes:
    area = clip if clip is not None else page.rect
    if max_long_edge:
        zoom = min(zoom, max_long_edge / max(area.width, area.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    if IMAGE_FORMAT.lower() in {"jpg", "jpeg"}:
        return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
    return pix.tobytes("png")


def _data_url(image: bytes) -> str:
    mime = "image/jpeg" if IMAGE_FORMAT.lower() in {"jpg", "jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


def _vertical_tiles(page: pymupdf.Page, count: int, overlap: float) -> list[pymupdf.Rect]:
    """生成覆盖整页的重叠纵向切片；不依赖 PDF 文本层。"""
    if count <= 1:
        return [page.rect]
    rect = page.rect
    tile_h = rect.height / (count - (count - 1) * overlap)
    step = tile_h * (1.0 - overlap)
    clips: list[pymupdf.Rect] = []
    for i in range(count):
        y0 = min(rect.y0 + i * step, rect.y1 - tile_h)
        y1 = rect.y1 if i == count - 1 else min(y0 + tile_h, rect.y1)
        clips.append(pymupdf.Rect(rect.x0, y0, rect.x1, y1))
    return clips


def _make_client(base_url: str, api_key: str) -> OpenAI:
    http_client = httpx.Client(trust_env=False, timeout=LLM_TIMEOUT)
    return OpenAI(base_url=base_url, api_key=api_key, http_client=http_client)


def _chat(
    client: OpenAI,
    model: str,
    messages: Iterable[ChatCompletionMessageParam],
    *,
    max_tokens: int,
) -> str:
    last_error: Exception | None = None
    for attempt in range(LLM_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=list(messages),
                max_tokens=max_tokens,
                temperature=0.0,
                timeout=LLM_TIMEOUT,
                extra_body={"think": False, "repeat_penalty": 1.08},
            )
            return _strip_fence(response.choices[0].message.content or "")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < LLM_RETRIES:
                wait = 2 * (attempt + 1)
                print(f"    ! 调用失败，{wait}s 后重试：{exc}")
                time.sleep(wait)
    raise RuntimeError(f"LLM 调用最终失败：{last_error}")


def _cache_get(cache: Path, name: str, enabled: bool) -> str | None:
    if not enabled:
        return None
    path = cache / name
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except (OSError, UnicodeDecodeError):
        return None


def _cache_put(cache: Path, name: str, value: str, enabled: bool) -> None:
    if enabled:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_text(value, encoding="utf-8")


def _source_fingerprint(pdf_path: str, model: str, quality: str) -> str:
    path = Path(pdf_path)
    stat = path.stat()
    prompts = PROMPT_FULL_PAGE + PROMPT_DETAIL_TILE + PROMPT_FINAL
    raw = "|".join([
        str(path.resolve()), str(stat.st_size), str(stat.st_mtime_ns), model, quality,
        CACHE_VERSION, _sha1(prompts),
    ])
    return _sha1(raw)


def _message_with_image(prompt: str, image: bytes) -> list[ChatCompletionMessageParam]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _data_url(image)}},
        ],
    }]


def _draft_page(
    page: pymupdf.Page,
    client: OpenAI,
    model: str,
    cfg: QualityConfig,
    cache: Path,
    key: str,
    use_cache: bool,
) -> tuple[str, bytes]:
    image = _pixmap_bytes(page, zoom=cfg.full_zoom, max_long_edge=cfg.full_long_edge)
    name = f"{key}_draft.md"
    cached = _cache_get(cache, name, use_cache)
    if cached is not None:
        print("    全页初稿：使用缓存")
        return cached, image
    start = time.perf_counter()
    draft = _chat(client, model, _message_with_image(PROMPT_FULL_PAGE, image),
                  max_tokens=cfg.draft_tokens)
    draft = _collapse_repeats(draft)
    _cache_put(cache, name, draft, use_cache)
    print(f"    全页初稿：{time.perf_counter() - start:.1f}s")
    return draft, image


def _inspect_tiles(
    page: pymupdf.Page,
    client: OpenAI,
    model: str,
    cfg: QualityConfig,
    cache: Path,
    key: str,
    use_cache: bool,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    clips = _vertical_tiles(page, cfg.tile_count, cfg.tile_overlap)
    for index, clip in enumerate(clips, start=1):
        name = f"{key}_tile_{index}.json"
        cached = _cache_get(cache, name, use_cache)
        if cached is None:
            image = _pixmap_bytes(page, zoom=cfg.detail_zoom, clip=clip)
            positional = (
                f"\n\n该切片为全页自上而下第 {index}/{len(clips)} 块；"
                f"纵向范围约为 {clip.y0 / page.rect.height:.0%} 到 "
                f"{clip.y1 / page.rect.height:.0%}。"
            )
            start = time.perf_counter()
            cached = _chat(
                client, model, _message_with_image(PROMPT_DETAIL_TILE + positional, image),
                max_tokens=cfg.detail_tokens,
            )
            _cache_put(cache, name, cached, use_cache)
            print(f"    高清切片 {index}/{len(clips)}：{time.perf_counter() - start:.1f}s")
        else:
            print(f"    高清切片 {index}/{len(clips)}：使用缓存")
        parsed = _safe_json(cached)
        reports.append(parsed if parsed is not None else {"raw_unparsed_report": cached})
    return reports


def _finalize_page(
    full_image: bytes,
    draft: str,
    reports: list[dict[str, Any]],
    client: OpenAI,
    model: str,
    cfg: QualityConfig,
    cache: Path,
    key: str,
    use_cache: bool,
) -> str:
    name = f"{key}_final.md"
    cached = _cache_get(cache, name, use_cache)
    if cached is not None:
        print("    终审校对：使用缓存")
        return cached
    evidence = json.dumps(reports, ensure_ascii=False, separators=(",", ":"))
    prompt = (
        PROMPT_FINAL + "\n\n===== 第一遍初稿（可能有错漏） =====\n" + draft
        + "\n\n===== 高清切片核查结果（可能相互重叠） =====\n" + evidence
    )
    messages: list[ChatCompletionMessageParam] = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _data_url(full_image)}},
        ],
    }]
    start = time.perf_counter()
    result = _chat(client, model, messages, max_tokens=cfg.final_tokens)
    result = _collapse_repeats(result)
    _cache_put(cache, name, result, use_cache)
    print(f"    终审校对：{time.perf_counter() - start:.1f}s")
    return result


def process_page(
    page: pymupdf.Page,
    page_index: int,
    client: OpenAI,
    model: str,
    quality: str,
    cache: Path,
    prefix: str,
    use_cache: bool,
) -> str:
    cfg = QUALITY_CONFIGS[quality]
    key = f"{prefix}_page_{page_index + 1}"
    draft, full_image = _draft_page(page, client, model, cfg, cache, key, use_cache)
    if not cfg.use_detail_pass:
        return draft
    reports = _inspect_tiles(page, client, model, cfg, cache, key, use_cache)
    return _finalize_page(full_image, draft, reports, client, model, cfg, cache, key, use_cache)


def merge_markdown(texts: dict[int, str]) -> str:
    parts: list[str] = []
    for page_index in sorted(texts):
        body = texts[page_index].strip() or "<!-- 本页未识别出内容 -->"
        parts.append(f"<!-- 第 {page_index + 1} 页 -->\n\n## 第 {page_index + 1} 页\n\n{body}")
    return "\n\n---\n\n".join(parts).rstrip() + "\n"


def parse_pages(value: str) -> list[int] | None:
    value = value.strip()
    if not value:
        return None
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start, end = int(left), int(right)
            if start > end:
                start, end = end, start
            if start < 1:
                raise ValueError("页码必须从 1 开始")
            pages.update(range(start - 1, end))
        else:
            page = int(token)
            if page < 1:
                raise ValueError("页码必须从 1 开始")
            pages.add(page - 1)
    return sorted(pages) or None


def run(
    pdf_path: str,
    output_path: str,
    pages: list[int] | None,
    *,
    base_url: str,
    api_key: str,
    model: str,
    quality: str,
    cache_dir: str,
    use_cache: bool,
) -> None:
    started = time.perf_counter()
    source = Path(pdf_path)
    if not source.is_file():
        raise FileNotFoundError(f"PDF 不存在：{source}")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(cache_dir)
    client = _make_client(base_url, api_key)
    doc = pymupdf.open(source)
    completed: dict[int, str] = {}
    failed = 0
    try:
        targets = pages if pages is not None else list(range(doc.page_count))
        invalid = [p + 1 for p in targets if p < 0 or p >= doc.page_count]
        if invalid:
            raise ValueError(f"页码超出范围（共 {doc.page_count} 页）：{invalid}")
        if not targets:
            raise ValueError("没有需要处理的页面")
        prefix = _source_fingerprint(str(source), model, quality)
        print("=" * 72)
        print("PDF -> Markdown 多阶段视觉提取（v7）")
        print(f"PDF：{source.resolve()}")
        print(f"页码：{[p + 1 for p in targets]}")
        print(f"模型：{model}；质量：{quality}；缓存：{'开' if use_cache else '关'}")
        print("=" * 72)
        for number, page_index in enumerate(targets, start=1):
            print(f"\n[{number}/{len(targets)}] 第 {page_index + 1} 页")
            try:
                completed[page_index] = process_page(
                    doc.load_page(page_index), page_index, client, model, quality,
                    cache, prefix, use_cache,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                completed[page_index] = f"<!-- 本页提取失败：{exc} -->"
                print(f"    x 失败：{exc}")
            out.write_text(merge_markdown(completed), encoding="utf-8")
        print("\n" + "=" * 72)
        print(f"完成：{len(targets) - failed} 页；失败：{failed} 页")
        print(f"耗时：{time.perf_counter() - started:.1f}s")
        print(f"输出：{out.resolve()}")
        print("=" * 72)
    finally:
        doc.close()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF 教材页面视觉提取为 Markdown")
    parser.add_argument("--pdf", default=PDF_PATH, help="输入 PDF 路径")
    parser.add_argument("--out", default=OUTPUT_PATH, help="输出 Markdown 路径")
    parser.add_argument("--pages", default="", help="页码，如 7、1,3-5")
    parser.add_argument("--base-url", default=BASE_URL, help="OpenAI 兼容接口地址")
    parser.add_argument("--api-key", default=API_KEY, help="接口密钥；Ollama 可任意非空")
    parser.add_argument("--model", default=MODEL_NAME, help="视觉模型名称")
    parser.add_argument(
        "--quality", choices=sorted(QUALITY_CONFIGS), default="balanced",
        help="fast=单次全页；balanced=3 切片复核；max=4 切片最高分辨率",
    )
    parser.add_argument("--cache-dir", default=CACHE_DIR, help="缓存目录")
    parser.add_argument("--no-cache", action="store_true", help="忽略且不写缓存")
    args = parser.parse_args()
    run(
        args.pdf, args.out, parse_pages(args.pages), base_url=args.base_url,
        api_key=args.api_key, model=args.model, quality=args.quality,
        cache_dir=args.cache_dir, use_cache=not args.no_cache,
    )


if __name__ == "__main__":
    main()

