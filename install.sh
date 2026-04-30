#!/bin/sh
# KwCode 安装程序 - Mac/Linux
# 用法: curl -sSL https://raw.githubusercontent.com/val1813/kwcode/main/install.sh | sh
#   或: chmod +x install.sh && ./install.sh

set -e

# ── 颜色 ─────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m'

step()  { printf "  ${GREEN}[*]${NC} %s\n" "$1"; }
warn()  { printf "  ${YELLOW}[!]${NC} %s\n" "$1"; }
err()   { printf "  ${RED}[x]${NC} %s\n" "$1"; }
info()  { printf "      ${GRAY}%s${NC}\n" "$1"; }

# ── Banner ───────────────────────────────────────────────────
printf "\n"
printf "  ${CYAN}╔══════════════════════════════════════╗${NC}\n"
printf "  ${CYAN}║       KwCode 安装程序 v1.0            ║${NC}\n"
printf "  ${CYAN}║   本地模型 Coding Agent               ║${NC}\n"
printf "  ${CYAN}╚══════════════════════════════════════╝${NC}\n"
printf "\n"

# ── Step 1: Python 版本检查 ──────────────────────────────────
step "检查 Python 版本..."

PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_CMD="$cmd"
            info "找到 Python $ver ($cmd)"
            break
        else
            warn "Python $ver 版本过低，需要 >= 3.10"
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    err "未找到 Python >= 3.10"
    info "macOS:  brew install python@3.12"
    info "Ubuntu: sudo apt install python3.12"
    info "其他:   https://www.python.org/downloads/"
    exit 1
fi

# ── Step 2: 安装 KwCode ──────────────────────────────────────
step "安装 KwCode..."

INSTALLED=0

# 优先 pipx（隔离环境，不污染系统 Python）
if command -v pipx >/dev/null 2>&1; then
    info "检测到 pipx，使用隔离安装..."
    if pipx install kwcode 2>/dev/null; then
        INSTALLED=1
        info "安装成功（pipx 隔离环境）"
    fi
fi

# 降级到 pip 默认源
if [ "$INSTALLED" -eq 0 ]; then
    info "尝试 pip 安装..."
    if "$PYTHON_CMD" -m pip install kwcode --quiet 2>/dev/null; then
        INSTALLED=1
        info "安装成功（pip 默认源）"
    fi
fi

# 降级到清华镜像
if [ "$INSTALLED" -eq 0 ]; then
    warn "默认源失败，切换到清华镜像..."
    if "$PYTHON_CMD" -m pip install kwcode \
        -i https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        --quiet 2>/dev/null; then
        INSTALLED=1
        info "安装成功（清华镜像）"
    fi
fi

if [ "$INSTALLED" -eq 0 ]; then
    err "KwCode 安装失败"
    info "请手动执行: $PYTHON_CMD -m pip install kwcode"
    info "如果网络慢，加上: -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
fi

# ── Step 3: 提示配置模型 ─────────────────────────────────────
step "模型配置提示..."
info "KwCode 支持任何 OpenAI 兼容 API（Ollama / DeepSeek / Qwen 等）"
info "安装完成后执行 kwcode init 即可配置 API 地址和模型"

# ── Step 4: 验证安装 ─────────────────────────────────────────
step "验证安装..."

if command -v kwcode >/dev/null 2>&1; then
    kwcode status || warn "状态检查失败，但安装可能已成功"
else
    "$PYTHON_CMD" -m kwcode status 2>/dev/null || warn "状态检查失败"
fi

# ── 完成 ─────────────────────────────────────────────────────
printf "\n"
printf "  ${GREEN}╔══════════════════════════════════════╗${NC}\n"
printf "  ${GREEN}║         安装完成!                     ║${NC}\n"
printf "  ${GREEN}╚══════════════════════════════════════╝${NC}\n"
printf "\n"
printf "  ${CYAN}下一步:${NC}\n"
printf "    1. cd 到你的项目目录\n"
printf "    2. kwcode init          # 初始化项目记忆\n"
printf "    3. kwcode               # 进入交互模式\n"
printf '    4. kwcode "修复登录bug"  # 直接执行任务\n'
printf "\n"
printf "  ${GRAY}文档: https://github.com/val1813/kwcode${NC}\n"
printf "\n"
