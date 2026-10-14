import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ui.analysis import UNKNOWN, case_id, collection_for, extract, stats
from ui.app import make_app
from ui.storage import Store
from ui.script_reports import markdown_snapshot, produced_reports


HEADERS = {"X-UI-Action": "1"}


def client_for(tmp_path):
    app = make_app(tmp_path / "data", "test-password", "test-session-secret-at-least-24-chars")
    return TestClient(app)


def login(client):
    assert client.post("/api/login", json={"password": "test-password"}).status_code == 200


def configure(client, tmp_path, stable=1):
    root = tmp_path / "dumps"
    root.mkdir(exist_ok=True)
    value = {"agent_url": "http://agent.example", "scan_roots": [str(root)],
             "symbol_roots": [str(root)], "stable_seconds": stable, "max_parallel": 1}
    assert client.put("/api/settings", json=value, headers=HEADERS).status_code == 200
    return root


def files(root):
    paths = {name: root / name for name in ("vmcore", "vmcore-dmesg.txt", "vmlinux")}
    for name, path in paths.items():
        path.write_text("Linux version 6.1.0 test\n" if "dmesg" in name else "ready", encoding="utf-8")
        old = time.time() - 60
        os.utime(path, (old, old))
    return {"vmcore_path": str(paths["vmcore"]), "vmcore_dmesg_path": str(paths["vmcore-dmesg.txt"]),
            "vmlinux_path": str(paths["vmlinux"]), "debug_symbol_paths": []}


def test_atomic_store_and_restart_recovery(tmp_path):
    store = Store(tmp_path / "data")
    store.write("tasks", [{"id": "one", "status": "running", "started_at": None, "created_at": "2026-01-01"},
                          {"id": "two", "status": "queued", "started_at": None, "created_at": "2026-01-02"}])
    original_replace = os.replace
    def broken(*args):
        raise OSError("interrupted replace")
    os.replace = broken
    try:
        with pytest.raises(OSError):
            store.write("tasks", [])
    finally:
        os.replace = original_replace
    assert len(store.read("tasks")) == 2
    with client_for(tmp_path) as client:
        login(client)
        statuses = {t["status"] for t in client.get("/api/tasks").json()}
        assert statuses == {"interrupted"}
        assert all("重启" in t["error"] for t in client.get("/api/tasks").json())


def test_history_import_groups_vmcore_and_does_not_infer_human_time(tmp_path):
    report = '# VMCore 分析报告\n- **vmcore 路径**: `/var/crash/a/vmcore`\nNODENAME: host1\nRELEASE: 6.1\nPANIC: "Oops"\n'
    with client_for(tmp_path) as client:
        login(client)
        a = client.post("/api/import", json={"filename": "a.md", "markdown": report}, headers=HEADERS).json()
        b = client.post("/api/import", json={"filename": "b.md", "markdown": report + '\nsecond run'}, headers=HEADERS).json()
        assert a["case"]["id"] == b["case"]["id"]
        assert len(client.get("/api/cases").json()) == 1
        tasks = client.get("/api/tasks").json()
        assert len(tasks) == 2 and all(t["duration_seconds"] is None for t in tasks)
        assert client.get("/api/reviews").json() == []
        assert client.get("/api/summary").json()["success_rate"] is None
        unknown = client.post("/api/import", json={"filename": "legacy.md", "markdown": "# Old analysis"}, headers=HEADERS).json()
        assert unknown["case"]["hostname"] == UNKNOWN
        assert unknown["case"]["dump_time"] == UNKNOWN
        assert unknown["case"]["vmcore_path"] is None


def test_stats_denominators_and_savings():
    cases = [{"id": "a", "ai": {"fault_type": "死锁", "root_cause": UNKNOWN}},
             {"id": "b", "ai": {"fault_type": "OOM", "root_cause": UNKNOWN}}]
    tasks = [{"id": "run-a", "case_id": "a", "status": "success", "duration_seconds": 60, "created_at": "2026-01-01"},
             {"id": "run-b", "case_id": "b", "status": "failed", "duration_seconds": None, "created_at": "2026-01-02"},
             {"id": "run-c", "case_id": "b", "status": "success", "duration_seconds": None, "created_at": "2026-01-03", "imported": True}]
    reviews = [{"case_id": "a", "human_seconds": 300, "reviewed_task_ids": ["run-a"], "verdicts": {"fault_type": "正确", "root_cause": "正确"}},
               {"case_id": "b", "human_seconds": None, "verdicts": {"fault_type": "待确认"}}]
    result = stats(tasks, cases, reviews)
    assert result["success_rate"] == 50
    assert result["accuracy"]["fault_type"]["reviewed"] == 1
    assert result["accuracy"]["fault_type"]["rate"] == 100
    assert result["accuracy"]["root_cause"]["reviewed"] == 0
    assert result["avg_saved_seconds"] == 240 and result["savings_samples"] == 1


def test_dashboard_overrides_are_labeled_separately_and_survive_restart(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        baseline = client.get("/api/summary").json()
        assert baseline["manual_overrides"] == {}
        assert client.put("/api/dashboard-overrides", json={"success_rate": 120}, headers=HEADERS).status_code == 422
        response = client.put("/api/dashboard-overrides", json={"success_rate": 82.5,
            "avg_analysis_seconds": 300, "avg_human_seconds": 900,
            "avg_saved_seconds": -60}, headers=HEADERS)
        assert response.status_code == 200
        result = client.get("/api/summary").json()
        assert result["success_rate"] is None
        assert result["manual_overrides"]["success_rate"] == 82.5
        assert result["manual_overrides"]["avg_saved_seconds"] == -60
        assert any(row["action"] == "dashboard_overrides_updated" for row in client.get("/api/audit-log").json())
    with client_for(tmp_path) as client:
        login(client)
        assert client.get("/api/summary").json()["manual_overrides"]["avg_human_seconds"] == 900
        assert client.put("/api/dashboard-overrides", json={}, headers=HEADERS).status_code == 200
        assert client.get("/api/summary").json()["manual_overrides"] == {}


def test_report_count_does_not_count_script_archive_twice(tmp_path):
    reports = tmp_path / "external-reports"
    reports.mkdir()
    external = reports / "result.md"
    external.write_text("# Analysis\n", encoding="utf-8")
    with client_for(tmp_path) as client:
        login(client)
        store = client.app.state.store
        config = store.read("config")
        config["report_roots"] = [str(reports)]
        store.write("config", config)
        store.write("tasks", [
            {"id": "script", "case_id": "case", "status": "success", "created_at": "2026-01-01",
             "report": "reports/script.md", "source_report": str(external)},
            {"id": "import", "case_id": "case", "status": "success", "created_at": "2026-01-02",
             "report": "reports/import.md", "source_report": None},
        ])
        assert client.get("/api/summary").json()["report_count"] == 2


def test_discovery_whitelist_and_stream_task(tmp_path, monkeypatch):
    from ui import app as module
    class FakeStream:
        def __init__(self): self.status_code = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def aiter_lines(self):
            for event in ({"event":"start","task_id":"agent-1"},
                          {"event":"node_complete","node":"crash_tool_node","step":2,"token_usage":42},
                          {"event":"complete","agent_answer":"# Report\nNODENAME: host1\nRELEASE: 6.1\nPANIC: Oops\n**故障类型**: 死锁", "audit_report":"# Audit", "token_usage":54}):
                yield "data: " + json.dumps(event)
    class FakeClient:
        def __init__(self,*args,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        def stream(self,method,url,json):
            assert url == "http://agent.example/analyze/stream"
            assert set(json) == {"vmcore_path","vmcore_dmesg_path","vmlinux_path","debug_symbol_paths"}
            return FakeStream()
    monkeypatch.setattr(module.httpx,"AsyncClient",FakeClient)
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client,tmp_path)
        payload = files(root)
        discovered = client.get("/api/discover").json()
        assert discovered[0]["vmcore_path"] == payload["vmcore_path"]
        outside = tmp_path / "outside"
        outside.write_text("x")
        invalid = dict(payload, vmcore_path=str(outside))
        assert client.post("/api/tasks",json=invalid,headers=HEADERS).status_code == 400
        task = client.post("/api/tasks",json=payload,headers=HEADERS).json()
        assert task["status"] == "queued"
        # Worker waits the configured stability interval before making the HTTP call.
        for _ in range(40):
            current = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
            if current["status"] == "success": break
            time.sleep(.1)
        assert current["status"] == "success", current
        assert current["token_usage"] == 54 and current["step"] == 2
        assert client.get(f"/api/tasks/{task['id']}/report").status_code == 200
        assert client.get(f"/api/tasks/{task['id']}/audit").text == "# Audit"
        case = client.get("/api/cases").json()[0]
        assert case["hostname"] == "host1"
        assert client.post(f"/api/tasks/{task['id']}/retry", headers=HEADERS).json()["case_id"] == case["id"]


def test_folder_inventory_vmcore_identity_conflicts_and_re_review(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path)
        batch = root / "batch-001"
        one = batch / "same-host" / "dump-1" / "vmcore"
        two = batch / "same-host" / "dump-2" / "vmcore"
        for path in (one, two):
            path.parent.mkdir(parents=True)
            path.write_text("ready")
            dmesg = path.parent / "vmcore-dmesg.txt"
            dmesg.write_text("Linux version 6.1.0\n")
            old = time.time() - 60
            for file in (path, dmesg):
                os.utime(file, (old, old))
        assert collection_for(str(one), [str(root)]) == str(batch.resolve())
        assert case_id(str(one), "") != case_id(str(two), "")
        def report(path, fault):
            return f"# VMCore 分析报告\n- **vmcore 路径**: `{path}`\nNODENAME: same-host\n## 总结\n**故障类型**: {fault}\n"
        def imported(path, fault):
            response = client.post("/api/import", json={"filename": "old.md", "markdown": report(path, fault)}, headers=HEADERS)
            assert response.status_code == 200, response.text
            return response.json()["task"]["id"]
        first = imported(one, "死锁")
        imported(one, "内存损坏")
        imported(two, "死锁")
        cases = client.get("/api/cases").json()
        assert len(cases) == 2
        one_case = next(c for c in cases if c["vmcore_path"] == str(one))
        assert one_case["run_count"] == 2
        assert one_case["variant_count"] == 2
        assert one_case["conflict_fields"] == ["fault_type"]
        assert one_case["has_conflict"] and one_case["review_state"] == "待复核"
        folders = client.get("/api/collections").json()
        group = next(g for g in folders if g["path"] == str(batch.resolve()))
        assert (group["discovered"], group["cases"], group["runs"], group["conflicts"]) == (2, 2, 3, 1)
        review = client.put(f"/api/cases/{one_case['id']}/review", json={
            "selected_task_id": first, "human": {"fault_type": "死锁"},
            "verdicts": {"fault_type": "正确"}, "human_seconds": 120}, headers=HEADERS)
        assert review.status_code == 200
        assert review.json()["task_id"] == first
        reviewed_case = next(c for c in client.get("/api/cases").json() if c["id"] == one_case["id"])
        assert reviewed_case["review_state"] == "已复核"
        assert client.get("/api/summary").json()["accuracy"]["fault_type"]["reviewed"] == 1
        imported(one, "驱动错误")
        latest = next(c for c in client.get("/api/cases").json() if c["id"] == one_case["id"])
        assert latest["review_state"] == "新增运行待复核"
        assert client.get("/api/summary").json()["accuracy"]["fault_type"]["reviewed"] == 0
        assert client.get("/api/summary").json()["conflict_cases"] == 1


def test_batch_creation_only_ready_unprocessed_vmcores(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path)
        symbol = root / "6.1.0" / "vmlinux"
        symbol.parent.mkdir()
        symbol.write_text("symbols")
        for index in (1, 2):
            folder = root / "batch-A" / f"host-{index}"
            folder.mkdir(parents=True)
            (folder / "vmcore").write_text("ready")
            (folder / "vmcore-dmesg.txt").write_text("Linux version 6.1.0\n")
            for file in folder.iterdir():
                os.utime(file, (time.time() - 60, time.time() - 60))
        os.utime(symbol, (time.time() - 60, time.time() - 60))
        collection = str((root / "batch-A").resolve())
        result = client.post("/api/batches", json={"collection": collection}, headers=HEADERS)
        assert result.status_code == 200, result.text
        assert len(result.json()["created"]) == 2
        again = client.post("/api/batches", json={"collection": collection}, headers=HEADERS).json()
        assert again["created"] == [] and again["skipped_existing"] == 2
        assert len(client.get("/api/cases").json()) == 2


def test_script_report_attribution_and_ambiguity(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    vmcore = tmp_path / "dump" / "vmcore"
    vmcore.parent.mkdir()
    vmcore.write_text("core")
    before = markdown_snapshot([str(root)])
    report = root / "run-01.md"
    report.write_text(f"# Report\n- **vmcore 路径**: `{vmcore}`\n**故障类型**: OOM", encoding="utf-8")
    audit = root / "run-01.audit.md"
    audit.write_text("# Audit")
    found, paired_audit = produced_reports(before, markdown_snapshot([str(root)]), str(vmcore))
    assert found == report and paired_audit == audit
    other = root / "wrong.md"
    other.write_text("# Report\n- **vmcore 路径**: `/other/vmcore`", encoding="utf-8")
    with pytest.raises(ValueError, match="路径与本任务不符"):
        produced_reports(markdown_snapshot([str(root)]) | {str(other.resolve()): (0, 0)},
                         markdown_snapshot([str(root)]), str(vmcore))
    before = markdown_snapshot([str(root)])
    (root / "another.md").write_text("# No identity")
    (root / "also.md").write_text("# No identity")
    with pytest.raises(ValueError, match="多份"):
        produced_reports(before, markdown_snapshot([str(root)]), str(vmcore))


def test_stop_queued_or_running_task(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path, stable=3)
        payload = files(root)
        task = client.post("/api/tasks", json=payload, headers=HEADERS).json()
        stopped = client.post(f"/api/tasks/{task['id']}/stop", headers=HEADERS)
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "interrupted"
        assert client.post(f"/api/tasks/{task['id']}/stop", headers=HEADERS).status_code == 409


def test_script_mode_captures_the_new_report_for_its_vmcore(tmp_path, monkeypatch):
    from ui import app as module
    root = tmp_path / "dumps"
    folder = root / "host-a"
    folder.mkdir(parents=True)
    (folder / "vmcore").write_text("core")
    (folder / "vmcore-dmesg.txt").write_text("DATE: Thu Mar 26 14:27:22 CST 2026\nNODENAME: host-a\n")
    old = time.time() - 60
    os.utime(folder / "vmcore", (old, old))
    reports = tmp_path / "reports"
    reports.mkdir()
    script = tmp_path / "analytics_vmcore.sh"
    script.write_text("#!/bin/bash\n")
    class Output:
        lines = iter((b"starting analysis\n", b"final report ready\n", b""))
        async def readline(self): return next(self.lines)
    class Process:
        returncode = 0
        stdout = Output()
        async def wait(self): return 0
    async def fake_exec(program, script_path, parent, **kwargs):
        assert (program, script_path, parent) == ("bash", str(script), "host-a")
        (reports / "new.md").write_text(f"# Report\n- **vmcore 路径**: `{folder / 'vmcore'}`\n**故障类型**: OOM", encoding="utf-8")
        (reports / "new.audit.md").write_text("# Audit")
        return Process()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_exec)
    with client_for(tmp_path) as client:
        login(client)
        config = {"agent_url": "http://agent.example", "scan_roots": [str(root)],
                  "symbol_roots": [], "stable_seconds": 1, "max_parallel": 1,
                  "execution_mode": "script", "script_path": str(script), "report_roots": [str(reports)]}
        assert client.put("/api/settings", json=config, headers=HEADERS).status_code == 200
        task = client.post("/api/tasks", json={"vmcore_path": str(folder)}, headers=HEADERS).json()
        for _ in range(40):
            current = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
            if current["status"] == "success": break
            time.sleep(.1)
        assert current["status"] == "success", current
        assert current["source_report"] == str(reports / "new.md")
        assert current["source_audit"] == str(reports / "new.audit.md")
        assert current["token_usage"] is None
        assert client.get(f"/api/tasks/{task['id']}/report").text.startswith("# Report")
        assert client.get(f"/api/tasks/{task['id']}/script_log").text == "starting analysis\nfinal report ready\n"
        assert (tmp_path / "data" / current["script_log"]).read_text() == "starting analysis\nfinal report ready\n"
        assert client.get("/api/cases").json()[0]["dump_time"] == "Thu Mar 26 14:27:22 CST 2026"


def test_failed_script_retains_full_output(tmp_path, monkeypatch):
    from ui import app as module
    folder = tmp_path / "dumps" / "host-a"
    folder.mkdir(parents=True)
    (folder / "vmcore").write_text("core")
    (folder / "vmcore-dmesg.txt").write_text("NODENAME: host-a\n")
    os.utime(folder / "vmcore", (time.time() - 60, time.time() - 60))
    reports = tmp_path / "reports"
    reports.mkdir()
    script = tmp_path / "analysis.sh"
    script.write_text("#!/bin/bash\n")
    class Output:
        lines = iter((b"first\n", b"fatal detail\n", b""))
        async def readline(self): return next(self.lines)
    class Process:
        returncode = 7
        stdout = Output()
        async def wait(self): return 7
    async def fake_exec(*args, **kwargs): return Process()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_exec)
    with client_for(tmp_path) as client:
        login(client)
        config = {"agent_url": "http://agent.example", "scan_roots": [str(folder.parent)],
                  "symbol_roots": [], "stable_seconds": 1, "max_parallel": 1,
                  "execution_mode": "script", "script_path": str(script), "report_roots": [str(reports)]}
        assert client.put("/api/settings", json=config, headers=HEADERS).status_code == 200
        task = client.post("/api/tasks", json={"vmcore_path": str(folder)}, headers=HEADERS).json()
        for _ in range(40):
            current = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
            if current["status"] == "failed": break
            time.sleep(.1)
        assert current["status"] == "failed"
        assert client.get(f"/api/tasks/{task['id']}/script_log").text == "first\nfatal detail\n"


def test_nested_discovery_and_relative_script_argument(tmp_path, monkeypatch):
    from ui import app as module
    root = tmp_path / "dumps"
    nested = root / "127.0.0.1" / "file"
    nested.mkdir(parents=True)
    vmcore = nested / "vmcore"
    vmcore.write_text("core")
    (nested / "vmcore-dmesg.txt").write_text("NODENAME: host-a\nLinux version 6.1.0\n")
    old = time.time() - 60
    os.utime(vmcore, (old, old))
    newer = root / "newer"
    newer.mkdir()
    (newer / "vmcore").write_text("core")
    (newer / "vmcore-dmesg.txt").write_text("NODENAME: host-b\n")
    os.utime(newer / "vmcore", (old, old))
    reports = tmp_path / "reports"
    reports.mkdir()
    script = tmp_path / "analytics_vmcore.sh"
    script.write_text("#!/bin/bash\n")
    class Process:
        returncode = 0
        class Output:
            async def readline(self): return b""
        stdout = Output()
        async def wait(self): return 0
    async def fake_exec(program, script_path, argument, **kwargs):
        assert (program, script_path, argument) == ("bash", str(script), "127.0.0.1/file")
        (reports / "new.md").write_text(
            f"# Report\n- **vmcore 路径**: `{vmcore}`\nNODENAME: host-a\nRELEASE: 6.1.0\n- **Token 使用量**: 1234\n",
            encoding="utf-8")
        return Process()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_exec)
    with client_for(tmp_path) as client:
        login(client)
        config = {"agent_url": "http://agent.example", "scan_roots": [str(root)],
                  "symbol_roots": [], "stable_seconds": 1, "max_parallel": 1,
                  "execution_mode": "script", "script_path": str(script), "report_roots": [str(reports)]}
        assert client.put("/api/settings", json=config, headers=HEADERS).status_code == 200
        discovered = client.get("/api/discover").json()
        assert [d["vmcore_path"] for d in discovered] == [str(newer / "vmcore"), str(vmcore)]
        assert discovered[1]["hostname_hint"] == "host-a"
        task = client.post("/api/tasks", json={"vmcore_path": str(nested)}, headers=HEADERS).json()
        for _ in range(40):
            current = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
            if current["status"] == "success": break
            time.sleep(.1)
        assert current["status"] == "success", current
        assert current["token_usage"] == 1234
        rows = client.get("/api/reports").json()
        assert len(rows) == 1 and rows[0]["hostname"] == "host-a" and rows[0]["task_id"] == task["id"]
        assert "Token 使用量" in client.get("/api/reports/" + rows[0]["id"]).text


def test_queue_pause_top_stop_delete_and_single_worker(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path, stable=3)
        first = files(root)
        one = client.post("/api/tasks", json=first, headers=HEADERS).json()
        folder = root / "second"
        folder.mkdir()
        second = files(folder)
        two = client.post("/api/tasks", json=second, headers=HEADERS).json()
        assert client.put("/api/settings", json={**client.get("/api/settings").json(), "max_parallel": 2}, headers=HEADERS).status_code == 422
        assert client.post(f"/api/tasks/{two['id']}/top", headers=HEADERS).json()["priority"] == 1
        assert client.post(f"/api/tasks/{two['id']}/pause", headers=HEADERS).json()["status"] == "paused"
        assert client.post(f"/api/tasks/{two['id']}/resume", headers=HEADERS).json()["status"] == "queued"
        assert client.post(f"/api/tasks/{two['id']}/stop", headers=HEADERS).json()["status"] == "interrupted"
        assert client.request("DELETE", f"/api/tasks/{two['id']}", headers=HEADERS).status_code == 200
        assert not any(t["id"] == two["id"] for t in client.get("/api/tasks").json())
        client.post(f"/api/tasks/{one['id']}/stop", headers=HEADERS)


def test_paused_task_does_not_start_after_file_stability_wait(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path, stable=1)
        task = client.post("/api/tasks", json=files(root), headers=HEADERS).json()
        response = client.post(f"/api/tasks/{task['id']}/pause", headers=HEADERS)
        assert response.status_code == 200
        time.sleep(1.3)
        current = next(t for t in client.get("/api/tasks").json() if t["id"] == task["id"])
        assert current["status"] == "paused"
        assert current["started_at"] is None


def test_osinfo_crash_cache_validates_source_files(tmp_path, monkeypatch):
    from ui import metadata
    folder = tmp_path / "host" / "dump"
    folder.mkdir(parents=True)
    vmcore, dmesg, vmlinux = (folder / name for name in ("vmcore", "vmcore-dmesg.txt", "vmlinux"))
    vmcore.write_text("core")
    dmesg.write_text("Linux version 6.1.0\n", encoding="utf-8")
    vmlinux.write_text("symbols")
    calls = []
    monkeypatch.setattr(metadata.shutil, "which", lambda name: "/usr/bin/crash")
    def crash(core, symbols):
        calls.append((core, symbols))
        return {"hostname": "host-a", "os_name": "RHEL 8.10", "kernel": "6.1.0",
                "dump_time": "Tue Oct 13 09:00:00 CST 2026"}
    monkeypatch.setattr(metadata, "_crash_sys", crash)
    first = metadata.scan_metadata(str(vmcore), str(dmesg), str(vmlinux))
    assert first["hostname"] == "host-a" and len(calls) == 1
    content = (folder / "osinfo.txt").read_text(encoding="utf-8")
    assert "vmlinux_path: " + str(vmlinux) in content
    assert "dump_time: Tue Oct 13" in content
    second = metadata.scan_metadata(str(vmcore), str(dmesg), str(vmlinux))
    assert second["osinfo_status"] == "已校验缓存" and len(calls) == 1
    (folder / "osinfo.txt").write_text("hostname: forged\n", encoding="utf-8")
    metadata.scan_metadata(str(vmcore), str(dmesg), str(vmlinux))
    assert len(calls) == 2
    vmcore.write_text("changed core")
    metadata.scan_metadata(str(vmcore), str(dmesg), str(vmlinux))
    assert len(calls) == 3


def test_crash_osrelease_matches_symbols_then_reads_sys(tmp_path, monkeypatch):
    from ui import metadata
    from ui.discovery import scan
    folder = tmp_path / "dumps" / "host"
    folder.mkdir(parents=True)
    (folder / "vmcore").write_bytes(b"dump")
    (folder / "vmcore-dmesg.txt").write_text("Red Hat Enterprise Linux release 8.10\n", encoding="utf-8")
    release = "4.18.0-553.22.1.el8_10.x86_64"
    symbol = tmp_path / "symbols" / release / "vmlinux"
    symbol.parent.mkdir(parents=True)
    symbol.write_text("symbols")
    calls = []
    monkeypatch.setattr(metadata.shutil, "which", lambda name: "/usr/bin/crash")
    def fake_run(command, **kwargs):
        calls.append(command)
        output = (release + "\n" if command[1] == "--osrelease" else
                  f"DATE: Wed Oct 8 10:48:56 CST 2026\nNODENAME: host-a\nRELEASE: {release}\n")
        return type("Result", (), {"returncode": 0, "stdout": output})()
    monkeypatch.setattr(metadata.subprocess, "run", fake_run)
    config = {"scan_roots": [str(folder.parent)], "symbol_roots": [str(symbol.parent.parent)], "stable_seconds": 1,
              "execution_mode": "script"}
    found = scan(config)
    assert found[0]["kernel_hint"] == release
    assert found[0]["vmlinux_path"] == str(symbol)
    assert found[0]["hostname_hint"] == "host-a"
    assert found[0]["dump_time_hint"] == "Wed Oct 8 10:48:56 CST 2026"
    assert found[0]["os_name_hint"] == "Red Hat Enterprise Linux release 8.10"
    assert ["crash", "--osrelease", str(folder / "vmcore")] in calls
    assert ["crash", str(symbol), str(folder / "vmcore")] in calls
    assert scan(config)[0]["kernel_hint"] == found[0]["kernel_hint"]
    without_symbols = scan({**config, "symbol_roots": []})[0]
    assert without_symbols["kernel_hint"] == release
    assert without_symbols["vmlinux_path"] is None
    assert "未匹配 vmlinux" in without_symbols["osinfo_status"]


def test_vmcore_sample_fingerprint_groups_identical_dumps(tmp_path):
    from ui.discovery import vmcore_fingerprint
    first, second, changed = (tmp_path / name for name in ("first", "second", "changed"))
    first.write_bytes(b"A" * 128)
    second.write_bytes(first.read_bytes())
    changed.write_bytes(b"A" * 127 + b"B")
    assert vmcore_fingerprint(str(first)) == vmcore_fingerprint(str(second))
    assert vmcore_fingerprint(str(first)) != vmcore_fingerprint(str(changed))


def test_discovery_stream_lists_scanned_folders_and_vmcores(tmp_path):
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path)
        nested = root / "host" / "dump"
        nested.mkdir(parents=True)
        files(nested)
        response = client.get("/api/discover/stream")
        assert response.status_code == 200
        events = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        assert [event["path"] for event in events if event["type"] == "directory"] == [str(root), str(root / "host"), str(nested)]
        assert events[-2]["type"] == "vmcore"
        assert events[-1] == {"type": "complete", "folders": 3, "vmcores": 1}
        assert (nested / "osinfo.txt").is_file()


def test_cached_inventory_only_rechecks_new_or_changed_vmcores(tmp_path, monkeypatch):
    from ui import discovery
    with client_for(tmp_path) as client:
        login(client)
        root = configure(client, tmp_path)
        first = root / "first"
        first.mkdir()
        files(first)
        assert client.get("/api/discover/cache").json() == {"initialized": False, "items": []}
        assert client.post("/api/discover/incremental", headers=HEADERS).json()["needs_full_scan"]
        assert client.get("/api/discover/stream").text.count('"type": "vmcore"') == 1
        original = discovery.scan_metadata
        checked = []
        def counted(vmcore, dmesg, vmlinux):
            checked.append(vmcore)
            return original(vmcore, dmesg, vmlinux)
        monkeypatch.setattr(discovery, "scan_metadata", counted)
        unchanged = client.post("/api/discover/incremental", headers=HEADERS).json()
        assert unchanged["changed"] == 0 and len(unchanged["items"]) == 1
        assert checked == []
        second = root / "second"
        second.mkdir()
        files(second)
        updated = client.post("/api/discover/incremental", headers=HEADERS).json()
        assert updated["changed"] == 1 and len(updated["items"]) == 2
        assert checked == [str(second / "vmcore")]
        checked.clear()
        (first / "vmcore-dmesg.txt").write_text("Linux version 6.1.1 test\n", encoding="utf-8")
        changed = client.post("/api/discover/incremental", headers=HEADERS).json()
        assert changed["changed"] == 1 and checked == [str(first / "vmcore")]
    with client_for(tmp_path) as client:
        login(client)
        restored = client.get("/api/discover/cache").json()
        assert restored["initialized"] and len(restored["items"]) == 2
