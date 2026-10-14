"""Build the offline RHEL 8.10 x86_64 bundle from downloaded inputs.

Usage (from repository root):
    python ui/deploy/build_rhel8_bundle.py \
      --runtime ui-bundle-staging/python-runtime.tar.gz \
      --site-packages ui-bundle-staging/site-packages \
      --output vmcore-ui-rhel8.10-x86_64.tar.gz
"""

import argparse
import io
import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
PORTABLE = UI / "deploy" / "portable"


def include(path):
    parts = set(path.parts)
    # Portable deployment files are added at archive root. Exclude older,
    # generic deployment examples to avoid shipping two Nginx configurations.
    return ("deploy" not in parts and not parts.intersection(
        {"data", "data-preview", "__pycache__", ".pytest_cache", ".venv"})
        and path.suffix != ".pyc")


def add_bytes(out, name, content, mode=0o644):
    payload = content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = mode
    out.addfile(info, io.BytesIO(payload))


def build(runtime, packages, output):
    with tarfile.open(output, "w:gz", compresslevel=6) as out:
        with tarfile.open(runtime, "r:gz") as source:
            for member in source:
                if member.name.startswith("python/"):
                    out.addfile(member, source.extractfile(member) if member.isfile() else None)
        for path in UI.rglob("*"):
            if path.is_file() and include(path.relative_to(UI)):
                out.add(path, arcname="ui/" + path.relative_to(UI).as_posix(), recursive=False)
        for path in packages.rglob("*"):
            relative = path.relative_to(packages)
            if path.is_file() and "__pycache__" not in relative.parts and "bin" not in relative.parts and path.suffix != ".pyc":
                out.add(path, arcname="site-packages/" + relative.as_posix(), recursive=False)
        for name in ("start.sh", "install.sh", "upgrade.sh", "vmcore-ui.service", "vmcore-ui.conf", "README.txt", "ensure_report_access.py"):
            out.add(PORTABLE / name, arcname=name, recursive=False,
                    filter=lambda info: _mode(info, 0o755 if name.endswith(".sh") else 0o644))
        out.add(UI / "deploy" / "config.production.json", arcname="config.production.json", recursive=False)
        add_bytes(out, "bundle-info.json", json.dumps({
            "target": "RHEL 8.10 x86_64",
            "install_path": "/data/vmcore-ui",
            "report_root": "/data/vmcore-analysis-agent/reports",
            "scan_root": "/data/vmcore",
            "analysis_script": "/data/analytics_vmcore.sh",
            "public_url": "http://vmcore.example.com:8080/",
            "nginx_listen_port": 8080,
            "ui_listen": "127.0.0.1:18080",
        }, ensure_ascii=False, indent=2) + "\n")


def _mode(info, mode):
    info.mode = mode
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.runtime, args.site_packages, args.output)
