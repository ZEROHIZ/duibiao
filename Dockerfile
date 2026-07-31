# 使用官方 Playwright Python 基础镜像，该镜像基于 Ubuntu 并预装了 Python、Playwright、Chromium 以及所有系统级图形库依赖
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV DISPLAY=:99
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 复制依赖描述文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# === 安装虚拟屏与远程桌面 (Xvfb / x11vnc / noVNC) 及智能体 CLI 工具 ===
# 1. 安装图形渲染与 VNC/noVNC Web 远程桌面依赖，Node.js 18
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    fluxbox \
    curl gnupg tzdata \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 安装 opencode CLI
RUN npm install -g opencode-ai

# 3. 安装 OpenAI Codex CLI
RUN npm install -g @openai/codex

# 4. 安装 Antigravity agy CLI (增加重试机制防止网络波动)
RUN (for i in 1 2 3 4 5; do curl -fsSL https://antigravity.google/cli/install.sh | bash && break || sleep 3; done) \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> /etc/environment
ENV PATH="/root/.local/bin:${PATH}"

# 复制整个项目到容器中
COPY . .

# 赋予入口引导脚本可执行权限
RUN chmod +x /app/entrypoint.sh

# 暴露后端 API 端口 (8000) 与 noVNC 网页远程桌面端口 (6080)
EXPOSE 8000 6080

# 启动服务引导脚本
ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
