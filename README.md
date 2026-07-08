<div align="center">

## 🎯 核心功能

*   **⚡ 极速同步与数据分析**：自动采集对标博主的笔记与视频统计数据，生成均赞、均藏、藏赞比、爆款率等运营数据看板。
*   **🎙️ 异步后台语音转录 (Whisper)**：内置高效的后台转译 Worker，支持将视频自动下载并调用 Whisper 服务翻译为文字，无缝填补文案细节。
*   **🔑 浏览器上下文持久化与无头扫码**：原生支持浏览器状态持久化（`data/browser_context`），首次登录生成二维码图片，支持在 Headless Docker 环境下进行扫码登录并终身免登录。
*   **🗂️ 任务双选项卡与实时控制台**：在网页端日志页提供“同步数据任务”与“后台语音转译”双向切换 Tab，支持逐个任务 stdout 实时流式日志回显。
*   **⚙️ 灵活系统控制**：设置页提供 Whisper 模型切换、单次获取上限、无头模式开关、后台转译扫描间隔（分钟级设定）和**「立即扫描转录」**等快捷唤醒控制。

---

## 🐳 Docker 快速启动

本项目完全支持容器化，免去了您在本地安装浏览器与复杂图形库依赖的烦恼。

### 📌 一行命令构建并启动 (推荐)
请在项目根目录下打开终端，执行以下一行命令即可完成构建、端口映射与持久化挂载：

```bash
docker compose up -d --build
```

### 📌 纯 `docker run` 一行命令启动
如果您不使用 Docker Compose，可以直接使用以下纯 `docker` 命令进行拉取构建与运行（已做好本地数据、输出与截图目录的挂载映射）：

```bash
# 构建镜像
docker build -t blogger-distiller .

# Windows (PowerShell) 一行命令运行
docker run -d --name blogger-distiller -p 8000:8000 -v "${PWD}/data:/app/data" -v "${PWD}/output:/app/output" -v "${PWD}/screenshots:/app/screenshots" --restart always blogger-distiller

# Linux / macOS (Bash) 一行命令运行
docker run -d --name blogger-distiller -p 8000:8000 -v "$(pwd)/data:/app/data" -v "$(pwd)/output:/app/output" -v "$(pwd)/screenshots:/app/screenshots" --restart always blogger-distiller
```

> **💡 持久化与服务说明**：
> - 启动后，请在浏览器中访问：`http://localhost:8000`
> - **持久化保障**：容器中使用的 SQLite 数据库、Cookie 缓存、生成报告及二维码截图，均映射在您本地的文件夹中，容器销毁重建数据不丢失。
> - **扫码登录**：如果是全新容器且抖音未登录，后台日志提示扫码时，可在本地的 `screenshots/` 目录下找到生成的 `login_qr.png` 二维码，用手机抖音扫码即可！

---

## 💻 本地 Python 环境运行

如果您希望在本地物理机直接运行：

### 1. 安装依赖包
```bash
pip install -r requirements.txt
```

### 2. 初始化 Playwright 浏览器核心
```bash
playwright install chromium
```

### 3. 启动后端应用
```bash
python web/backend/app.py
```
访问：`http://127.0.0.1:8000`

---

## 📂 项目结构说明

```
blogger-distiller/
├── Dockerfile                     # 官方 Playwright Python 基础环境容器定义
├── docker-compose.yml             # Docker 服务与本地数据持久化挂载编排
├── requirements.txt               # Python 依赖项描述
├── web/
│   ├── backend/
│   │   └── app.py                 # FastAPI 服务端（状态机控制、Worker 调度、API 路由）
│   └── frontend/
│       ├── index.html             # 看板面板、参数设置、双标签任务控制台 HTML
│       └── app.js                 # 前端数据绑定与轮询日志实现
├── scripts/
│   └── pachopngjiaoben/
│       ├── douyin_crawler.py      # Playwright 模拟下滚、持久化免密及扫码爬虫
│       ├── convert_douyin_notes.py# 抖音数据归一化与语音转译处理
│       └── pipeline.py            # 数据流调度管道
└── data/                          # 挂载的本地数据目录（SQLite 数据库与免密 Session 缓存）
```

---

## 📄 开源许可证
本项目基于 [MIT License](./LICENSE) 协议开源。
