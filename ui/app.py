"""Independent management service. Talks to Agent only through its public HTTP API."""
import asyncio
import hashlib
import hmac
import json
import os
import shutil
import signal
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import FIELDS, UNKNOWN, VERDICTS, case_comparison, case_id, collection_for, extract, result_fingerprint, stats
from .discovery import checked_file, inventory, scan, scan_events, stable_file
from .storage import Store
from .script_reports import markdown_snapshot, produced_reports
from .metadata import report_metadata, scan_metadata

HERE = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=2048)
def report_facts(path: str, size: int, modified_ns: int):
    content = Path(path).read_text(encoding="utf-8", errors="replace")[:300000]
    return extract(content), report_metadata(content)


class TaskInput(BaseModel):
    vmcore_path: str
    vmcore_dmesg_path: str | None = None
    vmlinux_path: str | None = None
    debug_symbol_paths: list[str] = Field(default_factory=list)


class ReviewInput(BaseModel):
    selected_task_id: str | None = None
    human: dict[str, str] = Field(default_factory=dict)
    verdicts: dict[str, str] = Field(default_factory=dict)
    human_seconds: int | None = Field(default=None, ge=0)
    notes: str = ""


class ImportInput(BaseModel):
    filename: str
    markdown: str = Field(min_length=1, max_length=10_000_000)


class SettingsInput(BaseModel):
    agent_url: str
    scan_roots: list[str]
    symbol_roots: list[str]
    stable_seconds: int = Field(ge=1, le=86400)
    max_parallel: int = Field(ge=1, le=1)
    execution_mode: str = "agent"
    script_path: str = "/data/analytics_vmcore.sh"
    report_roots: list[str] = Field(default_factory=list)


class BatchInput(BaseModel):
    collection: str | None = None


class DashboardOverridesInput(BaseModel):
    success_rate: float | None = Field(default=None, ge=0, le=100)
    avg_analysis_seconds: float | None = Field(default=None, ge=0, le=1_000_000_000)
    avg_human_seconds: float | None = Field(default=None, ge=0, le=1_000_000_000)
    avg_saved_seconds: float | None = Field(default=None, ge=-1_000_000_000, le=1_000_000_000)


def make_app(data_dir=None, password=None, secret=None):
    password = password or os.environ.get("UI_ADMIN_PASSWORD")
    secret = secret or os.environ.get("UI_SESSION_SECRET")
    if not password or not secret or len(secret) < 24:
        raise RuntimeError("设置 UI_ADMIN_PASSWORD 和长度至少 24 的 UI_SESSION_SECRET")
    store = Store(Path(data_dir or os.environ.get("UI_DATA_DIR", HERE / "data")))
    if not store.read("config"):
        example = json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
        store.write("config", example)
    queue_wake = asyncio.Event()
    workers = []
    active_runs = {}
    active_processes = {}
    script_lock = asyncio.Lock()

    def update_task(task):
        store.upsert("tasks", task)

    def fail(task, reason, status="failed"):
        task.update(status=status, error=str(reason), finished_at=now())
        if task.get("started_at"):
            task["duration_seconds"] = round((datetime.fromisoformat(task["finished_at"]) -
                                               datetime.fromisoformat(task["started_at"])).total_seconds(), 1)
        update_task(task)
        store.log("task_finished", f"{task['id']} {status}: {reason}", "system")

    def finish_report(task, report, audit="", source_report=None, source_audit=None):
        task_id = task["id"]
        store.atomic(store.root / "reports" / f"{task_id}.md", report)
        if audit:
            store.atomic(store.root / "audits" / f"{task_id}.md", audit)
        task.update(status="success", progress="分析完成", report=f"reports/{task_id}.md",
                    audit=f"audits/{task_id}.md" if audit else None, finished_at=now(), error=None,
                    source_report=source_report, source_audit=source_audit)
        task["duration_seconds"] = round((datetime.fromisoformat(task["finished_at"]) -
                                           datetime.fromisoformat(task["started_at"])).total_seconds(), 1)
        dmesg = ""
        if task.get("vmcore_dmesg_path"):
            with open(task["vmcore_dmesg_path"], "r", encoding="utf-8", errors="replace") as source:
                dmesg = source.read(200000)
        metadata = extract(report, dmesg)
        if task.get("token_usage") is None:
            task["token_usage"] = metadata["token_usage"]
        task["ai"] = metadata["ai"]
        task["result_fingerprint"] = result_fingerprint(report)
        case = next(c for c in store.read("cases") if c["id"] == task["case_id"])
        for key in ("hostname", "dump_time", "kernel", "panic", "os_name"):
            if metadata[key] != UNKNOWN:
                case[key] = metadata[key]
        case["ai"] = metadata["ai"]
        case["latest_task_id"] = task_id
        store.upsert("cases", case)
        update_task(task)

    async def run_script(task, config):
        # One script at a time: shared output directories otherwise make attribution ambiguous.
        async with script_lock:
            parent = Path(task["vmcore_path"]).parent
            matching = sorted((Path(root).resolve() for root in config["scan_roots"]
                               if parent.is_relative_to(Path(root).resolve())), key=lambda p: len(p.parts), reverse=True)
            if not matching:
                raise ValueError("vmcore 不在允许扫描目录内")
            argument = parent.relative_to(matching[0]).as_posix()
            if argument in ("", "."):
                raise ValueError("脚本参数须为扫描目录下的相对父路径")
            roots = config.get("report_roots") or [str(parent)]
            inaccessible = [root for root in roots if not Path(root).is_dir() or
                            not os.access(root, os.R_OK | os.W_OK | os.X_OK)]
            if inaccessible:
                raise ValueError("报告输出目录对 UI 服务用户不可读写：" + "、".join(inaccessible))
            before = markdown_snapshot(roots)
            task.update(status="running", started_at=now(), progress="脚本运行中", step=0,
                        token_usage=None)
            update_task(task)
            process = await asyncio.create_subprocess_exec(
                "bash", config["script_path"], argument,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt")
            active_processes[task["id"]] = process
            output = []
            log_path = store.root / "logs" / f"{task['id']}.log"
            task["script_log"] = f"logs/{task['id']}.log"
            update_task(task)
            try:
                with log_path.open("wb") as log:
                    while line := await process.stdout.readline():
                        log.write(line)
                        log.flush()
                        message = line.decode("utf-8", errors="replace").strip()
                        if message:
                            output.append(message)
                            output = output[-30:]
                            task["progress"] = message[:240]
                            update_task(task)
                code = await process.wait()
                if code:
                    raise RuntimeError(f"分析脚本退出码 {code}：{' | '.join(output[-3:])[:600]}")
                try:
                    source, audit_path = produced_reports(before, markdown_snapshot(roots), task["vmcore_path"])
                except ValueError as exc:
                    failed_save = next((line for line in reversed(output) if
                                        "保存报告失败" in line or "Agent 回答为空" in line), None)
                    detail = f"；脚本输出：{failed_save[:300]}" if failed_save else ""
                    raise ValueError(f"{exc}；已检查目录：{', '.join(roots)}{detail}") from exc
                report = source.read_text(encoding="utf-8", errors="replace")
                audit = audit_path.read_text(encoding="utf-8", errors="replace") if audit_path else ""
                finish_report(task, report, audit, str(source), str(audit_path) if audit_path else None)
            finally:
                active_processes.pop(task["id"], None)
                if process.returncode is None:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        await process.wait()

    async def run_task(task_id):
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task or task["status"] != "queued":
            return
        config = store.read("config")
        try:
            script_mode = task.get("execution_mode", "agent") == "script"
            paths = ([task["vmcore_path"], *([task["vmcore_dmesg_path"]] if task.get("vmcore_dmesg_path") else [])] if script_mode else
                     [task["vmcore_path"], task["vmcore_dmesg_path"], task["vmlinux_path"],
                      *task["debug_symbol_paths"]])
            roots = config["scan_roots"] + config["symbol_roots"]
            first = [(str(checked_file(p, roots)), Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in paths]
            await asyncio.sleep(config["stable_seconds"])
            second = [(str(checked_file(p, roots)), Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in paths]
            if next((t for t in store.read("tasks") if t["id"] == task_id), {}).get("status") != "queued":
                return
            if first != second or not all(stable_file(p, roots, config["stable_seconds"]) for p in paths):
                raise ValueError("文件仍在写入或发生变动，请稍后重试")
            if script_mode:
                await run_script(task, config)
                store.log("task_finished", f"{task_id} success", "system")
                return
            task.update(status="running", started_at=now(), progress="Agent 已连接", step=0)
            update_task(task)
            payload = {k: task[k] for k in ("vmcore_path", "vmcore_dmesg_path", "vmlinux_path", "debug_symbol_paths")}
            completed = False
            timeout = httpx.Timeout(connect=10, read=None, write=30, pool=10)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", config["agent_url"].rstrip("/") + "/analyze/stream", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        kind = event.get("event")
                        if kind == "start":
                            task["agent_task_id"] = event.get("task_id")
                        elif kind == "node_complete":
                            task.update(progress=event.get("node") or "分析中", step=event.get("step", 0),
                                        token_usage=event.get("token_usage", 0))
                        elif kind == "error":
                            raise RuntimeError(event.get("error") or "Agent 分析失败")
                        elif kind == "complete":
                            if event.get("error"):
                                raise RuntimeError(event["error"])
                            report = event.get("agent_answer") or ""
                            if not report:
                                raise RuntimeError("Agent 返回空报告")
                            audit = event.get("audit_report") or ""
                            task["token_usage"] = event.get("token_usage", 0)
                            finish_report(task, report, audit)
                            completed = True
                        update_task(task)
            if not completed:
                raise RuntimeError("Agent 流在完成事件前结束")
            store.log("task_finished", f"{task_id} success", "system")
        except asyncio.CancelledError:
            fail(task, "任务已停止或后台关闭", "interrupted")
            raise
        except Exception as exc:
            fail(task, exc)

    async def worker():
        while True:
            queued = sorted((t for t in store.read("tasks") if t["status"] == "queued"),
                            key=lambda t: (-t.get("priority", 0), t["created_at"]))
            if not queued:
                queue_wake.clear()
                await queue_wake.wait()
                continue
            task_id = queued[0]["id"]
            current = asyncio.create_task(run_task(task_id))
            active_runs[task_id] = current
            try:
                await current
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    current.cancel()
                    await asyncio.gather(current, return_exceptions=True)
                    raise
            finally:
                active_runs.pop(task_id, None)

    @asynccontextmanager
    async def lifespan(app):
        # Existing installations may have reports from before per-run comparisons.
        for task in store.read("tasks"):
            if task.get("report") and (not task.get("ai") or not task.get("result_fingerprint")):
                report_path = store.root / task["report"]
                if report_path.is_file():
                    report = report_path.read_text(encoding="utf-8")
                    task["ai"] = extract(report)["ai"]
                    task["result_fingerprint"] = result_fingerprint(report)
                    update_task(task)
        # No durable queue or resume API exists in Agent; make stale states explicit.
        for task in store.read("tasks"):
            if task["status"] in ("queued", "running") or (task["status"] == "paused" and task.get("started_at")):
                fail(task, "后台重启时任务未完成，请手动重试", "interrupted")
        workers.append(asyncio.create_task(worker()))
        queue_wake.set()
        yield
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    app = FastAPI(title="VMCore 管理后台", lifespan=lifespan)
    app.state.store = store
    app.state.queue = queue_wake
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def signed():
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode(), hashlib.sha256).hexdigest()
        return f"{stamp}.{signature}"

    def authorized(request):
        value = request.cookies.get("ui_session", "")
        try:
            stamp, signature = value.split(".", 1)
            expected = hmac.new(secret.encode(), stamp.encode(), hashlib.sha256).hexdigest()
            return abs(time.time() - int(stamp)) < 12 * 3600 and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/login":
            if not authorized(request):
                return JSONResponse({"detail": "请先登录"}, status_code=401)
            if request.method not in ("GET", "HEAD") and request.headers.get("x-ui-action") != "1":
                return JSONResponse({"detail": "缺少操作标记"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        if not hmac.compare_digest(str(body.get("password", "")), password):
            store.log("login_failed", request.client.host if request.client else "unknown", "anonymous")
            raise HTTPException(401, "密码错误")
        response = JSONResponse({"ok": True})
        response.set_cookie("ui_session", signed(), httponly=True, samesite="strict",
                            secure=os.environ.get("UI_SECURE_COOKIE", "0") == "1", max_age=43200)
        store.log("login", "登录成功")
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("ui_session")
        store.log("logout", "退出登录")
        return response

    @app.get("/api/summary")
    def summary():
        tasks = store.read("tasks")
        result = stats(tasks, store.read("cases"), store.read("reviews"))
        result["report_count"] = len(report_index()) + sum(
            1 for task in tasks if task.get("report") and not task.get("source_report"))
        result["manual_overrides"] = store.read("dashboard_overrides")
        return result

    @app.put("/api/dashboard-overrides")
    def save_dashboard_overrides(value: DashboardOverridesInput):
        overrides = {key: number for key, number in value.model_dump().items() if number is not None}
        store.write("dashboard_overrides", overrides)
        store.log("dashboard_overrides_updated", "概览统计手动填报已更新")
        return overrides

    @app.get("/api/tasks")
    def tasks():
        return sorted(store.read("tasks"), key=lambda t: t["created_at"], reverse=True)

    @app.get("/api/cases")
    def cases():
        tasks = store.read("tasks")
        tasks_by_case = {}
        for task in tasks:
            tasks_by_case.setdefault(task["case_id"], []).append(task)
        reviews = {r["case_id"]: r for r in store.read("reviews")}
        roots = store.read("config")["scan_roots"]
        return sorted(({**c, "collection": collection_for(c.get("vmcore_path"), roots),
                        **case_comparison(c, tasks_by_case.get(c["id"], []), reviews.get(c["id"]))}
                       for c in store.read("cases")), key=lambda c: c["created_at"], reverse=True)

    def report_index():
        config = store.read("config")
        paths = markdown_snapshot(config.get("report_roots", []))
        tasks = store.read("tasks")
        cases = {c["id"]: c for c in store.read("cases")}
        by_source = {str(Path(t["source_report"]).resolve()): t for t in tasks if t.get("source_report")}
        rows = []
        for name, (size, changed) in paths.items():
            if name.endswith(".audit.md"):
                continue
            try:
                extracted, factual = report_facts(name, size, changed)
            except OSError:
                continue
            task = by_source.get(name)
            case = cases.get(task["case_id"]) if task else next(
                (c for c in cases.values() if c.get("vmcore_path") and c["vmcore_path"] == extracted["vmcore_path"]), None)
            rows.append({"id": hashlib.sha256(name.encode()).hexdigest()[:24], "path": name,
                         "filename": Path(name).name, "modified_at": changed,
                         "task_id": task["id"] if task else None,
                         "vmcore_path": extracted["vmcore_path"] or (case or {}).get("vmcore_path"),
                         "hostname": factual["hostname"] if factual["hostname"] != UNKNOWN else (case or {}).get("hostname", UNKNOWN),
                         "dump_time": factual["dump_time"] if factual["dump_time"] != UNKNOWN else (case or {}).get("dump_time", UNKNOWN),
                         "os_name": factual["os_name"] if factual["os_name"] != UNKNOWN else (case or {}).get("os_name", UNKNOWN),
                         "kernel": factual["kernel"] if factual["kernel"] != UNKNOWN else (case or {}).get("kernel", UNKNOWN),
                         "token_usage": extracted["token_usage"]})
        return sorted(rows, key=lambda row: row["modified_at"], reverse=True)

    @app.get("/api/reports")
    def reports():
        return report_index()

    @app.get("/api/reports/{report_id}")
    def report_content(report_id: str):
        row = next((r for r in report_index() if r["id"] == report_id), None)
        if not row:
            raise HTTPException(404, "报告不存在")
        return PlainTextResponse(Path(row["path"]).read_text(encoding="utf-8", errors="replace"), media_type="text/markdown")

    @app.get("/api/collections")
    def collections():
        return inventory(store.read("config"), store.read("cases"),
                         store.read("tasks"), store.read("reviews"))

    @app.get("/api/reviews")
    def reviews():
        return store.read("reviews")

    @app.get("/api/discover")
    def discover():
        return scan(store.read("config"))

    @app.get("/api/discover/stream")
    def discover_stream():
        config = store.read("config")
        def events():
            count = 0
            folders = 0
            try:
                for event in scan_events(config):
                    if event["type"] == "directory":
                        folders += 1
                    else:
                        count += 1
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"type": "complete", "folders": folders,
                                              "vmcores": count}, ensure_ascii=False) + "\n\n"
            except Exception as exc:
                yield "data: " + json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/settings")
    def settings():
        return store.read("config")

    @app.put("/api/settings")
    def save_settings(value: SettingsInput):
        config = value.model_dump()
        if not config["agent_url"].startswith(("http://", "https://")):
            raise HTTPException(400, "Agent 地址需为 HTTP(S) URL")
        if not config["scan_roots"]:
            raise HTTPException(400, "至少配置一个扫描目录")
        if config["execution_mode"] not in ("agent", "script"):
            raise HTTPException(400, "执行方式须为 agent 或 script")
        for root in config["scan_roots"] + config["symbol_roots"] + config["report_roots"]:
            if not Path(root).is_absolute() or not Path(root).is_dir():
                raise HTTPException(400, f"目录不存在或不是绝对路径：{root}")
        if config["execution_mode"] == "script" and (not Path(config["script_path"]).is_absolute() or
                                                       not Path(config["script_path"]).is_file()):
            raise HTTPException(400, "脚本须为本机存在的绝对文件路径")
        store.write("config", config)
        store.log("settings_updated", "扫描目录、执行方式、报告目录或并发设置已更新")
        return config

    @app.get("/api/health")
    async def agent_health():
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(store.read("config")["agent_url"].rstrip("/") + "/health")
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            return JSONResponse({"status": "unavailable", "detail": str(exc)}, status_code=503)

    @app.get("/api/runtime")
    def runtime_health():
        config = store.read("config")
        if config.get("execution_mode", "agent") == "script":
            script = Path(config.get("script_path", ""))
            roots = config.get("report_roots") or []
            writable = bool(roots and all(Path(root).is_dir() and
                            os.access(root, os.R_OK | os.W_OK | os.X_OK) for root in roots))
            ready = bool(shutil.which("bash") and script.is_file() and writable)
            detail = None if writable else "报告目录对 UI 服务用户不可读写"
            return {"mode": "script", "status": "healthy" if ready else "unavailable", "detail": detail}
        return {"mode": "agent", "status": "configured"}

    async def create_task(value: TaskInput, retry_of=None):
        config = store.read("config")
        roots = config["scan_roots"] + config["symbol_roots"]
        mode = config.get("execution_mode", "agent")
        try:
            supplied = Path(value.vmcore_path)
            vmcore = checked_file(str(supplied / "vmcore" if supplied.is_dir() else supplied), config["scan_roots"])
            if vmcore.name != "vmcore":
                raise ValueError("请选择 vmcore 文件或其父目录")
            dmesg = value.vmcore_dmesg_path or next((str(vmcore.parent / name) for name in
                ("vmcore-dmesg.txt", "vmcore-dmesg") if (vmcore.parent / name).is_file()), None)
            if dmesg:
                checked_file(dmesg, config["scan_roots"])
            if mode == "script" and not dmesg:
                raise ValueError("脚本模式需要同目录的 vmcore-dmesg 文件")
            if mode == "agent":
                if not dmesg or not value.vmlinux_path:
                    raise ValueError("Agent 模式需要 dmesg 和 vmlinux")
                checked_file(value.vmlinux_path, roots)
                for path in value.debug_symbol_paths:
                    checked_file(path, roots)
        except (ValueError, OSError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if sum(t["status"] == "queued" for t in store.read("tasks")) >= 100:
            raise HTTPException(429, "任务队列已满")
        vmcore = str(vmcore.resolve())
        cid = case_id(vmcore, "")
        cases = store.read("cases")
        if not any(c["id"] == cid for c in cases):
            if dmesg:
                with open(dmesg, "r", encoding="utf-8", errors="replace") as source:
                    hints = extract("", source.read(200000))
            else:
                hints = {}
            discovered = scan_metadata(vmcore, dmesg, value.vmlinux_path)
            store.upsert("cases", {"id": cid, "vmcore_path": vmcore,
                                   "hostname": discovered.get("hostname", hints.get("hostname", UNKNOWN)),
                                   "dump_time": discovered.get("dump_time", hints.get("dump_time", UNKNOWN)),
                                   "kernel": discovered.get("kernel", hints.get("kernel", UNKNOWN)),
                                   "os_name": discovered.get("os_name", UNKNOWN),
                                   "panic": hints.get("panic", UNKNOWN),
                                   "ai": {f: UNKNOWN for f in FIELDS}, "created_at": now(),
                                   "latest_task_id": None, "imported": False})
        task = {"id": str(uuid.uuid4()), "case_id": cid, "status": "queued", "created_at": now(),
                "started_at": None, "finished_at": None, "duration_seconds": None,
                "token_usage": None if mode == "script" else 0, "progress": "排队中", "step": 0, "error": None,
                "priority": 0,
                "report": None, "audit": None, "agent_task_id": None, "retry_of": retry_of,
                **value.model_dump(), "vmcore_path": vmcore, "vmcore_dmesg_path": dmesg,
                "execution_mode": mode, "source_report": None, "source_audit": None,
                "script_log": None}
        update_task(task)
        queue_wake.set()
        store.log("task_created", f"{task['id']} {vmcore}" + (f" retry_of={retry_of}" if retry_of else ""))
        return task

    @app.post("/api/tasks")
    async def new_task(value: TaskInput):
        return await create_task(value)

    @app.post("/api/batches")
    async def create_batch(value: BatchInput):
        discovered = scan(store.read("config"))
        if value.collection and value.collection not in {item["collection"] for item in discovered}:
            raise HTTPException(404, "目录批次不存在或未发现 vmcore")
        active = {str(Path(t["vmcore_path"]).resolve()) for t in store.read("tasks")
                  if t.get("vmcore_path") and t["status"] in ("queued", "running", "success")}
        result = {"created": [], "skipped_existing": 0, "skipped_not_ready": 0,
                  "queue_full": False}
        for item in discovered:
            if value.collection and item["collection"] != value.collection:
                continue
            vmcore = str(Path(item["vmcore_path"]).resolve())
            if vmcore in active:
                result["skipped_existing"] += 1
                continue
            if not item["ready"]:
                result["skipped_not_ready"] += 1
                continue
            try:
                task = await create_task(TaskInput(**{key: item[key] for key in TaskInput.model_fields}))
            except HTTPException as exc:
                if exc.status_code == 429:
                    result["queue_full"] = True
                    break
                result["skipped_not_ready"] += 1
                continue
            result["created"].append(task["id"])
            active.add(vmcore)
        store.log("batch_created", f"collection={value.collection or 'all'} created={len(result['created'])}")
        return result

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        old = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not old:
            raise HTTPException(404, "任务不存在")
        return await create_task(TaskInput(**{k: old[k] for k in TaskInput.model_fields}), task_id)

    @app.post("/api/tasks/{task_id}/stop")
    async def stop_task(task_id: str):
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] not in ("queued", "running", "paused"):
            raise HTTPException(409, "任务已结束")
        current = active_runs.get(task_id)
        if current:
            process = active_processes.get(task_id)
            if process and task["status"] == "paused" and os.name != "nt":
                os.killpg(process.pid, signal.SIGCONT)
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)
        else:
            fail(task, "用户停止任务", "interrupted")
        store.log("task_stopped", task_id)
        return next(t for t in store.read("tasks") if t["id"] == task_id)

    @app.post("/api/tasks/{task_id}/pause")
    def pause_task(task_id: str):
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] not in ("queued", "running"):
            raise HTTPException(409, "只有排队或运行中的任务可暂停")
        if task["status"] == "running":
            process = active_processes.get(task_id)
            if not process or os.name == "nt":
                raise HTTPException(409, "当前执行方式暂不支持暂停运行中任务")
            os.killpg(process.pid, signal.SIGSTOP)
        task.update(status="paused", progress="已暂停")
        update_task(task)
        store.log("task_paused", task_id)
        return task

    @app.post("/api/tasks/{task_id}/resume")
    def resume_task(task_id: str):
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] != "paused":
            raise HTTPException(409, "任务并非暂停状态")
        process = active_processes.get(task_id)
        if process and os.name != "nt":
            os.killpg(process.pid, signal.SIGCONT)
        task.update(status="running" if process else "queued",
                    progress="脚本运行中" if process else "排队中")
        update_task(task)
        queue_wake.set()
        store.log("task_resumed", task_id)
        return task

    @app.post("/api/tasks/{task_id}/top")
    def top_task(task_id: str):
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] not in ("queued", "paused") or task.get("started_at"):
            raise HTTPException(409, "只有等待执行的任务可置顶")
        highest = max((t.get("priority", 0) for t in store.read("tasks")), default=0)
        task["priority"] = highest + 1
        update_task(task)
        queue_wake.set()
        store.log("task_topped", task_id)
        return task

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str):
        tasks = store.read("tasks")
        task = next((t for t in tasks if t["id"] == task_id), None)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] == "running" or (task["status"] == "paused" and task.get("started_at")):
            raise HTTPException(409, "请先停止运行中的任务")
        current = active_runs.get(task_id)
        if current:
            current.cancel()
            await asyncio.gather(current, return_exceptions=True)
            tasks = store.read("tasks")
            task = next(t for t in tasks if t["id"] == task_id)
        store.write("tasks", [t for t in tasks if t["id"] != task_id])
        for key in ("report", "audit", "script_log"):
            if task.get(key):
                (store.root / task[key]).unlink(missing_ok=True)
        remaining = [t for t in tasks if t["id"] != task_id and t["case_id"] == task["case_id"]]
        cases = store.read("cases")
        case = next((c for c in cases if c["id"] == task["case_id"]), None)
        if case:
            if remaining:
                latest = max(remaining, key=lambda t: t["created_at"])
                case["latest_task_id"] = latest["id"]
                successful = [t for t in remaining if t["status"] == "success"]
                case["ai"] = (max(successful, key=lambda t: t["created_at"]).get("ai", {})
                              if successful else {f: UNKNOWN for f in FIELDS})
                store.upsert("cases", case)
            else:
                store.write("cases", [c for c in cases if c["id"] != case["id"]])
                store.write("reviews", [r for r in store.read("reviews") if r["case_id"] != case["id"]])
        reviews = store.read("reviews")
        for review in reviews:
            if review.get("case_id") == task["case_id"]:
                if review.get("task_id") == task_id:
                    store.write("reviews", [r for r in reviews if r["case_id"] != task["case_id"]])
                elif task_id in review.get("reviewed_task_ids", []):
                    review["reviewed_task_ids"] = [rid for rid in review["reviewed_task_ids"] if rid != task_id]
                    store.upsert("reviews", review)
                break
        store.log("task_deleted", task_id)
        return {"deleted": task_id}

    @app.post("/api/import")
    def import_report(value: ImportInput):
        metadata = extract(value.markdown)
        cid = case_id(metadata["vmcore_path"], "import:" + hashlib.sha256(value.markdown.encode()).hexdigest())
        existing = next((c for c in store.read("cases") if c["id"] == cid), None)
        task_id = str(uuid.uuid4())
        store.atomic(store.root / "reports" / f"{task_id}.md", value.markdown)
        case = existing or {"id": cid, "created_at": now(), "imported": True, "latest_task_id": None,
                            "hostname": UNKNOWN, "dump_time": UNKNOWN, "kernel": UNKNOWN, "os_name": UNKNOWN,
                            "panic": UNKNOWN, "ai": {f: UNKNOWN for f in FIELDS}}
        for key in ("vmcore_path", "hostname", "dump_time", "kernel", "os_name", "panic"):
            if not existing or metadata[key] not in (UNKNOWN, None):
                case[key] = metadata[key]
        for key, extracted_value in metadata["ai"].items():
            if extracted_value != UNKNOWN:
                case["ai"][key] = extracted_value
        case["latest_task_id"] = task_id
        store.upsert("cases", case)
        task = {"id": task_id, "case_id": cid, "status": "success", "created_at": now(),
                "started_at": None, "finished_at": None, "duration_seconds": None,
                "token_usage": metadata["token_usage"], "progress": "历史报告已导入", "step": None,
                "error": None, "report": f"reports/{task_id}.md", "audit": None,
                "agent_task_id": None, "retry_of": None, "imported": True,
                "source_filename": Path(value.filename).name,
                "vmcore_path": metadata["vmcore_path"], "vmcore_dmesg_path": None,
                "vmlinux_path": None, "debug_symbol_paths": [],
                "ai": metadata["ai"], "result_fingerprint": result_fingerprint(value.markdown)}
        update_task(task)
        store.log("report_imported", f"{task_id} {task['source_filename']}")
        return {"case": case, "task": task}

    @app.put("/api/cases/{cid}/review")
    def save_review(cid: str, value: ReviewInput):
        if not any(c["id"] == cid for c in store.read("cases")):
            raise HTTPException(404, "案例不存在")
        if any(key not in FIELDS or verdict not in VERDICTS for key, verdict in value.verdicts.items()):
            raise HTTPException(400, "复核结果无效")
        runs = [t for t in store.read("tasks") if t["case_id"] == cid and t["status"] == "success"]
        selected = next((t for t in runs if t["id"] == value.selected_task_id), None) if value.selected_task_id else None
        if value.selected_task_id and not selected:
            raise HTTPException(400, "所选运行不属于该案例")
        if selected is None and runs:
            selected = max(runs, key=lambda t: t["created_at"])
        if not selected:
            raise HTTPException(400, "案例没有成功的分析运行")
        review = {"id": cid, "case_id": cid, "updated_at": now(),
                  "task_id": selected["id"], "ai_snapshot": selected.get("ai", {}).copy(),
                  "reviewed_task_ids": [t["id"] for t in runs],
                  **value.model_dump(exclude={"selected_task_id"})}
        store.upsert("reviews", review)
        store.log("review_saved", cid)
        return review

    @app.get("/api/tasks/{task_id}/{kind}")
    def report(task_id: str, kind: str):
        if kind not in ("report", "audit", "script_log"):
            raise HTTPException(404)
        task = next((t for t in store.read("tasks") if t["id"] == task_id), None)
        if not task or not task.get(kind):
            raise HTTPException(404, "报告不存在")
        path = store.root / task[kind]
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"),
                                 media_type="text/plain" if kind == "script_log" else "text/markdown")

    @app.get("/api/audit-log")
    def audit_log():
        return store.read("audit_log")[-200:][::-1]

    return app


app = make_app() if os.environ.get("UI_ADMIN_PASSWORD") and os.environ.get("UI_SESSION_SECRET") else None
