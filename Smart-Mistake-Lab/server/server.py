import os
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

    all_images = []
    try:
        for f in sorted(os.listdir(image_dir)):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                full_path = os.path.normpath(os.path.join(image_dir, f))
                all_images.append(full_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取目录失败：{e}")

    unindexed = []
    indexed = []
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

    return {
        "image_dir": image_dir,
        "total": len(all_images),
        "indexed_count": len(indexed),
        "unindexed_count": len(unindexed),
        "unindexed": unindexed,
        "indexed": indexed,
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

    if not file_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

    db.mark_indexed(file_path, title, summary, content, tags, notes, mastery, practice_count, last_practiced_at, solution)
    return {"status": "ok"}


@app.put("/api/images/update")
def update_image(data: dict):
    file_path = data.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path 不能为空")

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
        solution=data.get("solution"),
    )
    return {"status": "ok"}


@app.delete("/api/images/delete")
def delete_image(file_path: str = Query(..., description="图片文件路径")):
    db.delete_image(file_path)
    return {"status": "ok"}


@app.get("/api/images/all")
def get_all_images():
    return db.get_all_images()


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

    logger.info(f'[API] 收到分析请求：{file_path}')

    try:
        result = await analyze_image(file_path, ai_config)
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
