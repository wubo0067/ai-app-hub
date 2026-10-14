"""Whitelist-aware discovery. Paths are resolved again before each Agent call."""
import os
import re
import shutil
import time
import subprocess
import hashlib
from functools import lru_cache
from pathlib import Path

from .analysis import collection_for
from .analysis import extract
from .metadata import UNKNOWN, crash_osrelease, scan_metadata


@lru_cache(maxsize=4096)
def folder_created_at(path: str) -> float:
    stat = Path(path).stat()
    born = getattr(stat, "st_birthtime", None)
    if born:
        return born
    if os.name == "posix":
        try:
            result = subprocess.run(["stat", "-c", "%W", "--", path], capture_output=True,
                                    text=True, timeout=2, check=True)
            born = int(result.stdout.strip())
            if born > 0:
                return float(born)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return stat.st_mtime


def inside(path: str, roots: list[str]) -> bool:
    resolved = Path(path).resolve(strict=True)
    return any(resolved == Path(root).resolve(strict=True) or
               Path(root).resolve(strict=True) in resolved.parents for root in roots)


def checked_file(path: str, roots: list[str]) -> Path:
    p = Path(path).resolve(strict=True)
    if not p.is_file() or not inside(str(p), roots):
        raise ValueError(f"文件不在允许目录内：{path}")
    return p


def stable_file(path: str, roots: list[str], seconds: int) -> bool:
    p = checked_file(path, roots)
    a = p.stat()
    return a.st_size > 0 and time.time() - a.st_mtime >= seconds


def vmcore_fingerprint(path: str) -> str:
    """Sample three regions so multi-GB dumps can be compared during a scan."""
    size = Path(path).stat().st_size
    block = 1024 * 1024
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as stream:
        for offset in sorted({0, max(0, size // 2 - block // 2), max(0, size - block)}):
            stream.seek(offset)
            digest.update(stream.read(block))
    return digest.hexdigest()


def scan_signature(vmcore: str, dmesg: str) -> str:
    """Cheap change marker; an unchanged dump never needs crash or hashing again."""
    core = Path(vmcore).stat()
    log = Path(dmesg).stat()
    return f"{core.st_size}:{core.st_mtime_ns}:{log.st_size}:{log.st_mtime_ns}"


def _symbol_files(symbols: list[str]) -> list[str]:
    found = []
    for root in symbols:
        if not Path(root).is_dir():
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for name in files:
                if name == "vmlinux" or name.endswith(".ko"):
                    path = str(Path(base) / name)
                    if inside(path, symbols):
                        found.append(path)
    return found


def scan_events(config: dict, known: dict | None = None, emit_directories: bool = True):
    roots = config.get("scan_roots", [])
    symbols = config.get("symbol_roots", [])
    symbol_files = None
    seen = set()
    for root in roots:
        if not Path(root).is_dir():
            continue
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            if emit_directories:
                yield {"type": "directory", "path": base}
            if "vmcore" not in files or not any(n in files for n in ("vmcore-dmesg.txt", "vmcore-dmesg")):
                continue
            vmcore = str(Path(base) / "vmcore")
            if not inside(vmcore, roots):
                continue
            identity = str(Path(vmcore).resolve())
            if identity in seen:
                continue
            seen.add(identity)
            dmesg = next((str(Path(base) / n) for n in ("vmcore-dmesg.txt", "vmcore-dmesg")
                          if n in files and inside(str(Path(base) / n), roots)), None)
            if not dmesg:
                continue
            signature = scan_signature(vmcore, dmesg)
            cached = (known or {}).get(vmcore)
            if cached and cached.get("_scan_signature") == signature:
                item = dict(cached)
                ready_paths = [vmcore, dmesg]
                if config.get("execution_mode") != "script":
                    ready_paths += [item["vmlinux_path"], *item.get("debug_symbol_paths", [])] if item.get("vmlinux_path") else []
                item["ready"] = bool(dmesg and (config.get("execution_mode") == "script" or item.get("vmlinux_path"))
                                     and all(stable_file(path, roots + symbols, config["stable_seconds"])
                                             for path in ready_paths))
                yield {"type": "vmcore", "item": item, "cached": True}
                continue
            if symbol_files is None:
                symbol_files = _symbol_files(symbols)
            release = None
            if dmesg:
                with open(dmesg, "r", encoding="utf-8", errors="replace") as stream:
                    head = stream.read(200000)
                match = re.search(r"Linux version\s+(\S+)", head)
                release = match.group(1) if match else None
                if shutil.which("crash"):
                    try:
                        candidate = crash_osrelease(vmcore)
                        if candidate != UNKNOWN:
                            release = candidate
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            else:
                head = ""
            hints = extract("", head)
            vmlinux = next((p for p in symbol_files if Path(p).name == "vmlinux" and release and release in Path(p).parts), None)
            if not vmlinux:
                local = Path(base) / "vmlinux"
                if local.is_file() and inside(str(local), roots):
                    vmlinux = str(local)
            if not vmlinux and release:
                standard = Path("/usr/lib/debug/lib/modules") / release / "vmlinux"
                if standard.is_file():
                    vmlinux = str(standard)
            # An unmatched vmlinux requires explicit selection in the task form.
            loaded = re.findall(r"Modules linked in:\s*([^\n]+)", head)
            names = set(re.findall(r"[\w-]+", loaded[-1])) if loaded else set()
            modules = [p for p in symbol_files if p.endswith(".ko") and release and release in Path(p).parts
                       and Path(p).stem in names]
            metadata = scan_metadata(vmcore, dmesg, vmlinux)
            if config.get("execution_mode") == "script":
                ready = bool(dmesg and stable_file(vmcore, roots, config["stable_seconds"])
                             and stable_file(dmesg, roots, config["stable_seconds"]))
            else:
                ready = bool(dmesg and vmlinux and all(
                    stable_file(p, roots + symbols, config["stable_seconds"])
                    for p in (vmcore, dmesg, vmlinux, *modules)))
            try:
                fingerprint = vmcore_fingerprint(vmcore)
            except OSError:
                fingerprint = None
            yield {"type": "vmcore", "item": {"vmcore_path": vmcore, "vmcore_dmesg_path": dmesg,
                          "vmlinux_path": vmlinux, "debug_symbol_paths": modules,
                          "collection": collection_for(vmcore, roots),
                          "hostname_hint": metadata["hostname"] if metadata["hostname"] != "未知" else hints["hostname"],
                          "dump_time_hint": metadata["dump_time"] if metadata["dump_time"] != "未知" else hints["dump_time"],
                          "kernel_hint": metadata["kernel"] if metadata["kernel"] != "未知" else release or "未知",
                          "os_name_hint": metadata["os_name"],
                          "osinfo_status": metadata["osinfo_status"],
                          "osinfo_path": metadata["osinfo_path"],
                          "folder_created_at": folder_created_at(base),
                          "fingerprint": fingerprint,
                          "_scan_signature": signature,
                          "ready": ready,
                          "size_bytes": Path(vmcore).stat().st_size}}


def scan(config: dict) -> list[dict]:
    found = [event["item"] for event in scan_events(config) if event["type"] == "vmcore"]
    return sorted(found, key=lambda item: (item["folder_created_at"], item["vmcore_path"]), reverse=True)


def inventory(config: dict, cases: list[dict], tasks: list[dict], reviews: list[dict],
              discovered: list[dict] | None = None) -> list[dict]:
    """Folder-level inventory while keeping each vmcore as its own case."""
    roots = config.get("scan_roots", [])
    groups = {}
    seen = set()
    if discovered is None:
        for root in roots:
            base = Path(root)
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        groups[str(child.resolve())] = {"path": str(child.resolve()), "discovered": 0,
                            "ready": 0, "cases": 0, "runs": 0, "conflicts": 0, "pending_reviews": 0}
        paths = []
        for root in roots:
            if not Path(root).is_dir():
                continue
            for base, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
                if "vmcore" in files:
                    path = str(Path(base) / "vmcore")
                    if inside(path, roots):
                        dmesg = next((str(Path(base) / name) for name in ("vmcore-dmesg.txt", "vmcore-dmesg") if name in files), None)
                        paths.append((path, bool(dmesg and stable_file(path, roots, config["stable_seconds"])
                                                     and stable_file(dmesg, roots, config["stable_seconds"]))))
    else:
        paths = [(item["vmcore_path"], bool(item.get("ready"))) for item in discovered]
    for path, ready in paths:
        identity = str(Path(path).resolve())
        if identity in seen:
            continue
        seen.add(identity)
        key = collection_for(path, roots)
        group = groups.setdefault(key, {"path": key, "discovered": 0,
            "ready": 0, "cases": 0, "runs": 0, "conflicts": 0, "pending_reviews": 0})
        group["discovered"] += 1
        group["ready"] += int(ready)
    from .analysis import case_comparison
    review_by_case = {r["case_id"]: r for r in reviews}
    tasks_by_case = {}
    for task in tasks:
        tasks_by_case.setdefault(task["case_id"], []).append(task)
    for case in cases:
        key = collection_for(case.get("vmcore_path"), roots)
        group = groups.setdefault(key, {"path": key, "discovered": 0, "ready": 0,
            "cases": 0, "runs": 0, "conflicts": 0, "pending_reviews": 0})
        case_tasks = tasks_by_case.get(case["id"], [])
        info = case_comparison(case, case_tasks, review_by_case.get(case["id"]))
        group["cases"] += 1
        group["runs"] += len(case_tasks)
        group["conflicts"] += int(info["has_conflict"])
        group["pending_reviews"] += int(info["review_state"] != "已复核")
    return sorted((group for group in groups.values() if group["discovered"] or group["cases"]),
                  key=lambda group: (group["path"] == "未归入目录", group["path"]))
