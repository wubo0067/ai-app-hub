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

# ============== 各学科知识点 ==============

MATH_KNOWLEDGE_POINTS = [
    "三角形中位线定理",
    "勾股定理",
    "一元二次方程",
    "全等三角形判定",
    "二次函数图像与性质",
    "一次函数与图像",
    "反比例函数",
    "平行四边形的判定与性质"
    "矩形的性质与判定",
    "菱形的性质与判定",
    "正方形的性质与判定",
    "圆的切线性质",
    "圆周角定理",
    "垂径定理",
    "弧长与扇形面积",
    "锐角三角函数",
    "因式分解",
    "分式方程",
    "不等式与不等式组",
    "图形的平移与旋转",
    "轴对称与中心对称",
    "概率初步",
    "统计图表分析",
    "加权平均数与方差",
    "瓜豆原理动点轨迹为直线",
    "瓜豆原理动点轨迹为圆",
    "胡不归",
    "圆的内接四边形，对角互补",
    "定边对定角判定四点共圆，",
    "求最值，两定一动，定线段，构造平行四边形",
    "求最值，两定一动，将军饮马",
    "求最值，逆等线段",
    "求最值，代数题，数形结合"
    "垂美四边形",
    "托勒密定理",
    "韦达定理",
    "构造一元二次方程",
    "三角形内心",
    "三角形外心",
    "三角形重心",
    "三角形垂心",
    "三角形九点圆",
    "相似三角形判定 1，两个角相等",
    "相似三角形判定 2，夹角，夹边成比例",
    "相似三角形判定 3，边边边成比例",
    "相似三角形，A 字模型",
    "相似三角形，反 A 字模型",
    "相似三角形，8 字模型",
    "相似三角形，反 8 字模型",
    "相似三角形，射影定理",
    "相似三角形，角平分线模型",
    "孤单直角做三垂直构造三角形相似",
    "相似三角形，线段等积式",
    "相似三角形对应线段（高/中线/角平分线）的比等于相似比",
    "相似三角形周长的比等于相似比",
    "相似三角形面积的比等于相似比的平方",
    "坐标法/参数法表示线段，转化为函数最值问题",
    "翻折图形，同步信息，连接对称点",
    "遇到梯形想平移",
    "遇到中线想倍长",
    "柯西不等式",
    "配凑思想",
    "数形结合思想",
    "复合二次根式，把复合根号前面的系数变为 2，完全平方公式",
    "二倍角",
    "存在 90 度角就导角",
]

PHYSICS_KNOWLEDGE_POINTS = [
    "牛顿第一定律", "牛顿第二定律", "牛顿第三定律", "重力与弹力",
    "摩擦力", "力的合成与分解", "二力平衡", "压强", "液体压强",
    "大气压强", "浮力", "阿基米德原理", "物体浮沉条件",
    "功与功率", "机械效率", "动能与势能", "机械能守恒",
    "杠杆平衡条件", "滑轮组", "斜面", "光的反射", "平面镜成像",
    "光的折射", "凸透镜成像", "温度与物态变化", "比热容",
    "热值", "内能与热机", "电流与电路", "欧姆定律",
    "电阻的串并联", "电功与电功率", "焦耳定律", "家庭电路",
    "磁场与电流的磁场", "电磁感应", "速度与平均速度", "声音的产生与传播",
    "浮力，融化，密度大于就升，密度小于就降，密度相等就不变",
]

CHEMISTRY_KNOWLEDGE_POINTS = [
    "物理变化与化学变化", "物质的性质", "氧气的性质与制取",
    "空气的组成", "水的组成与净化", "质量守恒定律", "化学方程式",
    "碳的单质", "一氧化碳与二氧化碳", "燃烧与灭火",
    "金属材料", "金属的化学性质", "金属活动性顺序", "金属资源的利用与保护",
    "溶液的形成", "溶解度", "溶质质量分数", "溶液的配制",
    "常见的酸", "常见的碱", "中和反应", "溶液的酸碱度 pH",
    "盐的化学性质", "复分解反应", "化学肥料",
    "分子与原子", "原子的结构", "元素与元素周期表",
    "化合价与化学式", "有关化学式的计算", "有关化学方程式的计算"
]

ENGLISH_KNOWLEDGE_POINTS = [
    "一般现在时", "一般过去时", "一般将来时", "现在进行时",
    "过去进行时", "现在完成时", "过去完成时", "被动语态",
    "情态动词", "定语从句", "宾语从句", "状语从句",
    "条件状语从句", "虚拟语气", "非谓语动词", "主谓一致",
    "冠词用法", "介词搭配", "形容词与副词比较级", "不定代词",
    "阅读理解 - 主旨大意", "阅读理解 - 细节理解", "阅读理解 - 推理判断",
    "完形填空 - 上下文逻辑", "完形填空 - 词义辨析",
    "书面表达 - 书信格式", "书面表达 - 议论文结构",
    "单词拼写", "短语搭配", "同义词辨析", "反义词"
]

CHINESE_KNOWLEDGE_POINTS = [
    "字音辨析", "字形辨析", "词语运用", "成语运用",
    "病句辨析", "标点符号", "修辞手法", "仿写与句式变换",
    "古诗文默写", "古诗词鉴赏 - 意象", "古诗词鉴赏 - 情感", "古诗词鉴赏 - 手法",
    "文言文实词", "文言文虚词", "文言文翻译", "文言文断句",
    "现代文阅读 - 记叙文", "现代文阅读 - 说明文", "现代文阅读 - 议论文",
    "名著阅读", "综合性学习", "口语交际",
    "作文 - 审题立意", "作文 - 结构布局", "作文 - 素材运用", "作文 - 语言表达",
    "文学常识", "传统文化"
]

# ============== 学科配置 ==============

SUBJECT_CONFIG = {
    "数学": {
        "role": "经验丰富的初中数学老师",
        "knowledge_points": MATH_KNOWLEDGE_POINTS,
    },
    "物理": {
        "role": "经验丰富的初中物理老师",
        "knowledge_points": PHYSICS_KNOWLEDGE_POINTS,
    },
    "化学": {
        "role": "经验丰富的初中化学老师",
        "knowledge_points": CHEMISTRY_KNOWLEDGE_POINTS,
    },
    "英语": {
        "role": "经验丰富的初中英语老师",
        "knowledge_points": ENGLISH_KNOWLEDGE_POINTS,
    },
    "语文": {
        "role": "经验丰富的初中语文老师",
        "knowledge_points": CHINESE_KNOWLEDGE_POINTS,
    },
}

DEFAULT_SUBJECT_CONFIG = {
    "role": "经验丰富的中学老师",
    "knowledge_points": [],
}


def build_analysis_prompt(subject: str = "") -> str:
    """根据学科构建分析 prompt"""
    cfg = SUBJECT_CONFIG.get(subject, DEFAULT_SUBJECT_CONFIG)
    role = cfg["role"]
    knowledge_points = cfg["knowledge_points"]

    if knowledge_points:
        kp_lines = (
            f'3. 所有知识点标签**必须严格从**以下候选列表中选取，不得自由发明列表中不存在的知识点：\n'
            f'[{", ".join(knowledge_points)}]\n'
            '4. **例外**：仅当你确认该题目涉及的核心考点在候选列表中确实没有匹配项时，最多可补充 1 个你自己推理出的知识点（命名风格必须与候选列表保持一致：简洁、具体、专业，不要过于笼统）。\n'
        )
    else:
        kp_lines = (
            '3. 自己推理出题目涉及的知识点（命名风格：简洁、具体、专业，不要过于笼统）。\n'
        )

    return (
        f'你是一位{role}。请读取图片中的题目文字，并识别题目涉及的知识点。\n'
        '\n'
        '【规则】\n'
        '1. 先提取题目中的文字内容，只保留题干、条件、问题本身，不要描述图形，不要补充推理，不要解释。\n'
        '2. 如果图片里同时有图形和文字，只提取可见的题目文字内容，忽略图形关系本身。\n'
        + kp_lines +
        '5. 最多给出 5 个知识点，不要过于笼统（避免只写"几何""代数""语法"这种大类）。\n'
        '\n'
        '【输出格式】\n'
        '只输出一个 JSON 对象，不要有任何其他文字，不要用 markdown 代码块包裹：\n'
        '{"content": "题目文字内容", "tags": ["知识点 1", "知识点 2", "知识点 3"]}'
    )


# 保留旧变量以兼容可能的引用
ANALYSIS_PROMPT = build_analysis_prompt()


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

def build_analyze_request(config: AiConfig, api_url: str, image_data_uri: str, image_base64: str, prompt: str = "") -> dict:
    """构建 AI 分析请求，返回 {headers, body}"""
    if not prompt:
        prompt = build_analysis_prompt()
    #输出 prompt
    logger.info(f'[LLM] 使用 Prompt: {prompt}')

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
                        {'type': 'text', 'text': prompt},
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
                    'content': prompt,
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
                    {'type': 'text', 'text': prompt},
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
    """解析 AI 返回的 JSON 文本，提取 content/tags"""
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
    content = ''
    if isinstance(parsed, list):
        tags = parsed
    elif isinstance(parsed, dict):
        tags = parsed.get('tags') if isinstance(parsed.get('tags'), list) else []
        content = parsed.get('content', '') if isinstance(parsed.get('content'), str) else ''
    else:
        tags = []
    return {'content': content.strip(), 'tags': tags}


# ============== 核心分析函数 ==============

async def analyze_image(
    image_path: str,
    config: Optional[AiConfig] = None,
    subject: str = "",
) -> dict:
    """
    分析错题图片，返回 {title, summary, tags}。

    Args:
        image_path: 图片文件的绝对路径
        config: AI 配置，若为 None 则从环境变量读取
        subject: 学科名称（数学/物理/化学/英语/语文），用于选择知识点列表

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
    prompt = build_analysis_prompt(subject)
    request = build_analyze_request(config, api_url, data_uri, image_base64, prompt)

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


# ============== 鼓励语生成 ==============

ENCOURAGEMENT_PROMPT = """你是一名学习督促助手。

以下是用户错题本中多道超时未练习的题目信息，请为每道题生成一句 20 字以内的鼓励语。

要求：
- 每句不超过 20 个汉字
- 鼓励但不说教，不要用"加油""你可以的"这类空话
- 结合题目信息和超时情况，让建议具体
- 不要输出序号或多余解释
- 必须为每条题目返回对应的 file_path，且不要遗漏任何一条
- 只输出 JSON 数组，格式：[{"file_path": "...", "message": "..."}]
"""


def parse_encouragement_result(raw_text: str) -> list[dict]:
    """解析鼓励语 AI 响应，返回 [{file_path, message}, ...]"""
    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```json\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^```\s*', '', cleaned)
    cleaned = re.sub(r'```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        return []

    def normalize_entries(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            if isinstance(value.get("file_path"), str) and isinstance(value.get("message"), str):
                return [value]
            for key in ("reminders", "items", "results", "data"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return nested
            if value and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                return [{"file_path": key, "message": val} for key, val in value.items()]
        return []

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
            logger.warning(f"[Encourage] 响应不是 JSON，已忽略：{cleaned[:80]}")
            return []
    entries = normalize_entries(parsed)
    if not entries:
        logger.warning(f"[Encourage] 期望 JSON 数组或兼容对象，实际得到 {type(parsed).__name__}，已忽略")
        return []
    return entries


def load_json_relaxed(raw_text: str) -> dict:
    """宽松解析 AI 返回的 JSON，兼容前后夹杂少量说明文本的情况。"""
    cleaned = raw_text.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for start in (cleaned.find('{'), cleaned.find('[')):
        if start == -1:
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise json.JSONDecodeError('Expecting JSON object', cleaned, 0)


async def generate_encouragements(items: list[dict]) -> dict:
    """
    批量生成超时题目的鼓励语。
    items: [{title, subject, tags, inactive_hours, is_focus_overdue, file_path}, ...]
    返回：{file_path: message, ...}
    """
    from_env = AiConfig.from_env()
    cfg = {
        "api_url": os.environ.get("AI_API_URL") or from_env.api_url,
        "model": os.environ.get("AI_MODEL") or from_env.model,
        "api_key": os.environ.get("AI_API_KEY") or from_env.api_key,
    }
    ai_config = AiConfig(
        api_url=cfg["api_url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        timeout=120.0,
        max_tokens=1024,
    )
    api_url = normalize_api_url(ai_config.api_url)
    if not api_url or not ai_config.model.strip():
        logger.warning("[Encourage] AI 未配置，跳过鼓励语生成")
        return {}

    # 构建题目标题摘要
    lines = []
    for it in items:
        title = it.get("title", "未命名") or "未命名"
        subject = it.get("subject", "") or ""
        file_path = it.get("file_path", "") or ""
        tags_str = ", ".join((it.get("tags", []) or [])[:3])
        days = round((it.get("inactive_hours", 0) or 0) / 24, 1)
        lines.append(f"- file_path: {file_path}\n  题目：{title}（{subject}）\n  标签：{tags_str}\n  已 {days} 天未练习")
    items_text = "\n".join(lines)
    prompt = ENCOURAGEMENT_PROMPT + "\n\n" + items_text + "\n\n请严格输出 JSON 数组，不要输出任何解释："

    logger.info(f"[Encourage] 调用 AI 生成鼓励语，items={len(items)}")

    headers = {'Content-Type': 'application/json'}
    if ai_config.api_key:
        headers['Authorization'] = f'Bearer {ai_config.api_key}'

    body = {
        'model': ai_config.model,
        'max_tokens': ai_config.max_tokens,
        'stream': False,
        'messages': [
            {'role': 'user', 'content': prompt}
        ],
    }

    if is_ollama_chat_endpoint(api_url):
        body['think'] = False
        body['format'] = 'json'
        body['options'] = {'num_predict': ai_config.max_tokens}

    async with httpx.AsyncClient(timeout=httpx.Timeout(ai_config.timeout), trust_env=False) as client:
        resp = await client.post(api_url, headers=headers, json=body)

    if not resp.is_success:
        logger.error(f"[Encourage] AI 调用失败：HTTP {resp.status_code}")
        return {}

    response_data = load_json_relaxed(resp.text)
    response_text = extract_text_from_response(response_data, api_url)
    if not response_text:
        logger.warning("[Encourage] AI 返回空文本")
        return {}

    try:
        parsed = parse_encouragement_result(response_text)
        result = {}
        for entry in parsed:
            fp = entry.get("file_path", "")
            msg = (entry.get("message", "") or "").strip()
            if fp and msg:
                result[fp] = msg
        logger.info(f"[Encourage] 成功生成 {len(result)} 条鼓励语")
        return result
    except Exception as e:
        logger.error(f"[Encourage] 解析失败：{e}, raw={response_text[:200]}")
        return {}
