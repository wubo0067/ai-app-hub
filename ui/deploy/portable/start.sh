#!/bin/sh
set -eu
cd /data/vmcore-ui
export PYTHONPATH=/data/vmcore-ui/site-packages:/data/vmcore-ui
export UI_DATA_DIR=/data/vmcore-ui/data
export UI_HOST=127.0.0.1
export UI_PORT=18080
exec /data/vmcore-ui/python/bin/python3.11 /data/vmcore-ui/ui/run.py
