param(
    [switch]$NoFrontend,
    [switch]$NoBackend
)

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $RootDir "server"

Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║        Smart Mistake Lab  启动脚本      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ─── 启动后端 ────────────────────────────────────
if (-not $NoBackend) {
    Write-Host "▸ [1/2] 启动后端服务 (FastAPI) ..." -ForegroundColor Green
    $backendDirForArg = $ServerDir -replace "'", "''"
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "cd '$backendDirForArg'; uv run python server.py"
    ) -WindowStyle Normal
    Write-Host "  ✓ 后端服务窗口已创建 (http://127.0.0.1:8765)" -ForegroundColor DarkGreen
    Write-Host "  ✓ 后端日志将显示在独立窗口中" -ForegroundColor DarkGray
    Write-Host ""
    Start-Sleep 3
}

# ─── 启动前端 ────────────────────────────────────
if (-not $NoFrontend) {
    Write-Host "▸ [2/2] 启动前端服务 (Vite) ..." -ForegroundColor Green
    Write-Host ""
    Write-Host "  打开浏览器访问:" -ForegroundColor White
    Write-Host "  ┌─────────────────────────────────────┐" -ForegroundColor Cyan
    Write-Host "  │  http://localhost:5173               │" -ForegroundColor Cyan
    Write-Host "  └─────────────────────────────────────┘" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  按 Ctrl+C 可停止前端服务" -ForegroundColor Yellow
    Write-Host "  （后端服务请在独立窗口中关闭）" -ForegroundColor DarkGray
    Write-Host ""

    Push-Location $RootDir
    try {
        npm run dev
    }
    finally {
        Pop-Location
    }
}
