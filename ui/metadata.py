"""Crash metadata and a validated, atomic osinfo.txt cache beside each vmcore."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

UNKNOWN = "未知"
OSINFO_VERSION = "1"
FIELDS = ("hostname", "os_name", "kernel", "vmlinux_path", "dump_time")


def _field(text: str, name: str) -> str:
    match = re.search(r"^\s*" + re.escape(name) + r":\s*(.+)$", text, re.I | re.M)
    return match.group(1).strip().strip('"') if match else UNKNOWN


def report_metadata(text: str) -> dict:
    return {
        "hostname": _field(text, "NODENAME"),
        "dump_time": _field(text, "DATE"),
        "kernel": _field(text, "RELEASE"),
        "os_name": next((value for value in (_field(text, "OS"), _field(text, "OS NAME"),
                                            _field(text, "操作系统")) if value != UNKNOWN), UNKNOWN),
    }


def _signature(path: str | None) -> str:
    if not path:
        return "none"
    stat = Path(path).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _read_osinfo(path: Path, signatures: dict, require_crash: bool) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = dict(line.split(": ", 1) for line in lines)
    except (OSError, UnicodeError, ValueError):
        return None
    if any(values.get(key) != value for key, value in signatures.items()):
        return None
    if any(not values.get(key) for key in FIELDS):
        return None
    if values.get("source") not in ("crash", "crash-osrelease", "dmesg"):
        return None
    if require_crash and values.get("source") == "dmesg":
        return None
    if values.get("hostname") != UNKNOWN and not re.fullmatch(r"[\w.-]{1,253}", values["hostname"]):
        return None
    if values.get("kernel") == UNKNOWN or not re.search(r"\d", values["kernel"]):
        return None
    if values.get("dump_time") != UNKNOWN and not re.search(r"\b(?:19|20)\d{2}\b", values["dump_time"]):
        return None
    return values


def _write_osinfo(path: Path, values: dict) -> None:
    content = "\n".join(f"{key}: {str(value).replace(chr(10), ' ').replace(chr(13), ' ')}"
                        for key, value in values.items()) + "\n"
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".osinfo-", suffix=".tmp", delete=False) as stream:
            name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _dmesg_facts(path: str | None) -> dict:
    facts = {key: UNKNOWN for key in FIELDS}
    if not path:
        return facts
    text = Path(path).read_text(encoding="utf-8", errors="replace")[:300000]
    facts.update(report_metadata(text))
    if facts["kernel"] == UNKNOWN:
        match = re.search(r"Linux version\s+(\S+)", text)
        if match:
            facts["kernel"] = match.group(1)
    if facts["os_name"] == UNKNOWN:
        match = re.search(r"\b(Red Hat Enterprise Linux|CentOS(?: Linux| Stream)?|Ubuntu|SUSE Linux Enterprise|openEuler|Kylin Linux)\b[^\n]{0,65}", text, re.I)
        if match:
            facts["os_name"] = match.group(0).strip()
    return facts


def _crash_sys(vmcore: str, vmlinux: str) -> dict:
    result = subprocess.run(["crash", vmlinux, vmcore], input="sys\nquit\n", text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                            check=False)
    if result.returncode != 0:
        return {}
    facts = report_metadata(result.stdout)
    return facts if any(facts[key] != UNKNOWN for key in ("hostname", "kernel", "dump_time")) else {}


def crash_osrelease(vmcore: str) -> str:
    """Read the kernel release from vmcore without a matching vmlinux."""
    result = subprocess.run(["crash", "--osrelease", vmcore], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=30, check=False)
    if result.returncode:
        return UNKNOWN
    return next((line.strip() for line in result.stdout.splitlines()
                 if re.fullmatch(r"\d+\.\d+\.\d+[^\s]*", line.strip())), UNKNOWN)


def scan_metadata(vmcore: str, dmesg: str | None, vmlinux: str | None) -> dict:
    """Read a valid cache, otherwise inspect and refresh it. Unknown OS is explicit."""
    path = Path(vmcore).parent / "osinfo.txt"
    try:
        signatures = {"version": OSINFO_VERSION, "vmcore_path": str(Path(vmcore).resolve()),
                      "vmcore_signature": _signature(vmcore), "dmesg_signature": _signature(dmesg),
                      "vmlinux_path": str(Path(vmlinux).resolve()) if vmlinux else UNKNOWN,
                      "vmlinux_signature": _signature(vmlinux)}
        available = bool(shutil.which("crash"))
        cached = _read_osinfo(path, signatures, available)
        if cached:
            status = "已校验缓存"
            if cached["source"] == "crash-osrelease" and not vmlinux:
                status += "；未匹配 vmlinux，无法执行 crash sys"
            return {**{key: cached[key] for key in FIELDS}, "osinfo_status": status, "osinfo_path": str(path)}
        facts = _dmesg_facts(dmesg)
        facts["vmlinux_path"] = signatures["vmlinux_path"]
        source = "dmesg"
        error = None
        if available and vmlinux:
            try:
                crash = _crash_sys(vmcore, vmlinux)
                if crash:
                    facts.update({key: value for key, value in crash.items() if value != UNKNOWN})
                    source = "crash"
                else:
                    error = "crash 未返回有效 sys 信息"
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = f"crash 执行失败：{exc}"
        if available and source != "crash":
            try:
                release = crash_osrelease(vmcore)
                if release != UNKNOWN:
                    facts["kernel"] = release
                    source = "crash-osrelease"
                    error = None
            except (OSError, subprocess.TimeoutExpired) as exc:
                error = f"crash --osrelease 执行失败：{exc}"
        elif not available and vmlinux:
            error = "未安装 crash 命令"
        elif not available:
            error = "未匹配 vmlinux"
        if available and not vmlinux and (facts["hostname"] == UNKNOWN or facts["dump_time"] == UNKNOWN):
            error = "未匹配 vmlinux，无法执行 crash sys"
        values = {**signatures, **facts, "source": source}
        try:
            _write_osinfo(path, values)
            complete = all(facts[key] != UNKNOWN for key in ("hostname", "kernel", "dump_time"))
            status = "已写入 osinfo.txt" if complete else "已写入 osinfo.txt；字段待补全"
            if error:
                status += f"；{error}"
        except OSError as exc:
            status = f"osinfo.txt 写入失败：{exc}"
        return {**facts, "osinfo_status": status, "osinfo_path": str(path)}
    except OSError as exc:
        return {**{key: UNKNOWN for key in FIELDS},
                "osinfo_status": f"元数据读取失败：{exc}", "osinfo_path": str(path)}
