# KwCode 安装程序 - Windows PowerShell
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── Banner ───────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║       KwCode 安装程序 v1.0            ║" -ForegroundColor Cyan
Write-Host "  ║   本地模型 Coding Agent               ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Helper ───────────────────────────────────────────────────
function Write-Step($msg) { Write-Host "  [*] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [x] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "      $msg" -ForegroundColor Gray }

# ── Step 1: Python 版本检查 ──────────────────────────────────
Write-Step "检查 Python 版本..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                $pythonCmd = $cmd
                Write-Info "找到 $ver ($cmd)"
                break
            } else {
                Write-Warn "$ver 版本过低，需要 >= 3.10"
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Err "未找到 Python >= 3.10"
    Write-Info "请从 https://www.python.org/downloads/ 下载安装"
    Write-Info "安装时请勾选 'Add Python to PATH'"
    exit 1
}

# ── Step 2: 安装 KwCode ──────────────────────────────────────
Write-Step "安装 KwCode..."

$installed = $false

# 优先 pipx（隔离环境，不污染系统 Python）
if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Info "检测到 pipx，使用隔离安装..."
    & pipx install kwcode 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $installed = $true
        Write-Info "安装成功（pipx 隔离环境）"
    }
}

# 降级到 pip 默认源
if (-not $installed) {
    Write-Info "尝试 pip 安装..."
    & $pythonCmd -m pip install kwcode --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $installed = $true
        Write-Info "安装成功（pip 默认源）"
    }
}

# 降级到清华镜像
if (-not $installed) {
    Write-Warn "默认源失败，切换到清华镜像..."
    & $pythonCmd -m pip install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $installed = $true
        Write-Info "安装成功（清华镜像）"
    }
}

if (-not $installed) {
    Write-Err "KwCode 安装失败"
    Write-Info "请手动执行: $pythonCmd -m pip install kwcode"
    Write-Info "如果网络慢，加上: -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
}

# ── Step 4: 提示配置模型 ─────────────────────────────────────
Write-Step "模型配置提示..."
Write-Info "KwCode 支持任何 OpenAI 兼容 API（Ollama / DeepSeek / Qwen 等）"
Write-Info "安装完成后执行 kwcode init 即可配置 API 地址和模型"

# ── Step 5: 验证安装 ─────────────────────────────────────────
Write-Step "验证安装..."

try {
    & kwcode status
} catch {
    Write-Warn "状态检查失败，但安装可能已成功"
    Write-Info "请手动执行: kwcode status"
}

# ── 完成 ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║         安装完成!                     ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  下一步:" -ForegroundColor Cyan
Write-Host "    1. cd 到你的项目目录" -ForegroundColor White
Write-Host "    2. kwcode init          # 初始化项目记忆" -ForegroundColor White
Write-Host "    3. kwcode               # 进入交互模式" -ForegroundColor White
Write-Host '    4. kwcode "修复登录bug"  # 直接执行任务' -ForegroundColor White
Write-Host ""
Write-Host "  文档: https://github.com/val1813/kwcode" -ForegroundColor Gray
Write-Host ""
