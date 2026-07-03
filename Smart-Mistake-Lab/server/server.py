import os
import json
import base64
import re
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
load_dotenv(Path(__file__).parent.parent / '.env')

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import db
from log import logger
from llm import AiConfig, analyze_image


def _generate_solution_filename(original_path: str, index: int, ext: str) -> str:
    stem = Path(original_path).stem
    return f"{stem}_sol_{index}.{ext}"

app = FastAPI(title="Smart Mistake Lab Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}


def _infer_subject(file_path: str) -> str:
    """从文件路径推断学科：取 image_dir 下的第一级子目录名"""
    image_dir = db.get_config_value("image_dir") or ""
    if not image_dir:
        return ""
    try:
        rel = os.path.relpath(file_path, image_dir)
        parts = rel.replace("\\", "/").split("/")
        return parts[0] if len(parts) > 1 else ""
    except ValueError:
        return ""


def _scan_images_in_dir(directory: str, indexed_paths: set) -> list:
    """扫描单个目录下的所有图片文件，返回 [{file_path, file_name}...]"""
    result = []
    if not os.path.isdir(directory):
        return result
    try:
        for f in sorted(os.listdir(directory)):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                full_path = os.path.normpath(os.path.join(directory, f))
                if os.path.isfile(full_path) and not os.path.basename(full_path).startswith('.'):
                    result.append(full_path)
    except Exception:
        pass
    return result


@app.on_event("startup")
def startup():
    db.init_db()
    logger.info("Smart Mistake Lab Server 启动完成")
    # 输出配置信息，但不输出完整的 API Key
    cfg = AiConfig.from_env()
    logger.info(f"AI 配置：api_url={cfg.api_url}, model={cfg.model}, "
                f"has_api_key={'Yes' if cfg.api_key else 'No'}, timeout={cfg.timeout}s, max_tokens={cfg.max_tokens}")


# --- Health ---

@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Config ---

@app.get("/api/config")
def get_config():
    return {
        "image_dir": db.get_config_value("image_dir") or "",
    }


@app.put("/api/config")
def update_config(data: dict):
    if "image_dir" in data:
        db.set_config_value("image_dir", data["image_dir"])
        # 记录日志
        logger.info(f"图片目录已更新：{data['image_dir']}")
    return get_config()


# --- Scan ---

@app.get("/api/scan")
def scan_directory():
    image_dir = db.get_config_value("image_dir") or ""
    if not image_dir or not os.path.isdir(image_dir):
        logger.warning(f"图片目录未配置或不存在：{image_dir}")
        raise HTTPException(status_code=400, detail="图片目录未配置或不存在，请先在配置页面设置")

    indexed_paths = db.get_all_indexed_paths()

    by_subject = {}
    total_count = 0
    total_indexed = 0
    total_unindexed = 0

    # 扫描 image_dir 下的第一级子目录（每个 = 一个学科）
    try:
        entries = sorted(os.listdir(image_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取目录失败：{e}")

    for entry in entries:
        sub_path = os.path.join(image_dir, entry)
        if not os.path.isdir(sub_path):
            continue
        if entry.startswith('.'):
            continue
        subject = entry
        all_images = _scan_images_in_dir(sub_path, indexed_paths)

        indexed = []
        unindexed = []
        for fp in all_images:
            meta = db.get_image_by_path(fp)
            if meta:
                indexed.append(meta)
            else:
                unindexed.append({
                    "file_path": fp,
                    "file_name": os.path.basename(fp),
                    "title": "",
                    "summary": "",
                    "content": "",
                    "tags": [],
                    "notes": "",
                    "mastery": "",
                    "practice_count": 0,
                    "last_practiced_at": None,
                    "solution": "{}",
                    "indexed": False,
                })

        by_subject[subject] = {"indexed": indexed, "unindexed": unindexed}
        total_count += len(all_images)
        total_indexed += len(indexed)
        total_unindexed += len(unindexed)

    # 也处理根目录下的图片（不属于任何学科）
    root_images = [
        os.path.normpath(os.path.join(image_dir, f))
        for f in sorted(os.listdir(image_dir))
        if os.path.isfile(os.path.join(image_dir, f))
        and os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        and not f.startswith('.')
    ]
    if root_images:
        indexed = []
        unindexed = []
        for fp in root_images:
            meta = db.get_image_by_path(fp)
            if meta:
                indexed.append(meta)
            else:
                unindexed.append({
                    "file_path": fp,
                    "file_name": os.path.basename(fp),
                    "title": "",
                    "summary": "",
                    "content": "",
                    "tags": [],
                    "notes": "",
                    "mastery": "",
                    "practice_count": 0,
                    "last_practiced_at": None,
                    "solution": "{}",
                    "indexed": False,
                })
        by_subject["未分类"] = {"indexed": indexed, "unindexed": unindexed}
        total_count += len(root_images)
        total_indexed += len(indexed)
        total_unindexed += len(unindexed)

    # 预设学科顺序 + 剩余按名称 + 未分类垫底
    preset = ['数学', '物理', '化学', '英语', '语文']
    subject_order = [s for s in preset if s in by_subject]
    remaining = sorted(
        [s for s in by_subject if s not in preset and s != '未分类']
    )
    subject_order.extend(remaining)
    if '未分类' in by_subject:
        subject_order.append('未分类')

    return {
        "image_dir": image_dir,
        "total": total_count,
        "indexed_count": total_indexed,
        "unindexed_count": total_unindexed,
        "unindexed": sum((g["unindexed"] for g in by_subject.values()), []),
        "indexed": sum((g["indexed"] for g in by_subject.values()), []),
        "by_subject": by_subject,
        "subject_order": subject_order,
    }


# --- Serve Image File ---

@app.get("/api/image-file")
def get_image_file(path: str = Query(..., description="图片文件的绝对路径")):
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


# --- Index / Update / Delete ---

@app.post("/api/images/index")
def index_image(data: dict):
    file_path = data.get("file_path", "")
    title = data.get("title", "")
    summary = data.get("summary", "")
    content = data.get("content", "")
    tags = data.get("tags", [])
    notes = data.get("notes", "")
    mastery = data.get("mastery", "")
    practice_count = data.get("practice_count", 0)
    last_practiced_at = data.get("last_practiced_at")
    solution = data.get("solution", "")
    if isinstance(solution, dict):
        solution = json.dumps(solution, ensure_ascii=False)

    if not file_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

    subject = _infer_subject(file_path)
    db.mark_indexed(file_path, title, summary, content, tags, notes, mastery, practice_count, last_practiced_at, solution, subject)
    return {"status": "ok"}


@app.put("/api/images/update")
def update_image(data: dict):
    file_path = data.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

    solution = data.get("solution")
    if isinstance(solution, dict):
        solution = json.dumps(solution, ensure_ascii=False)

    db.update_image_meta(
        file_path,
        title=data.get("title"),
        summary=data.get("summary"),
        content=data.get("content"),
        tags=data.get("tags"),
        notes=data.get("notes"),
        mastery=data.get("mastery"),
        practice_count=data.get("practice_count"),
        last_practiced_at=data.get("last_practiced_at"),
        solution=solution,
    )
    return {"status": "ok"}


@app.delete("/api/images/delete")
def delete_image(file_path: str = Query(..., description="图片文件路径")):
    db.delete_image(file_path)
    return {"status": "ok"}


@app.get("/api/images/all")
def get_all_images(
    query: str = Query("", description="关键字搜索词"),
    subject: str = Query("", description="学科筛选"),
    date_enabled: bool = Query(False, description="是否启用日期范围筛选"),
    start_date: str | None = Query(None, description="开始日期，格式 YYYY-MM-DD"),
    end_date: str | None = Query(None, description="结束日期，格式 YYYY-MM-DD"),
):
    subject_param = subject.strip() or None

    # 错题库总数（不受筛选条件影响）
    total_count = db.get_total_image_count(subject=subject_param)

    # 构造日期时间字符串：开始日 00:00:00，结束日 23:59:59
    start_datetime = None
    end_datetime = None

    if date_enabled:
        if start_date:
            start_datetime = f"{start_date} 00:00:00"
        if end_date:
            end_datetime = f"{end_date} 23:59:59"

    # 如果有筛选条件则走 search_images，否则全量返回
    if query.strip() or start_datetime or end_datetime:
        items = db.search_images(
            query=query.strip() or None,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            subject=subject_param,
        )
    else:
        items = db.get_all_images(subject=subject_param)

    subjects = db.get_subject_counts()

    return {
        "items": items,
        "total_count": total_count,
        "filtered_count": len(items),
        "subjects": subjects,
    }


@app.post("/api/solution-image")
def upload_solution_image(data: dict):
    file_path = data.get("file_path", "")
    image_data = data.get("image_data", "")
    ext = (data.get("ext") or "png").lower().lstrip('.')

    if not file_path or not image_data:
        raise HTTPException(status_code=400, detail="file_path 和 image_data 不能为空")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="原题图片不存在")

    directory = os.path.dirname(file_path)
    stem = Path(file_path).stem
    existing_indexes = []
    pattern = re.compile(rf'^{re.escape(stem)}_sol_(\d+)\.\w+$', re.IGNORECASE)
    for name in os.listdir(directory):
        match = pattern.match(name)
        if match:
            existing_indexes.append(int(match.group(1)))
    new_index = (max(existing_indexes) + 1) if existing_indexes else 1

    filename = _generate_solution_filename(file_path, new_index, ext)
    save_path = os.path.join(directory, filename)
    base64_str = re.sub(r'^data:image/\w+;base64,', '', image_data.strip())

    try:
        raw = base64.b64decode(base64_str)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无效图片数据：{exc}")

    with open(save_path, 'wb') as f:
        f.write(raw)

    return {"filename": filename, "path": save_path}


@app.delete("/api/solution-image")
def delete_solution_image(path: str = Query(..., description="解答图片的绝对路径")):
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(path)
    return {"status": "ok"}


# --- AI Config ---

def _get_ai_config() -> dict:
    """获取 AI 配置，DB 中的值优先于环境变量"""
    env = AiConfig.from_env()
    return {
        "api_url": db.get_config_value("ai_api_url") or env.api_url,
        "model": db.get_config_value("ai_model") or env.model,
        "api_key": db.get_config_value("ai_api_key") or env.api_key,
        "timeout": env.timeout,
        "max_tokens": env.max_tokens,
    }


@app.get("/api/ai-config")
def get_ai_config():
    cfg = _get_ai_config()
    # 不返回完整的 api_key，只返回是否已设置
    return {
        "api_url": cfg["api_url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg["api_key"]),
    }


@app.put("/api/ai-config")
def update_ai_config(data: dict):
    if "api_url" in data and data["api_url"]:
        db.set_config_value("ai_api_url", data["api_url"])
    if "model" in data and data["model"]:
        db.set_config_value("ai_model", data["model"])
    if "api_key" in data and data["api_key"]:
        db.set_config_value("ai_api_key", data["api_key"])
    logger.info("AI 配置已更新")
    return get_ai_config()


# --- AI Analyze ---

@app.post("/api/analyze")
async def analyze(data: dict):
    """对指定图片进行 AI 分析，返回 {title, summary, tags}"""
    file_path = data.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"图片文件不存在：{file_path}")

    cfg = _get_ai_config()
    ai_config = AiConfig(
        api_url=cfg["api_url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        timeout=cfg["timeout"],
        max_tokens=cfg["max_tokens"],
    )

    subject = _infer_subject(file_path)
    logger.info(f'[API] 收到分析请求：{file_path}, subject={subject}')

    try:
        result = await analyze_image(file_path, ai_config, subject=subject)
        logger.info(f'[API] 分析完成：{file_path} -> tags={result.get("tags", [])}')
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception(f'[API] 分析异常：{file_path}')
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
