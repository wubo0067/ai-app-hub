#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 页面 -> Markdown 提取（初中物理 / 化学 / 数学多科通用）。

实现思路与 knowledge_extract/extract_pdf/main.py 一致，但模型统一通过
config.py 创建（不再直连 openai）：
- 通用结构化提取 PROMPT：对物理、化学、数学页面一视同仁，完整保留
  印刷文字、公式（统一 LaTeX）、表格、图形标签、手写批注；
- 视觉模型经 config.get_llm(provider="vision", is_vision=True) 获取，
  base_url / api_key / model_name 在 sida-agent/.env（VISION_*）中配置；
- 用 PyMuPDF 将每页渲染成 PNG（仅内存，不落盘）交给视觉大模型按提示词提取；
- 每页结果独立落盘 output/pdf_extract/{pdf_id}/p{页码}.md，
  存在且非空即视为已提取，不再调用模型（断点续跑，中断不丢数据）；
- pdf_id = PDF 文件内容的哈希前 16 位，与 extract_pdf 同算法，
  因此两个项目可共用同一份提取缓存目录。

对外接口保持：
    extract_pdf_pages_as_markdown(pdf_path, start_page, end_page,
                                  output_dir=None)
    -> List[Dict]： [{"page": 页码, "content": Markdown 文本}, ...]
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path

import pymupdf  # PyMuPDF
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from config import get_llm, resolve_llm_config
from logger import get_logger

BASE_DIR = Path(__file__).resolve().parent
log = get_logger()

# ---- 输出路径 -----------------------------------------------------------
# 与 extract_pdf/main.py 同结构的缓存：output/pdf_extract/{pdf_id}/p{页码}.md
OUTPUT_DIR = BASE_DIR / "output" / "pdf_extract"

# ---- 视觉解析模型 ---------------------------------------------------------
# 模型服务（base_url / api_key / model_name）统一在 sida-agent/.env 中配置，
# 由 config.py 的 get_llm(provider="vision", is_vision=True) 创建。
VISION_PROVIDER = "vision"

LLM_TIMEOUT = 300.0      # 单次请求超时（秒）
LLM_RETRIES = 2          # 本地调用失败重试次数（指数退避）
MAX_TOKENS = 8192        # 单次生成最大 token 数

MAX_LONG_EDGE = 2000     # PDF 页面渲染 PNG 的长边像素上限
MAX_ZOOM = 3.0           # 页面渲染最大放大倍数（72dpi 为 1.0）

# ---- 图片内容提取提示词（物理 / 化学 / 数学通用，勿改为单学科专用）----
PROMPT = """你是一名专业的 PDF 页面内容结构化提取器。

你的唯一任务是：
将当前页面中的视觉信息完整、准确地转换为结构化 Markdown。

==================================================
一、最高优先级原则
==================================================

1. 完整性优先。
   页面中可见的印刷文字、公式、表格、图形标签、手写批注都必须尽可能保留。

2. 准确性优先。
   严禁根据常识、学科知识、上下文或题目答案猜测图片中不存在或无法确认的内容。

3. 禁止幻觉。
   看不清的文字、数字、公式、上下标：
   使用 [无法辨认]。

   如果存在两种可能：
   使用 [疑似：A / B]。

4. 不得修改原文。
   不要纠正错别字、公式错误、学生错误答案或教师批注。
   图片中是什么，就提取什么。

5. 不要解题。
   不要计算答案，不要补充图片中没有出现的知识点。
   只提取页面实际包含的信息。

==================================================
二、页面结构
==================================================

首先识别页面中的内容区域，并按照逻辑结构组织：

- 标题
- 小节
- 知识点
- 例题
- 题干
- 选项
- 公式
- 表格
- 图片/电路图/装置图/几何图
- 手写批注
- 思维导图
- 页眉页脚

不要简单地按照像素坐标逐行 OCR。

==================================================
三、普通文字
==================================================

完整提取：

- 标题
- 正文
- 题号
- 题干
- 选项
- 注释
- 页眉页脚

尽可能保持原文标点和文字。

不要自行改写或总结。

==================================================
四、数学 / 物理 / 化学公式
==================================================

所有公式必须使用 LaTeX，并区分学科场景正确转写：

- 数学：代数式、方程、不等式、函数、几何量（角度、线段）等；
- 物理：物理量关系式与单位，如 $P=UI$、$Q=I^2Rt$；
- 化学：化学式、化学方程式与上下标，如 $\\mathrm{Na_2CO_3}$、
  $\\mathrm{2H_2 + O_2 \\xrightarrow{点燃} 2H_2O}$（严禁把化学式转成中文）。

行内公式：

$I=U/R$

独立公式：

$$
P=I^2R=\\frac{U^2}{R}
$$

要求：

- 正确识别上下标
- 正确识别分数
- 正确识别希腊字母
- 正确识别单位
- 正确识别括号
- 正确区分数字、字母和变量
- 不要使用 Unicode 数学字符代替 LaTeX

如果无法确认公式：
使用 [无法辨认的公式]
不要根据学科规律补全。

==================================================
五、表格
==================================================

如果页面存在表格：

必须恢复表格的行列关系。

使用 Markdown table：

| 项目 | 串联 | 并联 |
|---|---|---|
| 电压 | ... | ... |
| 电流 | ... | ... |
| 电阻 | ... | ... |

不得把表格简单展开成普通文字。

==================================================
六、思维导图 / 流程图
==================================================

如果页面存在思维导图、树状图或流程图：

优先恢复节点之间的层级和连接关系。

例如：

## 电功
- 电能
  - 来源
  - 利用
  - 单位与换算
- 电能表
  - 作用
  - 参数
- 电功
  - 实质
  - 公式

不要仅按照图片的从上到下、从左到右顺序输出。

==================================================
七、图形 / 电路图 / 实验装置图 / 几何图
==================================================

对于图形：

必须提取：

1. 所有文字标签
2. 所有元件名称
3. 元件之间的连接关系
4. 重要的位置关系
5. 图中的箭头、虚线、辅助线
6. 图中明确标出的数值

物理电路图，必须描述：

- 电源
- 开关
- 电阻
- 灯泡
- 电流表
- 电压表
- 各元件之间的串联/并联关系
- 电表连接位置

化学实验装置图，必须描述：

- 仪器名称（烧杯、酒精灯、集气瓶、导管、铁架台等）
- 装置连接顺序与气路走向
- 液面、药品颜色等可见信息

几何图 / 函数图，必须描述：

- 图形种类（三角形、圆、坐标系、抛物线等）
- 关键点、边、角、辅助线及其标注

不要只输出标签。

不要根据图形推断图中没有明确表达的信息。

==================================================
八、手写批注
==================================================

必须区分：

1. 印刷体
2. 手写内容

如果发现手写内容：

使用：

[红笔手写：...]
[蓝笔手写：...]
[黑笔手写：...]

同时尽可能说明它的位置和关联对象：

[红笔手写，位于例23选项D右侧：
$Q=I^2Rt$]

如果手写内容无法辨认：

[红笔手写：无法辨认]

不要把手写批注与印刷文字混为一体。

==================================================
九、手写批注与原文的关系
==================================================

如果手写内容明确：

- 圈选某个选项
- 划掉某个选项
- 指向某个公式
- 指向某个图形元件
- 在某道题旁边进行计算

必须描述这种关系。

例如：

[红笔手写：圈选 D]
[红笔手写：位于例25选项附近：Q=I²Rt]
[红笔箭头：指向例26中的 R1]

不要推测箭头没有明确指向的对象。

==================================================
十、阅读顺序
==================================================

普通文本：
按照自然阅读顺序组织。

多栏：
先完成左栏，再完成右栏。

表格：
按照表格结构。

思维导图：
按照节点层级。

图形：
按照图形结构。

手写批注：
放在其关联的内容附近。

==================================================
十一、颜色
==================================================

彩色印刷文字仍然属于印刷体。

只有明确属于手写笔迹的内容才标记：

[红笔手写：...]
[蓝笔手写：...]

不要因为文字本身是红色/蓝色就认为它是手写。

==================================================
十二、完整性检查
==================================================

输出前检查：

□ 是否遗漏标题
□ 是否遗漏题号
□ 是否遗漏题干
□ 是否遗漏选项
□ 是否遗漏公式
□ 是否遗漏表格
□ 是否遗漏图形标签
□ 是否遗漏手写批注
□ 是否遗漏手写公式
□ 是否保持表格结构
□ 是否保持思维导图层级
□ 是否描述重要图形连接关系
□ 是否存在自行猜测的内容

如果看不清，必须标记 [无法辨认]，不能猜。

==================================================
十三、输出要求
==================================================

只输出 Markdown。

不要输出：

- 分析过程
- OCR 过程
- 识别置信度说明
- 题目答案
- 额外知识
- 图片中不存在的内容

开始提取当前页面。"""


def _vision_llm() -> ChatOpenAI:
    """构造视觉解析 LLM（经 config.get_llm，配置来自 .env 的 VISION_*）。

    max_retries=0：重试交给 _invoke_llm 的本地指数退避，避免双份重试。
    """
    return get_llm(
        provider=VISION_PROVIDER,
        is_vision=True,
        temperature=0.0,
        max_tokens=MAX_TOKENS,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


def _fix_math(text: str) -> str:
    """修复模型输出中导致 KaTeX 报错的 $ 写法：把成对的 $$ 折叠为单个 $。"""
    return re.sub(r"\$\$", "$", text)


def _invoke_llm(llm: ChatOpenAI, messages: list[HumanMessage]) -> str:
    """调用视觉 LLM 并带指数退避重试，返回文本结果。"""
    last_error: Exception | None = None
    for attempt in range(LLM_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = llm.invoke(messages)
            content = response.content
            text = content.strip() if isinstance(content, str) else str(content or "").strip()
            log.debug(
                "    LLM 响应：耗时 %.1fs，输出 %d 字符（第 %d/%d 次尝试）",
                time.perf_counter() - started, len(text), attempt + 1, LLM_RETRIES + 1,
            )
            return text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.debug(
                "    LLM 异常（第 %d/%d 次，耗时 %.1fs）：%s: %s",
                attempt + 1, LLM_RETRIES + 1, time.perf_counter() - started,
                type(exc).__name__, exc,
            )
            if attempt < LLM_RETRIES:
                wait = 2 * (attempt + 1)
                log.warning("    ! 调用失败，%ds 后重试：%s", wait, exc)
                time.sleep(wait)
    log.error("LLM 调用最终失败：%s", last_error)
    raise RuntimeError(f"LLM 调用最终失败：{last_error}")


def _pdf_id(pdf_path: Path) -> str:
    """PDF 唯一标识：PDF 文件内容 SHA-256 前 16 位（与 extract_pdf 同算法）。"""
    h = hashlib.sha256()
    h.update(pdf_path.read_bytes())
    return h.hexdigest()[:16]


def _render_page(page: pymupdf.Page) -> bytes:
    """把 PDF 单页渲染成 PNG 字节，长边不超过 MAX_LONG_EDGE。"""
    rect = page.rect
    long_edge = max(rect.width, rect.height)
    zoom = min(MAX_ZOOM, MAX_LONG_EDGE / long_edge) if long_edge else 1.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def extract_pdf_pages_as_markdown(
    pdf_path: str,
    start_page: int,
    end_page: int,
    vision_provider: str = "qwen",
    *,
    output_dir: str | Path | None = None,
) -> list[dict]:
    """截取 PDF 页面渲染成 PNG，用视觉大模型提取为结构化 Markdown。

    结果逐页缓存到 output/pdf_extract/{pdf_id}/p{页码}.md：
    已提取（存在且非空）的页直接读缓存，不重复调用模型；
    中断重跑会自动跳过已完成页。缓存目录可通过 output_dir 覆盖
    （例如指向 extract_pdf 项目的 output 目录以复用其既有提取结果）。

    Args:
        pdf_path: PDF 文件路径。
        start_page: 起始页码（从 1 计）。
        end_page: 结束页码（含），超出 PDF 总页数时自动截断。
        vision_provider: 保留参数（语义兼容旧接口，仅用于日志）；实际视觉模型
            由 config.py + sida-agent/.env 的 VISION_* 配置决定。
        output_dir: 可选，覆盖默认缓存目录 output/pdf_extract。

    Returns:
        pages_data: [{"page": 页码, "content": Markdown 文本}, ...]
    """
    # 视觉模型配置（.env -> config），api_key 为空时给出可操作提示
    vision_cfg = resolve_llm_config(VISION_PROVIDER)
    if not (vision_cfg["api_key"] or ""):
        log.error(
            "视觉模型(%s)未配置 API Key：请在 sida-agent/.env 设置 VISION_API_KEY "
            "（或系统环境变量 DASHSCOPE_API_KEY）。", VISION_PROVIDER,
        )
        raise RuntimeError(
            "视觉模型未配置 API Key：请在 sida-agent/.env 设置 VISION_API_KEY，"
            "或配置 VISION_BASE_URL / VISION_MODEL / VISION_API_KEY。"
        )
    log.info("[pdf_processor] 视觉模型 provider=%s: model=%s base_url=%s",
             VISION_PROVIDER, vision_cfg["model_name"], vision_cfg["base_url"])

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 文件不存在：{pdf_path}")

    cache_root = Path(output_dir) if output_dir else OUTPUT_DIR
    cache_root.mkdir(parents=True, exist_ok=True)
    pdf_id = _pdf_id(pdf)
    book_dir = cache_root / pdf_id
    book_dir.mkdir(parents=True, exist_ok=True)

    vision_llm = _vision_llm()
    doc = pymupdf.open(pdf)
    try:
        total = doc.page_count
        start = max(1, start_page)
        end = min(total, end_page)
        if start > end:
            log.error("页范围 %d-%d 无效（PDF 共 %d 页）", start, end, total)
            raise ValueError(f"页范围 {start}-{end} 无效（PDF 共 {total} 页）。")

        log.info("[pdf_processor] PDF=%s（共 %d 页），提取第 %d-%d 页, id=%s",
                 pdf.name, total, start, end, pdf_id)
        pages_data: list[dict] = []
        for page_no in range(start, end + 1):
            page_file = book_dir / f"p{page_no:04d}.md"
            if page_file.exists() and page_file.read_text(encoding="utf-8").strip():
                content = page_file.read_text(encoding="utf-8").strip()
                log.info("  [已提取] 第 %d 页（%s）", page_no, page_file.name)
            else:
                png = _render_page(doc.load_page(page_no - 1))
                messages = [
                    HumanMessage(
                        content=[
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"},
                            },
                        ]
                    )
                ]
                log.info("  [提取] 第 %d 页 ...（缓存：%s）", page_no, page_file)
                content = _invoke_llm(vision_llm, messages)
                content = _fix_math(content)
                if content:
                    page_file.write_text(content, encoding="utf-8")
                else:
                    log.warning("  ! 第 %d 页：模型返回内容为空，未写入缓存", page_no)
            if content:
                pages_data.append({"page": page_no, "content": content})
        return pages_data
    finally:
        doc.close()