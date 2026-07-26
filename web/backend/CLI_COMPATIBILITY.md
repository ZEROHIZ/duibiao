# 智能体 CLI 跨平台兼容与开发指南 (CLI_COMPATIBILITY.md)

本指南详细记录了 Google Antigravity (`agy`) 与 OpenAI Codex (`codex`) 智能体客户端在 **Windows 宿主机**与 **Docker 容器 (Linux)** 下的兼容性设计和后端接口逻辑，方便后续迭代开发。

---

## 1. 运行环境诊断自检 (Diagnostics)
* **检测接口**：`GET /api/auth/cli/status`
* **检测机制**：
  1. 使用 Python 的 `shutil.which("agy")` / `shutil.which("codex")` 扫描系统环境变量路径，定位执行文件名。
  2. 若找到路径，通过拉起带 `--version` 参数的子进程抓取其版本号；若子进程执行出错，标记为通用 `已安装` 状态。
  3. 检测结果实时渲染在前端授权页的 **“智能体 CLI 运行环境诊断”** 面板中。

---

## 2. 跨平台一键部署 (Installer)
* **拉起接口**：`POST /api/auth/cli/install`（以 `BackgroundTasks` 异步执行）
* **日志接口**：`GET /api/auth/cli/install-logs`
* **自动编译与部署逻辑**：
  * **OpenAI Codex CLI**：
    * **Linux/Docker 容器环境**：首先检测 `npm` 是否存在。若缺失，自动静默执行系统级安装：`apt-get update && apt-get install -y nodejs npm`。接着执行全局部署：`npm install -g @openai/codex`。
    * **Windows 宿主环境**：自动定位系统的 `npm` 执行文件路径，拉起 `npm.cmd install -g @openai/codex`。
  * **Google Antigravity CLI**：
    * **Linux/Docker 容器环境**：拉起官方一键部署指令：`curl -fsSL https://antigravity.google/cli/install.sh | bash`。
    * **Windows 宿主环境**：拉起 Powershell 模块执行官方安装指令：`powershell -Command "irm https://antigravity.google/cli/install.ps1 | iex"`。
  * **日志捕获**：使用 `subprocess.Popen(..., stderr=subprocess.STDOUT)` 捕获所有安装输出，前端以 1 秒为间隔轮询展示。

---

## 3. 统一网页终端 OAuth 交互流程
* **接口列表**：
  - `/api/auth/terminal/start`：拉起登录进程。
  - `/api/auth/terminal/poll`：获取日志。
  - `/api/auth/terminal/input`：向进程输入验证码。
  - `/api/auth/terminal/kill`：强杀进程。
* **兼容性设计**：
  * **非阻塞字符级捕获 (`read_process_stdout`)**：
    Google / OpenAI 在命令行登录时，会输出提示语（例如 `Enter code:`，尾部没有换行符）。为了防止进程因为没有换行而导致缓冲区堵塞，捕获线程使用 `proc.stdout.read(1)` 进行**单字符实时读取**并写入全局缓冲区，确保交互提示在网页端无延时秒出。
  * **超链接智能解析**：
    前端 `app.js` 轮询到终端日志后，会利用正则表达式匹配 `https://...` 链接，自动在终端黑框中替换为 `<a href="..." target="_blank">` 标签，支持用户一键跳转到浏览器授权。
  * **进程输入回填**：
    用户在网页端输入授权码点击“发送”，后端通过 `proc.stdin.write(code + "\n")` 管道喂给正在挂起的子进程，闭环完成授权。

---

## 4. 拆解分析任务运行兼容 (Teardown & Pipeline)
当用户点击 **[AI 拆解]**（运行 `/api/hothook/teardown`）或自动定时执行更新流水线时，系统会在后台拉起分析命令，以下是为多端适配的关键代码规约：

* **Windows 批处理执行限制 (`shell=is_windows`)**：
  在 Windows 下，`agy` 可能指向批处理脚本（如 `.cmd`）。如果 `subprocess.Popen` 没有配置 `shell=True`，Windows 会直接抛出 `[WinError 2] 系统找不到指定的文件`。
  * **开发规约**：拉起智能体 CLI 的 Popen 必须自适应传递 `shell=is_windows` 参数（其中 `is_windows = os.name == "nt"`），并且在 Windows 上需要将命令参数直接以单个 String 传给 Popen（防止 shlex 导致参数解析歧义）。
* **防止拉起桌面 IDE 窗口 (`agy` 统一指令)**：
  在 Google 通道下，必须执行指令 **`"agy"`**，而**不能执行 `"antigravity"`**。因为在 Windows 下 `"antigravity"` 会映射到 `antigravity.cmd` 并直接唤醒本地桌面的图形化 IDE 软件。使用 `agy` 能够确保在 Windows 和 Docker 下均以 headless 静默命令行方式在后台运行。
* **智能体思索日志展示 (`--verbose`)**：
  为了提供高透明度的拆解日志，拉起 `agy` 时必须默认附加 `--verbose` 选项。这会让智能体的详细思考链路（思考、搜索、文件读取、工具链）全部输出到日志文件中，从而在前端动态展示其完整的拆解行为。

---

## 5. 前置状态自诊断与自动升级 (Optimization)
* **兼容性过滤**：
  如果用户已经登录过（即宿主机上已经有 IDE 授权，或者 Docker 容器内已经完成了 OAuth），如果盲目拉起 `agy login`，Go 的交互式 readline 库由于没有 TTY 终端可能会直接打印 `^C` 并崩溃退出。
  * **解决方案**：在 `/api/auth/terminal/start` 接口中进行了前置登录检测。
    * **Google 检测**：扫描并读取 `~/.config/opencode/antigravity-accounts.json`。若有活跃账号，直接在控制台输出“检测到就绪，无需重复登录”，同步状态并退出。
    * **OpenAI 检测**：在后台执行 `codex login status` 探测，若包含 `Logged in` 关键字则直接绑定成功。
* **命令指令配置热升级**：
  在 `load_settings()` 中加入了热升级代码。如果用户系统中带有老版本的配置文件（包含过时的 `antigravity login --no-browser` 等错误参数），在读取时会自动将其重写升级为 `agy login` 和 `codex login --device-auth`，保证版本迭代后用户的配置平滑过渡。
