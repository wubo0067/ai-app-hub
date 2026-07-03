#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── 颜色 ────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
DKGREEN='\033[2;32m'
DKGRAY='\033[2;37m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# 用法
usage() {
    echo "用法: $0 [--no-frontend] [--no-backend]"
    echo "  --no-frontend    仅启动后端"
    echo "  --no-backend     仅启动前端"
    exit 1
}

NO_FRONTEND=false
NO_BACKEND=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-frontend) NO_FRONTEND=true; shift ;;
        --no-backend)  NO_BACKEND=true;  shift ;;
        -h|--help)     usage ;;
        *) echo -e "${RED}未知参数: $1${NC}"; usage ;;
    esac
done

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║        Smart Mistake Lab  启动脚本      ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ─── 依赖检测 ────────────────────────────────────
if ! command -v node &>/dev/null; then
    echo -e "${RED}[错误] 未检测到 Node.js，请先安装 Node.js >= 18${NC}"
    echo "       下载地址: https://nodejs.org/"
    exit 1
fi

if ! command -v uv &>/dev/null; then
    echo -e "${RED}[错误] 未检测到 uv，请先安装 uv${NC}"
    echo "       安装方式: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ─── 依赖安装 ────────────────────────────────────
if [ ! -d "$ROOT_DIR/node_modules" ]; then
    echo -e "${GREEN}[信息] 未检测到 node_modules，正在安装前端依赖 ...${NC}"
    cd "$ROOT_DIR"
    npm install
    echo -e "${DKGREEN}  ✓ 前端依赖安装完成${NC}"
    echo ""
fi

if [ ! -d "$ROOT_DIR/server/.venv" ]; then
    echo -e "${GREEN}[信息] 未检测到 .venv，正在安装后端依赖 ...${NC}"
    cd "$ROOT_DIR/server"
    uv sync
    echo -e "${DKGREEN}  ✓ 后端依赖安装完成${NC}"
    echo ""
fi

cd "$ROOT_DIR"

# ─── 启动后端 ────────────────────────────────────
if [ "$NO_BACKEND" = false ]; then
    echo -e "${GREEN}▸ [1/2] 启动后端服务 (FastAPI) ...${NC}"
    echo -e "${DKGRAY}  ▸ http://127.0.0.1:8765${NC}"

    # 新终端启动后端 (优先 gnome-terminal, 回退 xterm)
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal -- bash -c "cd '$ROOT_DIR/server' && uv run python server.py; exec bash" 2>/dev/null
    elif command -v xterm &>/dev/null; then
        xterm -T "Smart Mistake Lab - Backend" -e "cd '$ROOT_DIR/server' && uv run python server.py" &
    else
        # 无图形终端时在当前后台运行
        cd "$ROOT_DIR/server"
        uv run python server.py &
        BACKEND_PID=$!
        cd "$ROOT_DIR"
        echo -e "${YELLOW}  ⚠ 未检测到图形终端，后端已在后台运行 (PID: $BACKEND_PID)${NC}"
    fi
    echo -e "${DKGREEN}  ✓ 后端服务启动中 ...${NC}"
    echo ""
    sleep 3
fi

# ─── 启动前端 ────────────────────────────────────
if [ "$NO_FRONTEND" = false ]; then
    echo -e "${GREEN}▸ [2/2] 启动前端服务 (Vite) ...${NC}"
    echo ""
    echo -e "${WHITE}  打开浏览器访问:${NC}"
    echo -e "${CYAN}  ┌─────────────────────────────────────┐${NC}"
    echo -e "${CYAN}  │  http://localhost:5173               │${NC}"
    echo -e "${CYAN}  └─────────────────────────────────────┘${NC}"
    echo ""
    echo -e "${YELLOW}  按 Ctrl+C 可停止前端服务${NC}"
    echo -e "${DKGRAY}  （后端进程请单独关闭）${NC}"
    echo ""

    cd "$ROOT_DIR"
    npm run dev
fi
