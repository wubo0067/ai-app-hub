#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF -> Markdown 提取（印刷体、公式、图形标签、彩色手写批注、思维导图、表格）。

流程：用 PyMuPDF 将每页渲染成 PNG（仅内存，不落盘）交给在线视觉大模型
（OpenAI 兼容接口）按提示词提取。pdf_id = PDF 文件内容的哈希前 16 位
（同一本 PDF 永远对应同一目录，不受提示词/模型更换影响），
每本 PDF 独立输出目录 output/{pdf_id}/：
- name.txt：书名（默认取 PDF 文件名，已存在则不覆盖）；
- config.txt：最近一次使用的提示词+模型配置指纹，配置变化时提醒；
- p{页码}.md：该页提取结果（从 1 计，零填充），存在且非空即视为已提取，
  不再调用模型；每页独立落盘，中断不丢数据。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Iterable

import pymupdf  # PyMuPDF
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from logger import get_logger, parse_level

BASE_DIR = Path(__file__).resolve().parent
log = get_logger()                             # 应用日志（控制台 INFO + 文件 DEBUG）

# ---- 输出路径 -------------------------------------------------------------
OUTPUT_DIR = BASE_DIR / "output"                   # 每本 PDF 一个子目录：output/{pdf_id}/

# ---- 在线视觉模型配置（阿里云百炼 DashScope，OpenAI 兼容接口）------------
#BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
BASE_URL = "https://developer.amd.com.cn/radeon/api/v1"
#MODEL_NAME = "qwen3.8-flash"
MODEL_NAME = "Qwen3.8-Flash-Next"
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")  # 从环境变量读取，避免硬编码密钥

LLM_TIMEOUT = 300.0      # 单次请求超时（秒），在线模型一般几十秒内返回
LLM_RETRIES = 2          # 调用失败重试次数
MAX_TOKENS = 8192        # 单次生成最大 token 数

MAX_LONG_EDGE = 2000     # PDF 页面渲染 PNG 的长边像素上限
MAX_ZOOM = 3.0           # 页面渲染最大放大倍数（72dpi 为 1.0）

# ---- 图片内容提取提示词 ---------------------------------------------------
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
- 图片/电路图/几何图
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
四、数学公式
==================================================

所有数学、物理、化学公式必须使用 LaTeX。

行内公式：

$P=UI$

独立公式：

$$
P=I^2R=\frac{U^2}{R}
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
不要根据物理规律补全。

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
七、图形 / 电路图
==================================================

对于图形：

必须提取：

1. 所有文字标签
2. 所有元件名称
3. 元件之间的连接关系
4. 重要的位置关系
5. 图中的箭头、虚线、辅助线
6. 图中明确标出的数值

对于电路图：

必须描述：

- 电源
- 开关
- 电阻
- 灯泡
- 电流表
- 电压表
- 各元件之间的串联/并联关系
- 电表连接位置

不要只输出标签。

不要根据电路图推断图片中没有明确表达的信息。

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


def _make_client() -> OpenAI:
    """构造 OpenAI 客户端，访问阿里云百炼在线模型。

    使用 SDK 默认 http 客户端（trust_env=True），自动读取系统代理
    （本机 127.0.0.1:7892）访问外网 DashScope 接口。
    """
    return OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=LLM_TIMEOUT)


def _fix_math(text: str) -> str:
    """修复模型输出中导致 KaTeX 报错的 $ 写法。

    典型错误：模型输出 $$...$$ 后紧跟正文时，渲染器按单个 $ 配对定界符，
    数学模式内部混入多余的 $，报
    "Can't use function '$' in math mode"。
    这里统一把成对的 $$ 折叠为单个 $（行内公式），KaTeX 可正常渲染。
    """
    return re.sub(r"\$\$", "$", text)


def _chat(client: OpenAI, messages: Iterable[ChatCompletionMessageParam]) -> str:
    last_error: Exception | None = None
    for attempt in range(LLM_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=list(messages),
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                timeout=LLM_TIMEOUT,
            )
            text = (response.choices[0].message.content or "").strip()
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


def _extract_png(client: OpenAI, png: bytes, page_file: Path, label: str) -> str:
    """提取单张 PNG 字节：结果文件已存在直接返回，否则调用模型并写入文件。

    空文件（如上次运行被中断产生的 0 字节文件）视为未提取，重新提取；
    模型返回空结果时不写文件，避免污染。
    """
    page_file.parent.mkdir(parents=True, exist_ok=True)
    if page_file.exists():
        cached = page_file.read_text(encoding="utf-8").strip()
        if cached:
            log.info("  [已提取] %s（%s）", label, page_file)
            return _fix_math(cached)
        log.warning("  结果文件为空，重新提取：%s", page_file)
        page_file.unlink()  # 删除空文件，重新提取

    log.info("  [提取] %s ...（输出：%s）", label, page_file)
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"},
                },
            ],
        }
    ]
    text = _chat(client, messages)
    if text:
        page_file.write_text(text, encoding="utf-8")
    else:
        log.warning("  ! %s：模型返回内容为空，未写入文件", label)
    return _fix_math(text)


def _pdf_id(pdf_path: Path) -> str:
    """PDF 唯一标识：仅取 PDF 文件内容的哈希前 16 位。

    同一本书永远对应同一输出目录；换提示词或换模型不会新建目录，
    但会通过目录内 config.txt 的指纹变化提醒用户：已提取页不会自动重跑。
    """
    h = hashlib.sha256()
    h.update(pdf_path.read_bytes())
    return h.hexdigest()[:16]


def _config_id() -> str:
    """提取配置指纹：提示词 + 模型名的哈希前 16 位。

    存进书目录的 config.txt；两者任一变化都会导致指纹不同，
    下次运行时据此提醒用户目录内旧页出自不同配置。
    """
    h = hashlib.sha256()
    h.update(PROMPT.encode("utf-8"))
    h.update(MODEL_NAME.encode("utf-8"))
    return h.hexdigest()[:16]


def _render_page(page: pymupdf.Page) -> bytes:
    """把 PDF 单页渲染成 PNG 字节，长边不超过 MAX_LONG_EDGE。"""
    rect = page.rect
    long_edge = max(rect.width, rect.height)
    zoom = min(MAX_ZOOM, MAX_LONG_EDGE / long_edge) if long_edge else 1.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def _page_file(pdf_id: str, page_no: int) -> Path:
    """某 PDF 第 page_no 页（从 1 计）的提取结果文件：output/{pdf_id}/p{页码}.md。

    页码零填充保证排序正确。该文件同时充当缓存：存在且非空即不再调模型。
    """
    return OUTPUT_DIR / pdf_id / f"p{page_no:04d}.md"


def process_pdf(client: OpenAI, pdf_path: Path, pdf_id: str, start: int, end: int) -> list[int]:
    """处理 PDF 指定页范围，返回成功提取的页码列表。

    逐页：结果文件已存在且非空则不渲染不调模型；否则渲染并调用视觉模型，
    结果直接写入 output/{pdf_id}/p{页码}.md，每页独立落盘，中断不丢数据。
    """
    doc = pymupdf.open(pdf_path)
    total = doc.page_count
    start = max(1, start)
    end = min(total, end)
    if start > end:
        doc.close()
        log.error("页范围 %d-%d 无效（PDF 共 %d 页）", start, end, total)
        raise SystemExit(f"错误：页范围 {start}-{end} 无效（PDF 共 {total} 页）。")

    log.info("PDF：%s（共 %d 页），处理第 %d-%d 页，id=%s", pdf_path.name, total, start, end, pdf_id)
    book_dir = OUTPUT_DIR / pdf_id
    book_dir.mkdir(parents=True, exist_ok=True)
    name_file = book_dir / "name.txt"
    if name_file.exists():
        log.info("书名文件已存在：%s（%s）", name_file, name_file.read_text(encoding="utf-8").strip())
    else:
        name_file.write_text(pdf_path.name, encoding="utf-8")
        log.info("已生成书名文件：%s（%s）", name_file, pdf_path.name)
    # 配置指纹：提示词/模型变了不会换目录，但要提醒旧页不会自动重提取。
    config_file = book_dir / "config.txt"
    config_id = _config_id()
    if config_file.exists():
        parts = config_file.read_text(encoding="utf-8").split()
        saved = parts[0] if parts else ""
        if saved and saved != config_id:
            log.warning(
                "检测到提示词/模型配置已变化（目录记录 %s -> 当前 %s）：目录内已提取的页"
                "仍会被跳过，不会用新配置重跑；如需重提取，请删除对应的 pXXXX.md 或整个目录。",
                saved, config_id,
            )
            config_file.write_text(f"{config_id} {MODEL_NAME}\n", encoding="utf-8")
    else:
        config_file.write_text(f"{config_id} {MODEL_NAME}\n", encoding="utf-8")
    done_pages: list[int] = []
    try:
        for page_no in range(start, end + 1):
            page_file = _page_file(pdf_id, page_no)
            if page_file.exists() and page_file.read_text(encoding="utf-8").strip():
                log.info("  [已提取] 第 %d 页（%s）", page_no, page_file.name)
            else:
                png = _render_page(doc.load_page(page_no - 1))
                body = _extract_png(client, png, page_file, f"第 {page_no} 页")
                if not body:
                    continue
            done_pages.append(page_no)
    finally:
        doc.close()
    return done_pages


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF -> Markdown 提取（视觉大模型）")
    parser.add_argument("--pdf", required=True, help="待处理的 PDF 文件路径")
    parser.add_argument("--start", type=int, default=1, help="起始页（从 1 计，默认 1）")
    parser.add_argument("--end", type=int, default=0, help="结束页（含，默认 0 表示到最后一页）")
    parser.add_argument(
        "--log-level", default="info",
        help="控制台日志级别：debug / info / warning / error（默认 info）；"
             "日志文件始终记录 debug 级别",
    )
    args = parser.parse_args()

    log = get_logger(parse_level(args.log_level))  # 按参数调整控制台级别
    log.info("启动参数：%s", vars(args))

    if not API_KEY:
        log.error("未设置 DASHSCOPE_API_KEY 环境变量。")
        log.error("请先运行：$env:DASHSCOPE_API_KEY=\"你的阿里云百炼 API Key\"")
        log.error("（阿里云百炼控制台：https://bailian.console.aliyun.com 获取 API Key）")
        raise SystemExit(1)

    client = _make_client()
    log.info("模型：%s（%s）", MODEL_NAME, BASE_URL)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        log.error("PDF 文件不存在：%s", pdf_path)
        raise SystemExit(f"错误：PDF 文件不存在：{pdf_path}")
    pdf_id = _pdf_id(pdf_path)
    end = args.end if args.end > 0 else 0x7FFFFFFF
    done_pages = process_pdf(client, pdf_path, pdf_id, args.start, end)
    if not done_pages:
        log.warning("没有成功提取任何页面。")
    else:
        log.info("完成：本次处理 %d 页，输出目录：%s", len(done_pages), OUTPUT_DIR / pdf_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # 每页提取完成即写入自己的输出文件，中断不会丢失已完成的内容。
        log.warning("收到 Ctrl+C，已中断。已完成的页均已保存在 %s 下，重新运行将自动跳过。", OUTPUT_DIR)
        raise SystemExit(130)

