# 使用官方 Playwright Python 基础镜像，该镜像基于 Ubuntu 并预装了 Python、Playwright、Chromium 以及所有系统级图形库依赖
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

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

# 复制整个项目到容器中
COPY . .

# 暴露后端服务端口
EXPOSE 8000

# 启动 FastAPI 服务
CMD ["python", "web/backend/app.py"]
