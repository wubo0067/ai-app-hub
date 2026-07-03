"""
Smart Mistake Lab - LLM 交互模块
负责 Prompt 管理、AI API 调用、响应解析。
"""

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from log import logger


# ============== Prompt 管理 ==============

MATH_KNOWLEDGE_POINTS = [
    "三角形中位线定理", "勾股定理", "一元二次方程",
    "全等三角形判定", "二次函数图像与性质", "一次函数与图像",
    "反比例函数", "平行四边形的判定与性质", "矩形的性质与判定",
    "菱形的性质与判定", "正方形的性质与判定", "圆的切线性质",
    "圆周角定理", "垂径定理", "弧长与扇形面积",
    "锐角三角函数", "因式分解","分式方程", "不等式与不等式组",
    "图形的平移与旋转", "轴对称与中心对称", "概率初步",
    "统计图表分析", "加权平均数与方差", "瓜豆原理动点轨迹为直线", "瓜豆原理动点轨迹为圆",
    "胡不归", "圆的内接四边形",
    "两定一动求最值，定线段，构造平行四边形", "两定一动，将军饮马",
    "最值，逆等线段","垂美四边形","托勒密定理","韦达定理","构造一元二次方程",
    "三角形内心", "三角形外心", "三角形重心", "三角形垂心", "三角形九点圆",
    "相似三角形，A 字模型", "相似三角形，反 A 字模型", "相似三角形，8 字模型", "相似三角形，反 8 字模型",
    "相似三角形，射影定理", "相似三角形，角平分线模型", "孤单之角，做三垂直，构造相似",
    "翻折图形", "相似三角形，线段等积式", "直角坐标系"
]

ANALYSIS_PROMPT = (
    '你是一位经验丰富的初中数学老师。请分析图片中的这道数学题，识别其所涉及的知识点。\n'
    '\n'
    '【规则】\n'
    '1. 优先从以下候选知识点列表中选择最匹配的标签：\n'
    f'[{", ".join(MATH_KNOWLEDGE_POINTS)}]\n'
    '2. 如果候选列表无法完全覆盖该题的全部考点，可以在结果中补充你自己推理出的知识点（命名风格与候选列表保持一致：简洁、具体、专业）。\n'
    '3. 最多给出 5 个知识点，不要过于笼统（避免只写"几何""代数"这种大类）。\n'
    '\n'
    '【输出格式】\n'
    '只输出一个 JSON 数组，不要有任何其他文字，不要用 markdown 代码块包裹：\n'
    '["知识点 1", "知识点 2", "知识点 3"]'
)


# ============== 默认 AI 配置 ==============

@dataclass
class AiConfig:
    api_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 120.0
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> 'AiConfig':
        return cls(
            api_url=os.getenv('AI_API_URL', 'https://api.deepseek.com'),
            model=os.getenv('AI_MODEL', 'deepseek-v4-pro'),
            api_key=os.getenv('AI_API_KEY', ''),
            timeout=float(os.getenv('AI_TIMEOUT', '120')),
            max_tokens=int(os.getenv('AI_MAX_TOKENS', '4096')),
        )


# ============== 端点检测 ==============

def is_anthropic_endpoint(api_url: str) -> bool:
    return bool(re.search(r'/v1/messages(?:$|\?)', api_url))


def is_ollama_chat_endpoint(api_url: str) -> bool:
    return bool(re.search(r'/api/chat(?:$|\?)', api_url))


def is_probably_ollama_base_url(api_url: str) -> bool:
    return bool(re.match(r'^https?://(localhost|127\.0\.0\.1)(:\d+)?/?$', api_url.strip()))


def normalize_api_url(api_url: str) -> str:
    trimmed = api_url.strip()
    if not trimmed:
        return trimmed
    if is_anthropic_endpoint(trimmed) or '/v1/chat/completions' in trimmed or is_ollama_chat_endpoint(trimmed):
        return trimmed
    if is_probably_ollama_base_url(trimmed):
        return f"{trimmed.rstrip('/')}/api/chat"
    return f"{trimmed.rstrip('/')}/v1/chat/completions"


def should_require_api_key(api_url: str) -> bool:
    return is_anthropic_endpoint(api_url)


# ============== 请求构建 ==============

def build_analyze_request(config: AiConfig, api_url: str, image_data_uri: str, image_base64: str) -> dict:
    """构建 AI 分析请求，返回 {headers, body}"""
    #输出 prompt
    logger.info(f'[LLM] 使用 Prompt: {ANALYSIS_PROMPT}')

    if is_anthropic_endpoint(api_url):
        return {
            'headers': {
                'Content-Type': 'application/json',
                'x-api-key': config.api_key,
                'anthropic-version': '2023-06-01',
            },
            'body': {
                'model': config.model,
                'max_tokens': config.max_tokens,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': image_base64.split(',')[1] if ',' in image_base64 else image_base64}},
                        {'type': 'text', 'text': ANALYSIS_PROMPT},
                    ]
                }],
            },
        }
    if is_ollama_chat_endpoint(api_url):
        return {
            'headers': {'Content-Type': 'application/json'},
            'body': {
                'model': config.model,
                'stream': False,
                'think': False,
                'options': {'num_predict': config.max_tokens},
                'messages': [{
                    'role': 'user',
                    'content': ANALYSIS_PROMPT,
                    'images': [image_base64],
                }],
            },
        }
    # OpenAI 兼容格式
    headers = {'Content-Type': 'application/json'}
    if config.api_key:
        headers['Authorization'] = f'Bearer {config.api_key}'
    return {
        'headers': headers,
        'body': {
            'model': config.model,
            'max_tokens': config.max_tokens,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': ANALYSIS_PROMPT},
                        {'type': 'image_url', 'image_url': {'url': image_data_uri}},
                ]
            }],
        },
    }


# ============== 响应解析 ==============

def extract_text_from_response(data: dict, api_url: str) -> str:
    """从 AI 响应中提取文本内容"""
    if is_anthropic_endpoint(api_url):
        text_block = next((block for block in (data.get('content') or []) if block.get('type') == 'text'), None)
        return text_block['text'] if text_block else ''

    if is_ollama_chat_endpoint(api_url):
        message = data.get('message', {})
        content = message.get('content', '')
        thinking = message.get('thinking', '')
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(thinking, str) and thinking.strip():
            logger.info('[LLM] Ollama content 为空，使用 thinking 字段')
            return thinking
        return ''

    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    reasoning = data.get('choices', [{}])[0].get('message', {}).get('reasoning', '')
    if isinstance(content, str) and content.strip():
        return content
    # thinking 模型可能把最终答案放在 content，推理过程在 reasoning；
    # 若 content 为空则回退到 reasoning（也可能是 token 不足，仅输出了 reasoning）
    if isinstance(reasoning, str) and reasoning.strip():
        logger.info('[LLM] content 为空，使用 reasoning 字段')
        return reasoning
    if isinstance(content, list):
        return ''.join(item.get('text', '') for item in content if item.get('type') == 'text')
    return ''


def format_ai_error(detail: str) -> str:
    if 'unknown variant `image_url`' in detail or 'expected `text`' in detail.lower():
        return '当前 AI 服务拒绝了图片消息格式（image_url）。该服务的兼容接口没有接受本应用发送的图片输入格式。'
    return f'AI API error: {detail}'


def parse_analysis_result(raw_text: str) -> dict:
    """解析 AI 返回的 JSON 文本，提取 title/summary/tags"""
    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^```\s*', '', cleaned)
    cleaned = re.sub(r'```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start_positions = [idx for idx in (cleaned.find('['), cleaned.find('{')) if idx != -1]
        parsed = None
        for start in sorted(start_positions):
            try:
                parsed, _ = decoder.raw_decode(cleaned[start:])
                break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            raise
    # 支持两种格式：直接返回数组 ["tag1", "tag2"] 或 {"tags": [...]}
    if isinstance(parsed, list):
        tags = parsed
    elif isinstance(parsed, dict):
        tags = parsed.get('tags') if isinstance(parsed.get('tags'), list) else []
    else:
        tags = []
    return {'tags': tags}


# ============== 核心分析函数 ==============

async def analyze_image(
    image_path: str,
    config: Optional[AiConfig] = None,
) -> dict:
    """
    分析错题图片，返回 {title, summary, tags}。

    Args:
        image_path: 图片文件的绝对路径
        config: AI 配置，若为 None 则从环境变量读取

    Returns:
        {'title': str, 'summary': str, 'tags': list[str]}

    Raises:
        FileNotFoundError: 图片文件不存在
        ValueError: AI 配置无效
        RuntimeError: AI 调用失败
    """
    if config is None:
        config = AiConfig.from_env()

    logger.info(f'[LLM] 开始分析图片：{image_path}')

    # 1. 读取图片并转 base64
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f'图片文件不存在：{image_path}')

    with open(image_path, 'rb') as f:
        image_data = f.read()
    image_base64 = base64.b64encode(image_data).decode('utf-8')

    # 判断图片类型
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
    mime_type = mime_map.get(ext, 'image/jpeg')
    data_uri = f'data:{mime_type};base64,{image_base64}'

    logger.info(f'[LLM] 图片已编码，大小：{len(image_data)} bytes, MIME: {mime_type}')

    # 2. 校验配置
    api_url = normalize_api_url(config.api_url)
    if not api_url or not config.model.strip():
        raise ValueError('AI 配置不完整：请设置 API URL 和模型名')

    if should_require_api_key(api_url) and not config.api_key:
        raise ValueError('该端点需要 API Key')

    logger.info(f'[LLM] 调用 AI API: url={api_url}, model={config.model}')

    # 3. 构建请求
    request = build_analyze_request(config, api_url, data_uri, image_base64)

    # 4. 调用 AI
    async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout), trust_env=False) as client:
        resp = await client.post(api_url, headers=request['headers'], json=request['body'])

    logger.info(f'[LLM] AI 响应：status={resp.status_code}, len={len(resp.text)}')

    if not resp.is_success:
        err_detail = f'HTTP {resp.status_code} {resp.reason_phrase}'
        try:
            err_data = resp.json()
            err_detail = err_data.get('error', {}).get('message') or err_data.get('message') or err_data.get('detail') or err_detail
        except Exception:
            text_snippet = re.sub(r'<[^>]+>', '', resp.text).strip()[:200]
            if text_snippet:
                err_detail = f'HTTP {resp.status_code}: {text_snippet}'
        logger.error(f'[LLM] AI 调用失败：{err_detail}')
        raise RuntimeError(format_ai_error(err_detail))

    # 5. 解析响应
    data = resp.json()
    response_text = extract_text_from_response(data, api_url)
    if not response_text:
        logger.error(f'[LLM] AI 返回无文本内容：{json.dumps(data, ensure_ascii=False)[:500]}')
        raise RuntimeError('AI 未返回文本内容，请检查模型名称')

    logger.info(f'[LLM] AI 返回文本长度：{len(response_text)}')

    # 6. 解析 JSON 结果
    try:
        result = parse_analysis_result(response_text)
        logger.info(f'[LLM] 解析成功：tags={result["tags"]}')
        return result
    except json.JSONDecodeError as e:
        logger.error(f'[LLM] JSON 解析失败：{e}, raw={response_text[:300]}')
        raise RuntimeError(f'AI 返回的不是有效 JSON: {e}')
