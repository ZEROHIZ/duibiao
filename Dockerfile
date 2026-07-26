# 使用官方 Playwright Python 基础镜像，该镜像基于 Ubuntu 并预装了 Python、Playwright、Chromium 以及所有系统级图形库依赖
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 复制依赖描述文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# === 安装智能体 CLI 工具 ===
# 1. 安装 Node.js 18（用于 opencode CLI 和 codex CLI 的 npm 依赖）
RUN apt-get update && apt-get install -y curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. 安装 opencode CLI（管理 Google Antigravity 渠道 OAuth 登录，即 opencode auth login）
RUN npm install -g opencode-ai

# 3. 安装 OpenAI Codex CLI（codex login --device-auth）
RUN npm install -g @openai/codex

# 4. 安装 Antigravity agy CLI（Google Antigravity AI 智能体，用于调用 AI 模型）
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> /etc/environment
ENV PATH="/root/.local/bin:${PATH}"

# 复制整个项目到容器中
COPY . .

# 暴露后端服务端口
EXPOSE 8000

# 启动 FastAPI 服务
CMD ["python", "web/backend/app.py"]
