"""Small, single-process JSON store. Every replacement is complete or absent."""
import json
import os
import tempfile
import threading
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        for name in ("tasks", "cases", "reviews", "config", "audit_log", "dashboard_overrides"):
            if not (self.root / f"{name}.json").exists():
                self.write(name, {} if name in ("config", "dashboard_overrides") else [])
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "audits").mkdir(exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)

    def read(self, name):
        with self.lock:
            return json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))

    def write(self, name, value):
        with self.lock:
            self.atomic(self.root / f"{name}.json", json.dumps(value, ensure_ascii=False, indent=2))

    def atomic(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
            if os.name == "posix":
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def upsert(self, name, record, key="id"):
        with self.lock:
            rows = self.read(name)
            rows = [row for row in rows if row[key] != record[key]]
            rows.append(record)
            self.write(name, rows)

    def log(self, action, detail, actor="admin"):
        with self.lock:
            from datetime import datetime, timezone
            rows = self.read("audit_log")
            rows.append({"time": datetime.now(timezone.utc).isoformat(), "actor": actor,
                         "action": action, "detail": detail})
            self.write("audit_log", rows)
