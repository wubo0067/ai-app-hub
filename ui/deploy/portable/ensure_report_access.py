"""Make configured script report directories usable by the UI service account."""
import json
import shutil
import subprocess
import sys
from pathlib import Path


USER = "vmcoreui"


def accessible(path: Path) -> bool:
    return all(subprocess.run(
        ["runuser", "-u", USER, "--", "test", flag, str(path)],
        check=False,
    ).returncode == 0 for flag in ("-x", "-r", "-w"))


def main(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("execution_mode") != "script":
        return
    roots = config.get("report_roots") or []
    if not roots:
        raise RuntimeError("脚本模式需要配置报告输出目录")
    for configured in roots:
        path = Path(configured)
        if not path.is_absolute() or not path.is_dir():
            raise RuntimeError(f"报告目录不存在或不是绝对目录：{path}")
        if not accessible(path):
            if not shutil.which("setfacl"):
                raise RuntimeError(f"{USER} 无权读写报告目录，系统缺少 setfacl：{path}")
            subprocess.run(["setfacl", "-m", f"u:{USER}:rwx", str(path)], check=True)
        if not accessible(path):
            raise RuntimeError(f"{USER} 仍无权读写报告目录，请检查上级目录进入权限：{path}")
        print(f"报告目录权限检查通过：{path} ({USER} 可读写)")


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]))
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"报告目录权限检查失败：{exc}", file=sys.stderr)
        sys.exit(1)
