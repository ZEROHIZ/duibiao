#!/bin/bash
# -------------------------------------------------------------
# Docker 容器启动引导脚本 (entrypoint.sh)
# 核心职责：
# 1. 启动 Xvfb 虚拟显示屏 (:99)，允许 Playwright 以 Headful 模式渲染 UI
# 2. 启动 Fluxbox 窗口管理器与 x11vnc (5900)
# 3. 启动 websockify / noVNC (6080)，允许用户从 HTML5 浏览器直接观看与操作
# 4. 最终启动 FastAPI 后端应用
# -------------------------------------------------------------

set -e

# 设置默认 Display 环境变量
export DISPLAY=${DISPLAY:-:99}

echo "====================================================="
echo "🚀 启动 Docker 图形环境与服务组件"
echo "====================================================="

# 0. 自动清理上次容器崩溃/重启遗留的 X11 显示屏锁文件与 Chromium 单例死锁标志
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /tmp/.X*-lock /tmp/.X11-unix/X* 2>/dev/null || true
if [ -d "/app/data/browser_context" ]; then
    find /app/data/browser_context \( -name "SingletonLock" -o -name "SingletonSocket" -o -name "SingletonCookie" \) -delete 2>/dev/null || true
fi

# 1. 启动 Xvfb 虚拟显示屏 (分辨率: 1280x800, 24位深度)
echo "[1/5] 启动 Xvfb 虚拟屏 ($DISPLAY)..."
Xvfb $DISPLAY -screen 0 1280x800x24 -ac &
sleep 1

# 2. 启动 Fluxbox 窗口管理器
echo "[2/5] 启动 Fluxbox 窗口管理器..."
fluxbox &
sleep 1

# 3. 校验 VNC 密码安全配置
VNC_PASS_CMD="-nopw"
if [ -n "$VNC_PASSWORD" ]; then
    echo "[Security] 检测到 VNC_PASSWORD 环境变量，启用 VNC 访问密码校验..."
    mkdir -p ~/.vnc
    x11vnc -storepasswd "$VNC_PASSWORD" ~/.vnc/passwd
    VNC_PASS_CMD="-rfbauth ~/.vnc/passwd"
else
    echo "[Security] 默认以无密码模式 (-nopw) 启动 VNC 服务..."
fi

# 4. 启动 x11vnc
echo "[3/5] 启动 x11vnc 服务 (端口 5900)..."
x11vnc -display $DISPLAY -forever -shared -rfbport 5900 $VNC_PASS_CMD -quiet &
sleep 1

# 5. 启动 websockify / noVNC 服务 (网页映射端口 6080)
echo "[4/5] 启动 noVNC 网页代理服务 (端口 6080)..."
if [ -d "/usr/share/novnc" ]; then
    websockify --web /usr/share/novnc 6080 localhost:5900 &
elif [ -d "/usr/local/novnc" ]; then
    websockify --web /usr/local/novnc 6080 localhost:5900 &
else
    echo "⚠️ 未找到 noVNC 静态目录，尝试使用 websockify 基础代理模式..."
    websockify 6080 localhost:5900 &
fi
sleep 1

# 6. 启动 FastAPI 后端服务
echo "[5/5] 启动 FastAPI 主后台应用 (app.py)..."
exec python web/backend/app.py
