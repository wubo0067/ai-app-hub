"""Run from the ui directory: python run.py"""
import os
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
uvicorn.run("ui.app:app", host=os.environ.get("UI_HOST", "127.0.0.1"),
            port=int(os.environ.get("UI_PORT", "8080")), workers=1)
