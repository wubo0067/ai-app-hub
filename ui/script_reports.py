"""Identify files produced by one script run without guessing from timestamps alone."""
from pathlib import Path

from .analysis import extract


def markdown_snapshot(roots: list[str]) -> dict[str, tuple[int, int]]:
    found = {}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                found[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
    return found


def produced_reports(before: dict, after: dict, vmcore_path: str) -> tuple[Path, Path | None]:
    changed = [Path(path) for path, signature in after.items() if before.get(path) != signature]
    mains = [path for path in changed if not path.name.endswith(".audit.md")]
    if not mains:
        raise ValueError("脚本结束后没有发现新增或更新的 Markdown 报告；请配置报告输出目录")
    target = str(Path(vmcore_path).resolve())
    matches = []
    identified = []
    for path in mains:
        metadata = extract(path.read_text(encoding="utf-8", errors="replace"))
        if metadata["vmcore_path"]:
            identified.append(path)
            if str(Path(metadata["vmcore_path"]).resolve()) == target:
                matches.append(path)
    if identified and not matches:
        raise ValueError("新生成报告中的 vmcore 路径与本任务不符")
    candidates = matches or mains
    if len(candidates) != 1:
        raise ValueError("本次脚本产生多份 Markdown，无法确定任务报告；请缩小报告输出目录或让报告写入 vmcore 路径")
    report = candidates[0]
    audit = report.with_name(report.stem + ".audit.md")
    return report, audit if audit in changed else None
