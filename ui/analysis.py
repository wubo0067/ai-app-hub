"""Conservative metadata extraction and explicitly denominated statistics."""
import hashlib
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

UNKNOWN = "未知"
FIELDS = ("fault_type", "root_cause", "module", "evidence", "recommendation")
VERDICTS = ("正确", "部分正确", "错误", "待确认")


def incident_code(identity: str, dump_time: str | None, fallback_time: str | None = None) -> str:
    """Stable, globally usable crash code: date plus a path-derived discriminator."""
    stamp = None
    for value in (dump_time, fallback_time):
        if not value or value == UNKNOWN:
            continue
        text = str(value).strip()
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # crash sys commonly prints: Thu Mar 26 14:27:22 CST 2026.
            cleaned = re.sub(r"\s+[A-Z]{2,5}\s+(\d{4})$", r" \1", text)
            for pattern in ("%a %b %d %H:%M:%S %Y", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    stamp = datetime.strptime(cleaned, pattern)
                    break
                except ValueError:
                    pass
        if stamp:
            break
    date = stamp.strftime("%Y%m%d%H%M%S") if stamp else "00000000000000"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8].upper()
    return f"VM-{date}-{digest}"


def collection_for(vmcore_path: str | None, roots: list[str]) -> str:
    """Immediate child of the longest configured root is a collection, not a case ID."""
    if not vmcore_path:
        return "未归入目录"
    path = Path(vmcore_path).resolve()
    matches = []
    for root in roots:
        base = Path(root).resolve()
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        matches.append((len(base.parts), base, relative))
    if not matches:
        return "未归入目录"
    _, base, relative = min(matches, key=lambda item: item[0])
    return str(base / relative.parts[0]) if len(relative.parts) > 1 else str(base)


def result_fingerprint(report: str) -> str:
    summary = report.rsplit("## 总结", 1)[-1]
    summary = re.sub(r"^本次分析共执行[^\n]*$", "", summary, flags=re.M)
    normalized = " ".join(summary.split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def case_comparison(case: dict, tasks: list[dict], review: dict | None = None) -> dict:
    all_runs = [t for t in tasks if t.get("case_id") == case["id"]]
    runs = sorted((t for t in all_runs if t["status"] == "success" and not t.get("report_deleted")),
                  key=lambda t: t["created_at"])
    fingerprints = {t.get("result_fingerprint") for t in runs if t.get("result_fingerprint")}
    changed = [field for field in FIELDS if len({t.get("ai", {}).get(field, UNKNOWN) for t in runs}) > 1]
    reviewed = set(review.get("reviewed_task_ids", [])) if review else set()
    run_ids = {t["id"] for t in runs}
    if not review:
        review_state = "待复核"
    elif run_ids - reviewed:
        review_state = "新增运行待复核"
    else:
        review_state = "已复核"
    return {"run_count": len(all_runs), "variant_count": len(fingerprints),
            "conflict_fields": changed, "has_conflict": len(fingerprints) > 1 or bool(changed),
            "review_state": review_state}


def case_id(vmcore_path: str | None, fallback: str) -> str:
    identity = str(Path(vmcore_path).resolve()) if vmcore_path else fallback
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def _first(pattern, source, flags=re.I | re.M):
    match = re.search(pattern, source, flags)
    return match.group(1).strip().strip('"`') if match else UNKNOWN


def _last(pattern, source):
    matches = re.findall(pattern, source, re.I | re.M)
    return matches[-1].strip().strip('"`') if matches else UNKNOWN


def extract(report: str, dmesg: str = "") -> dict:
    # Labels from the Agent's generated report and its embedded `sys` output.
    vmcore = _first(r"^[-*]?\s*\*\*vmcore 路径\*\*:\s*`?([^`\n]+)", report)
    source = report[:200000] + "\n" + dmesg[:200000]
    # vmcore-dmesg has no guaranteed wall clock. Only explicit DATE lines count.
    dump_time = _first(r"^\s*DATE:\s*(.+)$", dmesg) if dmesg else UNKNOWN
    if dump_time == UNKNOWN:
        dump_time = _first(r"^\s*DATE:\s*(.+)$", report)
    ai = {
        "fault_type": _last(r"\*\*(?:初步根因分类|最终根因类|故障类型)\*\*:\s*([^\n]+)", report[-80000:]),
        "root_cause": _first(r"\*\*(?:根因|Root Cause)\*\*:\s*([^\n]+)", report[-80000:]),
        "module": _first(r"\*\*(?:责任模块|Responsible Module)\*\*:\s*([^\n]+)", report[-80000:]),
        "evidence": _first(r"\*\*(?:关键证据|Key Evidence)\*\*:\s*([^\n]+)", report[-80000:]),
        "recommendation": _first(r"\*\*(?:修复建议|Recommendations?)\*\*:\s*([^\n]+)", report[-80000:]),
    }
    token_text = _first(r"^[-*]?\s*\*\*Token 使用量\*\*:\s*([\d,]+)", report)
    if token_text == UNKNOWN:
        token_text = _first(r"使用了\s*([\d,]+)\s*个\s*Token", report)
    token_usage = int(token_text.replace(",", "")) if token_text != UNKNOWN else None
    return {
        "vmcore_path": None if vmcore == UNKNOWN else vmcore,
        "hostname": _first(r"^\s*NODENAME:\s*(\S+)", source),
        "dump_time": dump_time,
        "kernel": _first(r"^\s*RELEASE:\s*(\S+)", source),
        "os_name": _first(r"^\s*(?:OS|OS NAME|操作系统):\s*(.+)$", source),
        "panic": _first(r"^\s*PANIC:\s*(.+)$", source),
        "token_usage": token_usage,
        "ai": ai,
    }


def stats(tasks, cases, reviews, comparison_tasks=None):
    tasks_by_case = {}
    for task in comparison_tasks if comparison_tasks is not None else tasks:
        tasks_by_case.setdefault(task["case_id"], []).append(task)
    executed = [t for t in tasks if not t.get("imported")]
    completed = [t for t in executed if t["status"] in ("success", "failed")]
    successful = [t for t in executed if t["status"] == "success"]
    success_by_case = {}
    for task in (comparison_tasks if comparison_tasks is not None else successful):
        if task["status"] != "success" or task.get("report_deleted"):
            continue
        success_by_case.setdefault(task["case_id"], []).append(task)
    durations = [t["duration_seconds"] for t in successful if t.get("duration_seconds") is not None]
    human = [r["human_seconds"] for r in reviews if r.get("human_seconds") is not None]
    savings = []
    case_by_id = {c["id"]: c for c in cases}
    eligible_reviews = [r for r in reviews if case_by_id.get(r.get("case_id")) and
                        case_comparison(case_by_id[r["case_id"]], tasks_by_case.get(r["case_id"], []), r)["review_state"] == "已复核"]
    for r in reviews:
        c = case_by_id.get(r["case_id"], {})
        runs = success_by_case.get(c.get("id"), [])
        if r.get("human_seconds") is not None and runs:
            latest = (next((t for t in runs if t.get("id") == r["task_id"]), None)
                      if r.get("task_id") else max(runs, key=lambda t: t["created_at"]))
            if latest is None:
                continue
            if latest.get("duration_seconds") is not None:
                savings.append(r["human_seconds"] - latest["duration_seconds"])
    accuracy = {}
    for field in FIELDS:
        values = [r.get("verdicts", {}).get(field) for r in eligible_reviews
                  if r.get("ai_snapshot", case_by_id.get(r.get("case_id"), {}).get("ai", {})).get(field, UNKNOWN) != UNKNOWN]
        assessed = [v for v in values if v in VERDICTS[:3]]
        accuracy[field] = {"reviewed": len(assessed),
                           "correct": assessed.count("正确"),
                           "partial": assessed.count("部分正确"),
                           "wrong": assessed.count("错误"),
                           "rate": round(100 * assessed.count("正确") / len(assessed), 1) if assessed else None}
    review_by_case = {r["case_id"]: r for r in eligible_reviews}
    types = Counter(
        (review_by_case.get(c["id"], {}).get("human", {}).get("fault_type")
         or c.get("ai", {}).get("fault_type", UNKNOWN))
        for c in cases if success_by_case.get(c["id"])
    )
    types.pop(UNKNOWN, None)
    review_by_case = {r["case_id"]: r for r in reviews}
    conflicts = sum(case_comparison(c, tasks_by_case.get(c["id"], []), review_by_case.get(c["id"]))["has_conflict"] for c in cases)
    pending = sum(case_comparison(c, tasks_by_case.get(c["id"], []), review_by_case.get(c["id"]))["review_state"] != "已复核" for c in cases)
    return {"tasks": len(tasks), "completed": len(completed), "success_rate":
            round(100 * len(successful) / len(completed), 1) if completed else None,
            "avg_analysis_seconds": round(sum(durations) / len(durations)) if durations else None,
            "avg_human_seconds": round(sum(human) / len(human)) if human else None,
            "avg_saved_seconds": round(sum(savings) / len(savings)) if savings else None,
            "savings_samples": len(savings), "accuracy": accuracy,
            "fault_types": types.most_common(8), "cases": len(cases),
            "conflict_cases": conflicts, "pending_reviews": pending}
