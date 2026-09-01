#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - check-pdf-annotation

PDF → Markdown 智能提取流水线（v3）

设计目标：
1. 正常 PDF 文本：优先使用 pymupdf4llm，高速、低成本。
2. PDF 文本层乱码：自动检测，降级到 VLM 视觉识别。
3. 思维导图 / 复杂二维页面：可自动降级到 VLM，保留层级结构。
4. 手写批注：仅在可能存在手写时调用 VLM。
5. 视觉识别结果缓存，避免重复运行。
6. 对“正文视觉恢复 + 手写笔记”合并为一次 VLM 调用，避免乱码页重复识别。

注意：
- 这是基于原 main.py 改造的完整版本。
- 默认仍使用本地 OpenAI-compatible Ollama 网关。
"""

import argparse
import base64
import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any, cast

import httpx2
import pymupdf
import pymupdf4llm
from openai import OpenAI


# ============================================================================
# 配置
# ============================================================================

PDF_PATH = r"H:\wechat_files\xwechat_files\calm-wu_9d75\msg\file\2026-08\9S合并PDF.pdf"
OUTPUT_PATH = "output/notes.md"
CACHE_DIR = "cache"

BASE_URL = "http://localhost:11636/v1"
MODEL_NAME = "qwen3.8:27b-mtp-q4_K_M"

# PDF 渲染
RENDER_ZOOM = 1.5
MAX_LONG_EDGE = 1600

# PNG 对文字/手写通常最稳；JPEG 可减少请求体积。
# 可选：png / jpeg
IMAGE_FORMAT = "jpeg"
JPEG_QUALITY = 85

# LLM
LLM_TIMEOUT = 900
LLM_RETRIES = 1

# 不同任务限制输出长度，避免模型无谓生成大量 token。
MAX_TOKENS_DEFAULT = 2048
MAX_TOKENS_HANDWRITING = 2048
MAX_TOKENS_FORMULA_PAGE = 1536
MAX_TOKENS_RECOVERY = 4096

# 缓存版本：
# 修改 Prompt / 模型 / 核心识别策略后，建议修改该版本号，
# 避免继续使用旧缓存。
CACHE_VERSION = "v5"

# ---------------------------------------------------------------------------
# 公式提取
# ---------------------------------------------------------------------------
ENABLE_FORMULA_EXTRACTION = True
ENABLE_FORMULA_HEURISTIC_FALLBACK = False
FORMULA_RENDER_ZOOM = 2.2
FORMULA_MAX_LONG_EDGE = 1400
FORMULA_PADDING = 6.0
FORMULA_MERGE_GAP = 7.0
MAX_FORMULA_CANDIDATE_HEIGHT = 120.0

# ---------------------------------------------------------------------------
# 手写预筛
# ---------------------------------------------------------------------------

NO_NOTE_MARK = "NO_HANDWRITING"

# 老师电子笔迹常表现为贝塞尔曲线/弯折多段线。
# 本机 PDF 实测：有笔记页 >=3，无笔记页恒为 1（印刷花括号弧线）。
MIN_STROKE_PATHS = 2

# ---------------------------------------------------------------------------
# PDF 文本质量检测
# ---------------------------------------------------------------------------

# 中文教材通常应该有较高比例中文字符。
# 0.08 过松：本机实测第 3 页中文仅 9.5%（ASCII 60%，明显乱码）却漏检，
# 提到 0.15。纯英文页面若被误判，也只是多走一次 VLM，不会丢内容。
MIN_CHINESE_RATIO = 0.15

# 当英文 ASCII 字母比例很高、中文比例很低时，通常说明字体编码乱码。
MIN_ASCII_RATIO_FOR_GARBLED = 0.30

# 页面有效文本太少时，也可能是扫描 PDF / 图片 PDF。
MIN_EFFECTIVE_TEXT_CHARS = 8

# ---------------------------------------------------------------------------
# 复杂二维页面检测
# ---------------------------------------------------------------------------

# 是否检测思维导图、流程图、复杂二维结构。
ENABLE_VISUAL_STRUCTURE_DETECTION = True

# 图形对象很多时，可能是思维导图 / 流程图 / 表格等。
MIN_DRAWINGS_FOR_VISUAL_PAGE = 12

# 文本块数量较少但图形对象很多，更像二维结构图。
MAX_TEXT_BLOCKS_FOR_VISUAL_PAGE = 80

# 文本层结构损坏信号：pymupdf4llm 把公式变量拆成 _X_ 形式（如 _U_、_R_）
# 的斜体碎片数量。图形对象多但文本层完好的页（普通例题页的装饰线/表格边框）
# 不应送 VLM 恢复；只有文本层同时破碎才是真正需要恢复的结构页。
# 本机实测：第 5 页知识导航表格 = 29，正常文本页 = 0。
MIN_FORMULA_FRAGMENTS = 6


# ============================================================================
# Prompt
# ============================================================================

PROMPT_HANDWRITING = f"""\
这是一页教材 PDF 的渲染图。

图中可能同时包含：
- 印刷体教材原文
- 老师手写批注
- 图表、公式、思维导图

你的任务：只转录老师手写的内容，不要转录任何印刷体教材原文。

要求：
1. 只识别老师手写笔迹；
2. 按手写块在页面上的顺序（从上到下）输出；
3. 每块前使用【位置】标注大致区域，例如：
   【左上】、【右侧】、【页脚】、【行间】；
4. 数学公式使用 LaTeX，例如 $E=Pt$；
5. 无法辨认的字使用 [?]；
6. 严禁根据教材内容猜测或补全老师没有写的内容；
7. 如果整页没有任何手写笔迹，只输出：
{NO_NOTE_MARK}
"""


PROMPT_PAGE_RECOVERY = r"""\
这是一页中文教材 PDF 的完整渲染图。

该 PDF 的原始文本层可能存在中文字体编码乱码，因此不能依赖文本层。
请直接根据图像恢复本页的“印刷体教材内容”和“老师手写批注”。

你的任务非常重要：请区分“印刷体”和“手写体”。

请严格按照下面 JSON 格式输出，不要输出 Markdown 代码块，不要输出任何额外解释：

{
  "content_markdown": "本页印刷体教材内容转换后的 Markdown",
  "handwriting": "本页老师手写批注；没有则填写 NO_HANDWRITING"
}

识别要求：

【content_markdown】
1. 只包含印刷体教材原始内容；
2. 不要包含老师手写批注；
3. 保留标题、正文、列表、公式；
4. 数学公式使用 LaTeX；
5. 如果页面是思维导图、知识树、流程图、结构图：
   - 必须识别父节点与子节点关系；
   - 使用 Markdown 标题和嵌套列表表达层级；
   - 不要简单按照视觉位置罗列文字；
   - 不要凭空补充图中不存在的知识；
6. 表格尽量转换为 Markdown 表格；
7. 无法识别的印刷文字使用 [?]，严禁猜测。

【handwriting】
1. 只包含老师手写内容；
2. 每块手写内容前标注大致位置，例如【左上】、【右侧】、【行间】；
3. 数学公式使用 LaTeX；
4. 无法辨认使用 [?]；
5. 如果没有任何手写内容，严格填写：
NO_HANDWRITING

重要原则：
- 不要根据常识补全教材内容；
- 不要把印刷体误认为手写；
- 不要把手写误认为印刷体；
- 必须尽可能保持原图的信息结构。
"""

PROMPT_FORMULA_PAGE = r"""\
这是一页中文数学/物理教材的页面截图。

PDF 文本层已经提取了正文，但其中的数学公式可能丢失、残缺或被错误编码。
请只识别本页中“印刷体教材”的数学公式，不识别老师手写内容。

请严格输出 JSON，不要输出 Markdown 代码块：
{
  "formulas": [
    {"location": "公式大致位置", "latex": "LaTeX公式"}
  ]
}

要求：
1. 从上到下列出本页所有明显的独立数学/物理公式；
2. 普通正文中的“=”不要当成公式；
3. 表格中的普通数字、页码、题号不要当成公式；
4. 保留上下标、分式、根号、平方、括号和运算符；
5. 公式编号①②③不要放进 latex；
6. 如果公式分多行，可以使用 aligned；
7. 不要根据教材常识补写图片中没有出现的公式；
8. 如果没有明显公式，返回 {"formulas": []}。
"""

PROMPT_FORMULA_AND_HANDWRITING = r"""\
这是一本中文数学/物理教材的一页截图。

请同时完成两项任务：
A. 识别本页印刷体教材中明显的数学/物理公式；
B. 识别老师手写批注。

请严格输出 JSON，不要输出 Markdown 代码块：
{
  "formulas": [
    {"location": "公式大致位置", "latex": "LaTeX公式"}
  ],
  "handwriting": "手写内容"
}

【公式】
1. 只识别印刷体独立公式，不要把正文中的普通等号当公式；
2. 从上到下输出；
3. 保留上下标、分式、根号、平方、括号和运算符；
4. 公式编号不要放入 latex；
5. 不要根据常识补写图片中没有出现的公式；
6. 没有公式时 formulas 返回空数组。

【手写】
1. 只识别老师手写内容；
2. 每块前标注大致位置，例如【左上】【右侧】【行间】【页脚】；
3. 数学公式使用 LaTeX；
4. 无法辨认使用 [?]；
5. 没有手写时填写 NO_HANDWRITING。
"""


# ============================================================================
# 基础工具
# ============================================================================

def _sha1_short(value: str) -> str:
    """生成短 hash，用于缓存版本隔离。"""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _safe_json_loads(text: str) -> dict[str, Any] | None:
    """
    尝试解析 VLM 返回的 JSON。

    某些模型可能仍会返回：
    ```json
    {...}
    ```
    所以这里做兼容。
    """
    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # 尝试截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")

    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    return None


# ============================================================================
# ① pymupdf4llm 提取教材正文 + page_boxes
# ============================================================================

def extract_pages(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """保留 page_boxes / words，尤其利用 page_boxes 的 formula bbox + pos。"""
    chunks = cast(
        list[dict[str, Any]],
        pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            extract_words=True,
            write_images=False,
            force_text=True,
        ),
    )
    if len(chunks) < doc.page_count:
        chunks.extend({"text": "", "page_boxes": [], "words": []}
                      for _ in range(doc.page_count - len(chunks)))
    return chunks[:doc.page_count]


def extract_text_pages(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("text", "")) for c in chunks]


# ============================================================================
# ② 页面渲染
# ============================================================================

def render_page_b64(page: pymupdf.Page) -> str:
    """
    渲染页面为图片并返回 base64。

    长边超过 MAX_LONG_EDGE 时自动降低 zoom，
    防止超大图片导致 VLM 推理明显变慢。
    """

    rect = page.rect

    zoom_limit = MAX_LONG_EDGE * 72 / max(rect.width, rect.height)
    zoom = min(RENDER_ZOOM, zoom_limit)

    pix = page.get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom),
        alpha=False,
    )

    if IMAGE_FORMAT.lower() in {"jpg", "jpeg"}:
        image_bytes = pix.tobytes(
            "jpeg",
            jpg_quality=JPEG_QUALITY,
        )
    else:
        image_bytes = pix.tobytes("png")

    return base64.b64encode(image_bytes).decode("ascii")


def image_data_url(img_b64: str) -> str:
    """根据配置生成 data URL。"""
    mime = "image/jpeg" if IMAGE_FORMAT.lower() in {"jpg", "jpeg"} else "image/png"
    return f"data:{mime};base64,{img_b64}"


# ============================================================================
# ③ PDF 文本质量检测
# ============================================================================

def text_quality_stats(text: str) -> dict[str, float | int]:
    """
    统计文本质量指标。

    注意：
    这里只用于判断 PDF 文本层是否可信，
    不评价文本语义正确性。
    """

    if not text:
        return {
            "total": 0,
            "effective": 0,
            "chinese": 0,
            "ascii_alpha": 0,
            "chinese_ratio": 0.0,
            "ascii_ratio": 0.0,
        }

    effective_chars = [
        ch
        for ch in text
        if not ch.isspace()
    ]

    effective = len(effective_chars)

    chinese = sum(
        1
        for ch in effective_chars
        if "\u4e00" <= ch <= "\u9fff"
    )

    ascii_alpha = sum(
        1
        for ch in effective_chars
        if ("A" <= ch <= "Z") or ("a" <= ch <= "z")
    )

    return {
        "total": len(text),
        "effective": effective,
        "chinese": chinese,
        "ascii_alpha": ascii_alpha,
        "chinese_ratio": chinese / effective if effective else 0.0,
        "ascii_ratio": ascii_alpha / effective if effective else 0.0,
    }


def is_text_garbled(text: str) -> tuple[bool, str]:
    """
    判断 PDF 文本层是否可能乱码。

    返回：
        (是否乱码, 原因)
    """

    stats = text_quality_stats(text)

    effective = int(stats["effective"])
    chinese_ratio = float(stats["chinese_ratio"])
    ascii_ratio = float(stats["ascii_ratio"])

    if effective < MIN_EFFECTIVE_TEXT_CHARS:
        return True, f"有效文本过少({effective})"

    # 典型中文 PDF 乱码：
    # 视觉上是中文，但 extract_text 得到大量无意义 ASCII。
    if (
        chinese_ratio < MIN_CHINESE_RATIO
        and ascii_ratio > MIN_ASCII_RATIO_FOR_GARBLED
    ):
        return (
            True,
            (
                "中文比例过低"
                f"({chinese_ratio:.1%})，"
                "ASCII 字母比例过高"
                f"({ascii_ratio:.1%})"
            ),
        )

    return False, "文本层正常"


# ============================================================================
# ④ 公式区域检测
# ============================================================================

def _box_bbox(box: dict[str, Any]) -> pymupdf.Rect | None:
    bbox = box.get("bbox")
    if not bbox:
        return None
    try:
        rect = pymupdf.Rect(bbox)
        return None if rect.is_empty or rect.is_infinite else rect
    except Exception:
        return None


def _box_pos(box: dict[str, Any]) -> tuple[int, int] | None:
    pos = box.get("pos")
    if not pos or len(pos) != 2:
        return None
    try:
        return int(pos[0]), int(pos[1])
    except Exception:
        return None


def detect_formula_boxes(page_chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """主方案：直接使用 pymupdf4llm page_boxes 中的 formula。"""
    text = str(page_chunk.get("text", ""))
    result = []
    for box in page_chunk.get("page_boxes", []):
        if not isinstance(box, dict):
            continue
        if str(box.get("class", "")).lower() != "formula":
            continue
        bbox = _box_bbox(box)
        pos = _box_pos(box)
        if bbox is None or pos is None:
            continue
        start, stop = pos
        if not (0 <= start <= stop <= len(text)):
            continue
        result.append({"bbox": bbox, "start": start, "stop": stop,
                       "original": text[start:stop], "source": "page_box"})
    return sorted(result, key=lambda x: x["start"])


def _formula_signal_count(text: str) -> int:
    signals = 0
    if re.search(r"[=≈≠≤≥]", text):
        signals += 1
    if re.search(r"[+\-×÷*/·^²³√]", text):
        signals += 1
    if re.search(r"[A-Za-z][0-9]", text):
        signals += 1
    if re.search(r"[PUWIRQtEFm]", text):
        signals += 1
    return signals


def _extract_text_lines(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    result = []
    try:
        data = page.get_text("dict")
    except Exception:
        return result
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(s.get("text", "")) for s in spans).strip()
            bbox = line.get("bbox")
            if not text or not bbox:
                continue
            try:
                rect = pymupdf.Rect(bbox)
            except Exception:
                continue
            if not rect.is_empty:
                result.append((rect, text))
    return result


def detect_formula_candidates(page: pymupdf.Page) -> list[dict[str, Any]]:
    """fallback：当前 pymupdf4llm 没有 formula box 时检测公式行。"""
    if not ENABLE_FORMULA_HEURISTIC_FALLBACK:
        return []
    candidates = []
    for rect, text in _extract_text_lines(page):
        if len(text) <= 180 and _formula_signal_count(text) >= 1:
            candidates.append((rect, text))

    merged = []
    for rect, text in candidates:
        if not merged:
            merged.append((rect, text))
            continue
        prev_rect, prev_text = merged[-1]
        gap = rect.y0 - prev_rect.y1
        overlap_x = not (rect.x1 < prev_rect.x0 or rect.x0 > prev_rect.x1)
        if gap <= FORMULA_MERGE_GAP and overlap_x and rect.y1 - prev_rect.y0 <= MAX_FORMULA_CANDIDATE_HEIGHT:
            merged[-1] = (prev_rect | rect, prev_text + "\n" + text)
        else:
            merged.append((rect, text))

    return [{"bbox": rect, "start": None, "stop": None,
             "original": text, "source": "heuristic"}
            for rect, text in merged]


def is_complex_visual_page(page: pymupdf.Page, text: str, formula_boxes: list[dict[str, Any]]) -> tuple[bool, str]:
    if not ENABLE_VISUAL_STRUCTURE_DETECTION:
        return False, "复杂页面检测已关闭"
    try:
        drawing_count = len(page.get_drawings())
        text_block_count = len(page.get_text("blocks"))
        if (drawing_count >= MIN_DRAWINGS_FOR_VISUAL_PAGE
                and text_block_count <= MAX_TEXT_BLOCKS_FOR_VISUAL_PAGE
                and not formula_boxes):
            return True, (f"图形对象较多({drawing_count})，文本块({text_block_count})，疑似二维结构页")
    except Exception as exc:
        return False, f"复杂页面检测异常: {exc}"
    return False, "普通页面"


# ============================================================================
# ⑤ 手写矢量预筛
# ============================================================================

def _is_straight(pts: list[tuple[float, float]], tol: float = 0.5) -> bool:
    """点列是否近似共线。"""

    if len(pts) < 3:
        return True

    (x0, y0), (x1, y1) = pts[0], pts[-1]

    dx = x1 - x0
    dy = y1 - y0

    length = math.hypot(dx, dy)

    if length < 1e-6:
        return True

    for x, y in pts[1:-1]:
        distance = abs(
            (x - x0) * dy - (y - y0) * dx
        ) / length

        if distance > tol:
            return False

    return True


def count_stroke_paths(page: pymupdf.Page) -> int:
    """
    统计疑似手写笔迹的矢量路径数。

    判定：
    - 贝塞尔曲线 → 疑似笔迹
    - 多段线且明显弯折 → 疑似笔迹

    过滤：
    - 普通直线
    - 表格线
    - 边框
    """

    n = 0

    for drawing in page.get_drawings():

        items = drawing.get("items", [])

        if not items:
            continue

        types = {item[0] for item in items}

        # 贝塞尔曲线
        if "c" in types:
            n += 1
            continue

        pts: list[tuple[float, float]] = []

        for item in items:
            if item[0] == "l":
                pts.append(tuple(item[1]))
                pts.append(tuple(item[2]))

        if len(pts) >= 3 and not _is_straight(pts):
            n += 1

    return n


def may_have_handwriting(page: pymupdf.Page) -> tuple[bool, int]:
    """
    判断页面是否可能存在矢量手写笔记。

    返回：
        (是否可能存在, 疑似路径数量)
    """

    count = count_stroke_paths(page)

    return count >= MIN_STROKE_PATHS, count


# ============================================================================
# ⑥ LLM 调用
# ============================================================================

def _chat(
    client: OpenAI,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = MAX_TOKENS_DEFAULT,
) -> str:
    """统一 LLM 调用入口。"""

    last_err: Exception | None = None

    for attempt in range(LLM_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                timeout=LLM_TIMEOUT,
                max_tokens=max_tokens,
                extra_body={
                    "think": False,
                },
            )

            text = resp.choices[0].message.content or ""

            # 兜底去掉模型可能残留的 think。
            text = re.sub(
                r"<think.*?>.*?</think\s*>",
                "",
                text,
                flags=re.S,
            )

            return text.strip()

        except Exception as exc:  # noqa: BLE001
            last_err = exc

            if attempt < LLM_RETRIES:
                print(
                    f"    ! LLM 调用失败，"
                    f"{attempt + 1}/{LLM_RETRIES + 1} 次重试: {exc}"
                )
                time.sleep(2)

    raise RuntimeError(f"LLM 调用最终失败: {last_err}")


# ============================================================================
# ⑦ VLM 公式识别
# ============================================================================

def render_formula_b64(page: pymupdf.Page, bbox: pymupdf.Rect) -> str:
    rect = pymupdf.Rect(bbox)
    rect.x0 = max(page.rect.x0, rect.x0 - FORMULA_PADDING)
    rect.y0 = max(page.rect.y0, rect.y0 - FORMULA_PADDING)
    rect.x1 = min(page.rect.x1, rect.x1 + FORMULA_PADDING)
    rect.y1 = min(page.rect.y1, rect.y1 + FORMULA_PADDING)
    long_edge = max(rect.width, rect.height)
    zoom_limit = FORMULA_MAX_LONG_EDGE * 72 / long_edge if long_edge > 0 else FORMULA_RENDER_ZOOM
    zoom = min(FORMULA_RENDER_ZOOM, zoom_limit)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False)
    image_bytes = pix.tobytes("png")
    return base64.b64encode(image_bytes).decode("ascii")


def clean_latex(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:latex|tex)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    if text.startswith("$$") and text.endswith("$$"):
        text = text[2:-2].strip()
    if text.startswith(r"\[") and text.endswith(r"\]"):
        text = text[2:-2].strip()
    return text.strip()


def _parse_formula_json(raw: str) -> list[dict[str, str]]:
    data = _safe_json_loads(raw)
    if not data:
        return []
    formulas = data.get("formulas", [])
    if not isinstance(formulas, list):
        return []
    result = []
    for item in formulas:
        if not isinstance(item, dict):
            continue
        latex = clean_latex(str(item.get("latex", "")))
        location = str(item.get("location", "")).strip()
        if latex:
            result.append({"location": location, "latex": latex})
    return result


def recognize_formula_page(client: OpenAI, img_b64: str) -> list[dict[str, str]]:
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT_FORMULA_PAGE},
        {"type": "image_url", "image_url": {"url": image_data_url(img_b64)}},
    ]}]
    raw = _chat(client, messages, max_tokens=MAX_TOKENS_FORMULA_PAGE)
    return _parse_formula_json(raw)


def recognize_formula_and_handwriting(client: OpenAI, img_b64: str) -> tuple[list[dict[str, str]], str]:
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT_FORMULA_AND_HANDWRITING},
        {"type": "image_url", "image_url": {"url": image_data_url(img_b64)}},
    ]}]
    raw = _chat(client, messages, max_tokens=MAX_TOKENS_FORMULA_PAGE)
    data = _safe_json_loads(raw) or {}
    formulas = _parse_formula_json(raw)
    notes = str(data.get("handwriting", "")).strip()
    if notes == NO_NOTE_MARK:
        notes = ""
    return formulas, notes


def formula_markdown(latex: str) -> str:
    latex = clean_latex(latex)
    return f"\n\n$$\n{latex}\n$$\n" if latex else ""


def extract_formulas_one_call(
    *,
    page: pymupdf.Page,
    page_index: int,
    client: OpenAI,
    cache: Path,
    cache_prefix: str,
) -> list[dict[str, str]]:
    """每页最多一次公式 VLM；不再对每个公式单独调用模型。"""
    cache_name = f"{cache_prefix}_page_{page_index}_formulas.json"
    cached = _cache_get(cache, cache_name)
    if cached is not None:
        try:
            data = json.loads(cached)
            if isinstance(data, list):
                print("    公式识别：使用缓存")
                return data
        except json.JSONDecodeError:
            pass

    start = time.perf_counter()
    formulas = recognize_formula_page(client, render_page_b64(page))
    elapsed = time.perf_counter() - start
    print(f"    公式整页 VLM：{elapsed:.1f}s，识别 {len(formulas)} 个")

    _cache_put(cache, cache_name, json.dumps(formulas, ensure_ascii=False, indent=2))
    return formulas


# ============================================================================
# ⑧ VLM 手写识别
# ============================================================================

def recognize_handwriting(
    client: OpenAI,
    img_b64: str,
) -> str:
    """只识别老师手写笔记。"""

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": PROMPT_HANDWRITING,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url(img_b64),
                    },
                },
            ],
        }
    ]

    return _chat(client, messages, max_tokens=MAX_TOKENS_HANDWRITING)


# ============================================================================
# ⑧ VLM 页面恢复
# ============================================================================

# ============================================================================
# ⑨ Markdown 合并
# ============================================================================

def format_notes(notes: str) -> str:
    """
    格式化手写笔记。

    使用普通 Markdown 标题，不使用 blockquote，
    避免复杂公式 / 列表在引用块中渲染异常。
    """

    notes = notes.strip()

    if not notes:
        return ""

    return f"""\
### 📝 老师手写笔记

{notes}
"""


def merge_markdown(
    texts: list[str],
    notes_by_page: dict[int, str],
    targets: list[int],
) -> str:
    """按页面顺序合并 Markdown。"""

    parts: list[str] = []

    for i in targets:

        content = texts[i].strip()

        page_parts = [
            f"<!-- 第 {i + 1} 页 -->",
            "",
            f"## 第 {i + 1} 页",
        ]

        if content:
            page_parts.extend([
                "",
                "### 教材内容",
                "",
                content,
            ])
        else:
            page_parts.extend([
                "",
                "### 教材内容",
                "",
                "<!-- 本页未提取到教材正文 -->",
            ])

        notes = notes_by_page.get(i, "").strip()

        if notes:
            page_parts.extend([
                "",
                format_notes(notes),
            ])

        parts.append("\n".join(page_parts).rstrip())

    return "\n\n---\n\n".join(parts) + "\n"


# ============================================================================
# ⑩ 缓存
# ============================================================================

def _cache_get(cache: Path, name: str) -> str | None:
    path = cache / name

    if not path.exists():
        return None

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _cache_put(
    cache: Path,
    name: str,
    content: str,
) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    (cache / name).write_text(content, encoding="utf-8")


def make_cache_prefix(pdf_path: str) -> str:
    """
    生成当前 PDF + 模型 + Prompt 的缓存前缀。

    这样：
    - 换 PDF
    - 换模型
    - 换 Prompt
    - 换 CACHE_VERSION

    都不会误用旧缓存。
    """

    path = Path(pdf_path)

    try:
        stat = path.stat()

        source = (
            f"{path.resolve()}|"
            f"{stat.st_size}|"
            f"{stat.st_mtime_ns}|"
            f"{MODEL_NAME}|"
            f"{CACHE_VERSION}|"
            f"{_sha1_short(PROMPT_HANDWRITING)}|"
            f"{_sha1_short(PROMPT_PAGE_RECOVERY)}"
        )
    except OSError:
        source = (
            f"{pdf_path}|"
            f"{MODEL_NAME}|"
            f"{CACHE_VERSION}"
        )

    return _sha1_short(source)


# ============================================================================
# ⑪ OpenAI 客户端
# ============================================================================

def _make_client() -> OpenAI:
    """
    构造 OpenAI 客户端。

    显式 trust_env=False：
    防止 localhost 请求因为系统代理被转发，
    导致本地 Ollama 网关出现 502。
    """

    http_client = httpx2.Client(
        trust_env=False,
        timeout=LLM_TIMEOUT,
    )

    return OpenAI(
        base_url=BASE_URL,
        api_key="***",
        http_client=http_client,
    )


# ============================================================================
# ⑫ 单页处理
# ============================================================================

def process_page(
    *,
    doc: pymupdf.Document,
    page_index: int,
    page_chunk: dict[str, Any],
    client: OpenAI,
    cache: Path,
    cache_prefix: str,
) -> tuple[str, str, str]:
    """高性能页面路由：一页最多一次 VLM，普通页零 VLM。"""
    page = doc.load_page(page_index)
    text = str(page_chunk.get("text", "")).strip()

    # 1. PDF 文本层乱码：一次整页视觉恢复。
    garbled, reason = is_text_garbled(text)
    if garbled:
        print(f"    文本层异常：{reason}")
        content, notes = process_visual_recovery(
            page=page, page_index=page_index, client=client,
            cache=cache, cache_prefix=cache_prefix,
        )
        return content, notes, "recovery"

    # 2. 只使用真正的 page_boxes formula；默认关闭危险的 heuristic fallback。
    formula_boxes = detect_formula_boxes(page_chunk)

    # 如果当前 pymupdf4llm 真的提供了 formula page_box，直接认为本页有公式。
    formula_text_hint = bool(formula_boxes) or detect_formula_text_hint(page, text)

    # 3. 复杂二维页面优先整页恢复。
    complex_page, reason = is_complex_visual_page(page, text, formula_boxes)
    if complex_page:
        print(f"    复杂结构页：{reason}")
        content, notes = process_visual_recovery(
            page=page, page_index=page_index, client=client,
            cache=cache, cache_prefix=cache_prefix,
        )
        return content, notes, "recovery"

    # 4. 矢量路径只是“可能手写”，不要因此额外调用一次 VLM。
    has_handwriting, stroke_count = may_have_handwriting(page)

    # 5. 当前版本通常没有 formula page_box。
    #    因此用一个便宜的“页面是否值得做公式 VLM”判断，避免每页调用。
    if has_handwriting and formula_text_hint:
        print(
            f"    疑似手写路径={stroke_count} + 公式特征；"
            "合并为一次 VLM"
        )
        cache_name = f"{cache_prefix}_page_{page_index}_formula_handwriting.json"
        cached = _cache_get(cache, cache_name)
        if cached is not None:
            try:
                data = json.loads(cached)
                formulas = data.get("formulas", [])
                notes = str(data.get("handwriting", "")).strip()
                print("    公式+手写：使用缓存")
            except Exception:
                formulas, notes = [], ""
        else:
            start = time.perf_counter()
            formulas, notes = recognize_formula_and_handwriting(
                client, render_page_b64(page)
            )
            elapsed = time.perf_counter() - start
            print(
                f"    公式+手写 VLM：{elapsed:.1f}s，"
                f"公式 {len(formulas)} 个"
            )
            _cache_put(
                cache, cache_name,
                json.dumps(
                    {"formulas": formulas, "handwriting": notes},
                    ensure_ascii=False, indent=2,
                ),
            )

        if notes == NO_NOTE_MARK:
            notes = ""
        text = append_formula_supplement(text, formulas)
        return text, notes, "formula+handwriting"

    if has_handwriting:
        print(f"    检测到疑似手写：路径={stroke_count}，调用手写 VLM")
        cache_name = f"{cache_prefix}_page_{page_index}_handwriting.md"
        notes = _cache_get(cache, cache_name)
        if notes is None:
            start = time.perf_counter()
            notes = recognize_handwriting(client, render_page_b64(page))
            print(f"    手写 VLM 完成：{time.perf_counter() - start:.1f}s")
            _cache_put(cache, cache_name, notes)
        else:
            print("    手写识别：使用缓存")
        if NO_NOTE_MARK in notes:
            notes = ""
        return text, notes, "handwriting"

    if formula_text_hint:
        print("    检测到公式特征：整页一次 VLM，而不是逐公式调用")
        formulas = extract_formulas_one_call(
            page=page, page_index=page_index, client=client,
            cache=cache, cache_prefix=cache_prefix,
        )
        text = append_formula_supplement(text, formulas)
        return text, "", "formula" if formulas else "normal"

    print(f"    疑似笔迹路径={stroke_count}；无明显公式特征；0 次 VLM")
    return text, "", "normal"


def detect_formula_text_hint(page: pymupdf.Page, text: str) -> bool:
    """非常保守的公式页筛选：只决定‘要不要调用一次 VLM’，不直接认公式。"""
    # 先看 pymupdf4llm 是否已经发现 formula box。
    # 使用原始 text 行，但必须同时出现强数学特征，避免普通正文误触发。
    for _, line in _extract_text_lines(page):
        line = line.strip()
        if len(line) > 120:
            continue
        has_equal = bool(re.search(r"[=≈≠≤≥]", line))
        has_math = bool(re.search(r"[+×÷·^²³√]", line))
        has_variable_number = bool(re.search(r"[A-Za-z][0-9]", line))
        # 例如 P1 + P2、U总 = U1 + U2、W=UIt。
        if has_equal and (has_math or has_variable_number):
            return True
    return False


def append_formula_supplement(text: str, formulas: list[dict[str, str]]) -> str:
    if not formulas:
        return text
    parts = []
    for item in formulas:
        latex = clean_latex(str(item.get("latex", "")))
        location = str(item.get("location", "")).strip()
        if not latex:
            continue
        prefix = f"<!-- {location} -->\n" if location else ""
        parts.append(prefix + formula_markdown(latex))
    if not parts:
        return text
    return text.rstrip() + "\n\n### 公式补充\n" + "\n".join(parts) + "\n"


def process_visual_recovery(
    *,
    page: pymupdf.Page,
    page_index: int,
    client: OpenAI,
    cache: Path,
    cache_prefix: str,
) -> tuple[str, str]:
    """
    页面视觉恢复。

    使用一次 VLM 同时恢复：
    - 印刷教材内容
    - 手写笔记
    """

    cache_name = (
        f"{cache_prefix}_"
        f"page_{page_index}_"
        f"recovery.json"
    )

    cached = _cache_get(cache, cache_name)

    if cached is not None:
        print("    页面视觉恢复：使用缓存")

        data = _safe_json_loads(cached)

        if data is not None:
            content = str(
                data.get("content_markdown", "")
            ).strip()

            notes = str(
                data.get("handwriting", "")
            ).strip()

            if notes == NO_NOTE_MARK:
                notes = ""

            return content, notes

        # 理论上不应发生；旧缓存格式异常时重新识别。
        print("    ! 缓存 JSON 异常，重新识别")

    start = time.perf_counter()

    img_b64 = render_page_b64(page)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": PROMPT_PAGE_RECOVERY,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_data_url(img_b64),
                    },
                },
            ],
        }
    ]

    raw = _chat(client, messages, max_tokens=MAX_TOKENS_RECOVERY)

    elapsed = time.perf_counter() - start

    print(
        f"    页面视觉恢复完成："
        f"{elapsed:.1f}s"
    )

    data = _safe_json_loads(raw)

    if data is None:
        # 缓存原始输出，方便人工排查。
        _cache_put(
            cache,
            cache_name,
            raw,
        )

        print(
            "    ! VLM 未返回合法 JSON，"
            "原始输出暂作为教材内容"
        )

        return raw.strip(), ""

    # 统一写入 JSON，保证缓存格式稳定。
    normalized = {
        "content_markdown": str(
            data.get("content_markdown", "")
        ).strip(),
        "handwriting": str(
            data.get("handwriting", "")
        ).strip(),
    }

    _cache_put(
        cache,
        cache_name,
        json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
        ),
    )

    notes = normalized["handwriting"]

    if notes == NO_NOTE_MARK:
        notes = ""

    return normalized["content_markdown"], notes


# ============================================================================
# ⑬ 主流程
# ============================================================================

def run(
    pdf_path: str,
    output_path: str,
    pages: list[int] | None,
) -> None:

    total_start = time.perf_counter()

    client = _make_client()
    cache = Path(CACHE_DIR)

    doc = pymupdf.open(pdf_path)

    try:
        total = doc.page_count

        if pages:
            targets = [
                p
                for p in pages
                if 0 <= p < total
            ]
        else:
            targets = list(range(total))

        if not targets:
            raise ValueError("没有有效页码")

        print("=" * 72)
        print("PDF → Markdown 智能提取")
        print("=" * 72)
        print(f"PDF: {pdf_path}")
        print(f"总页数: {total}")
        print(
            "处理页码: "
            f"{[p + 1 for p in targets]}"
        )
        print(f"模型: {MODEL_NAME}")
        print(f"缓存版本: {CACHE_VERSION}")
        print("=" * 72)

        # -------------------------------------------------------------------
        # 第一阶段：高速提取整个 PDF 文本层
        # -------------------------------------------------------------------

        print("\n[阶段 1/2] pymupdf4llm 提取 PDF 文本层...")

        extract_start = time.perf_counter()

        page_chunks = extract_pages(doc)
        texts = extract_text_pages(page_chunks)

        extract_elapsed = (
            time.perf_counter() - extract_start
        )

        print(
            f"文本层提取完成："
            f"{extract_elapsed:.2f}s"
        )

        # -------------------------------------------------------------------
        # 第二阶段：逐页智能处理
        # -------------------------------------------------------------------

        print("\n[阶段 2/2] 智能页面处理...")

        notes_by_page: dict[int, str] = {}
        recovered_texts = texts.copy()

        cache_prefix = make_cache_prefix(pdf_path)

        stats = {
            "normal": 0,
            "formula": 0,
            "recovery": 0,
            "handwriting": 0,
            "formula_handwriting": 0,
            "failed": 0,
        }

        for index, i in enumerate(targets, start=1):

            page_start = time.perf_counter()

            print(
                f"\n[{index}/{len(targets)}] "
                f"第 {i + 1} 页"
            )

            try:
                original = recovered_texts[i]

                content, notes, route = process_page(
                    doc=doc,
                    page_index=i,
                    page_chunk=page_chunks[i],
                    client=client,
                    cache=cache,
                    cache_prefix=cache_prefix,
                )

                # 路由统计直接使用 process_page 的判定结果，
                # 避免 get_drawings() 等检测在此处被重复计算。
                if route == "recovery":
                    stats["recovery"] += 1
                elif route == "formula":
                    stats["formula"] += 1
                elif route == "handwriting":
                    stats["handwriting"] += 1
                elif route == "formula+handwriting":
                    stats["formula_handwriting"] += 1
                else:
                    stats["normal"] += 1

                recovered_texts[i] = content

                if notes:
                    notes_by_page[i] = notes

                    print(
                        f"    ✔ 提取到手写笔记 "
                        f"{len(notes)} 字"
                    )

                elapsed = (
                    time.perf_counter() - page_start
                )

                print(
                    f"    第 {i + 1} 页完成："
                    f"{elapsed:.2f}s"
                )

            except Exception as exc:  # noqa: BLE001

                stats["failed"] += 1

                recovered_texts[i] = (
                    f"<!-- 第 {i + 1} 页提取失败: {exc} -->"
                )

                print(f"    ✘ 第 {i + 1} 页失败: {exc}")

        # -------------------------------------------------------------------
        # 输出
        # -------------------------------------------------------------------

        out = Path(output_path)
        out.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        markdown = merge_markdown(
            recovered_texts,
            notes_by_page,
            targets,
        )

        out.write_text(
            markdown,
            encoding="utf-8",
        )

        total_elapsed = (
            time.perf_counter() - total_start
        )

        print("\n" + "=" * 72)
        print("处理完成")
        print("=" * 72)
        print(f"正常文本页: {stats['normal']}")
        print(f"公式处理页: {stats['formula']}")
        print(f"公式+手写页: {stats['formula_handwriting']}")
        print(f"视觉恢复页: {stats['recovery']}")
        print(f"手写 VLM 页: {stats['handwriting']}")
        print(f"失败页: {stats['failed']}")
        print(
            f"总耗时: "
            f"{total_elapsed:.1f}s"
        )
        print(
            f"输出文件: "
            f"{out.resolve()}"
        )
        print("=" * 72)

    finally:
        doc.close()


# ============================================================================
# CLI
# ============================================================================

def parse_pages(value: str) -> list[int] | None:
    """
    解析：
        --pages 7
        --pages 1,2,3
        --pages 1-5
        --pages 1,3-5,8

    返回 0-based 页码。
    """

    value = value.strip()

    if not value:
        return None

    pages: set[int] = set()

    for item in value.split(","):

        item = item.strip()

        if not item:
            continue

        if "-" in item:
            start_str, end_str = item.split("-", 1)

            start = int(start_str.strip())
            end = int(end_str.strip())

            if start <= 0 or end <= 0:
                raise ValueError("页码必须从 1 开始")

            if start > end:
                start, end = end, start

            for p in range(start, end + 1):
                pages.add(p - 1)

        else:
            p = int(item)

            if p <= 0:
                raise ValueError("页码必须从 1 开始")

            pages.add(p - 1)

    return sorted(pages) or None


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "教材 PDF → Markdown 智能提取 "
            "（文本层 + 乱码恢复 + 思维导图 + 手写批注）"
        )
    )

    parser.add_argument(
        "--pdf",
        default=PDF_PATH,
        help="PDF 路径",
    )

    parser.add_argument(
        "--out",
        default=OUTPUT_PATH,
        help="输出 Markdown 路径",
    )

    parser.add_argument(
        "--pages",
        default="",
        help=(
            "仅处理指定页（1-based），"
            "支持 7、1,2,3、1-5、1,3-5"
        ),
    )

    args = parser.parse_args()

    pages = parse_pages(args.pages)

    run(
        pdf_path=args.pdf,
        output_path=args.out,
        pages=pages,
    )


if __name__ == "__main__":
    main()
