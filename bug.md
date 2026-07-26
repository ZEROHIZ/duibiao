# Bug 档案记录 (bug.md)

## Bug 01: 智能蒸馏模式选择弹窗 / 终端抽屉在页面切换时发生排版移位与局部黑幕遮挡 (UI 不对)

* **发生时间**：2026-07-09
* **问题现象**：点击“AI 智能蒸馏”按钮后，弹出的 Modal 对话框和右侧 Drawer 出现严重偏斜与遮挡，屏幕左侧出现大片空白背景，底层半透明黑幕无法铺满全屏。
* **主要根源**：弹窗与抽屉元素的 `position: fixed` 原先被嵌套在 `blogger-detail-view` 容器内部。由于该页面容器在切换时会被 GSAP 等动画库进行三维或二维的 `transform` 位移动效变换，导致浏览器自动隔离了 `position: fixed` 的全局定位上下文，使其退化为相对父元素绝对定位，进而破坏了整体排版。
* **解决方案**：将 `#modal-distill-mode` 与 `#drawer-distill-terminal` 的 HTML DOM 节点从 `blogger-detail-view` 内部完全移出，统一放置到 `<body>` 根节点的末尾（紧邻 `delete-modal-overlay` ）。这样可确保其正确定位在 Viewport 上，不受任何页面转场动画的影响。

---

## Bug 02: 系统设置页面缺少 OpenAI API 配置输入框

* **发生时间**：2026-07-09
* **问题现象**：用户在系统设置面板里无法找到配置 OpenAI API Key, Base URL 以及 Model Name 的输入组件，导致拉起 Agent 蒸馏时直接因缺少密钥而报错。
* **主要根源**：后端和 API 层面虽然新增了对 OpenAI 参数 of 增删改查支持，但前端 `index.html` 的配置表单中未加入相应的 input 表单，且 `app.js` 的 `loadSettingsPageData` 与 `handleSystemSettingsSubmit` 均没有与之绑定的序列化读写逻辑。
* **解决方案**：
  * 在 `index.html` 对应表单内加入 OpenAI Credentials 参数组输入框。
  * 在 `app.js` 中补齐了对这三个新增字段的从 API 读取回显以及表单 Submit 提交序列化封装。

---

## Bug 03: 系统配置页面右侧留白过多且开关勾选框不美观

* **发生时间**：2026-07-09
* **问题现象**：系统参数配置表单采用单列纵向排列，在宽屏下右侧出现大面积空置，留白过多不够饱满；同时，传统的 checkbox 多选勾选框组件在杂志排版风格下视觉上显得过于突兀且不够协调。
* **主要根源**：未针对宽屏做分栏网格约束，且使用了基础的勾选框交互元素。
* **解决方案**：
  * 将 `system-settings-form` 修改为双列网格布局 (`display: grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; max-width: 1200px;`)，左侧承载系统与转录服务配置，右侧承载 AI 密钥及上限指标，按钮栏独占底端一行。
  * 将传统的“开启无头模式”和“开启后台转录” checkbox 组件改换为自定义风格的 `<select>` 下拉选项菜单，与表单整体的素雅杂志框体格调保持绝对一致，并在 `app.js` 中将布尔值与下拉框的 `"true"` / `"false"` 字符串进行转换。

---

## Bug 04: 对话在面对特长上下文时发生连接重置并伴随示例标题截断

* **发生时间**：2026-07-09
* **问题现象**：当分析正文极长的博主时，智能体请求 API 服务发生：`Server disconnected without sending a response` 错误；同时底稿中的示例标题被卡死在 20 个字符以内。
* **主要根源**：
  * 智能体客户端 HTTP 请求默认超时限制为 120 秒 (`timeout=120`)。当底稿中包含极长的视频转录完整全文时，Prompt 规模达到 90K+ tokens，导致本地 API 代理的 Prefill 填充计算时间超过 120 秒，从而使 Python 连接超时中断。
  * `scripts/deep_analyze.py` 中对标题有写死的 `[:20]` 切片截断。
* **解决方案**：
  * 将 `agent_engine/run_agent_loop.py` 中的 LLM HTTP 请求超时时间从 120 秒大幅提升至 **900 秒 (15分钟)**。
  * 在 `scripts/deep_analyze.py` 中完全去除了 `[:20]` 切片限制，在保证底稿和公式中包含完整、详实的长正文及完整标题的前提下，消除了连接中断风险。

---

## Bug 05: 智能体历史上下文压缩导致 Skill 指令遗忘、Context 溢出与人设/记忆模块丢失

* **发生时间**：2026-07-09
* **问题现象**：当智能体处理超长 Skill 指令（如 5W tokens 的博主创作指南）并在运行数轮后触发历史上下文压缩时，智能体会完全遗忘 Skill 的规则与约束，且此前在初始化时拼入的 `SOUL.md` 和 `MEMORY.md` 也会在压缩切片时被误删，导致智能体行为失常。此外，巨量工具输出直接追加至上下文会导致 API 报 Context 溢出崩溃。
* **主要根源**：
  * 原上下文压缩采用硬编码字符数（40k chars）切片，且固定压缩 `messages[1:-10]` 历史，没有区分系统规则区与会话对话区，导致夹在 `messages[1]` 处的人设与记忆被无差别压缩并删除。
  * 巨量 Skill 指令做为 tool 返回值被拼入对话历史，触发历史压缩后被移出，造成记忆丢失。
  * 缺少对单个超长工具输出（如大文件或大段 STDOUT）的过滤与首尾截断。
* **解决方案**：
  * 彻底移除已废弃的 `SOUL` / `MEMORY` 的加载与初始化逻辑。
  * 引入 `count_tokens` Token 计数器，使用 `tiktoken` 精确计数（提供字符比例估算 fallback），将压缩阈值改为 Token 限制（默认 50,000 tokens）。
  * 引入 **动态系统提示词固定 (Skill Instructions Pinning)** 机制：将已加载的 Skill 详细 instructions 动态绑定到不可压缩的 System Message `messages[0]` 中，确保其无论如何压缩历史也绝不丢失。
  * 引入**工具输出主动截断**：超过 15,000 字符的工具返回自动做首尾截断（保留前 4k 和后 4k 字符，中间标注截断说明）并进入 Active Context。
  * 引入**完整历史回溯**：在 `AgentSession` 中独立保存 `raw_messages` 原始日志，包含未压缩的对话与未截断的原始工具结果，并一并持久化存盘，方便智能体读取 JSON 文件进行细节回溯。

---

## Bug 06: Docker 部署下运行爬虫即使开启无头模式依然报错缺少浏览器可执行文件

* **发生时间**：2026-07-10
* **问题现象**：Docker 部署下以无头模式 (`--headless true`) 启动抖音爬虫脚本时，Playwright 抛出异常：`BrowserType.launch_persistent_context: Executable doesn't exist at /ms-playwright/chromium_headless_shell-1228/...` 并提示基础镜像版本与依赖库版本不匹配。
* **主要根源**：
  * Docker 镜像使用了旧版的基础镜像 `playwright/python:v1.40.0-jammy`，其内置的浏览器二进制文件（位于 `/ms-playwright`）只兼容 Playwright 1.40.0。
  * `requirements.txt` 中配置为 `playwright>=1.40.0`。在 Docker 构建镜像运行 `pip install` 时，自动拉取了最新版 Playwright Python 包（如 1.61.0）。
  * 最新版 Playwright 在启动浏览器时（不论是有头还是无头）都需要调用对应的最新版浏览器二进制文件（`chrome-headless-shell`），由于基础镜像里只有旧版浏览器且没有对应的最新二进制文件，导致启动失败。
* **解决方案**：
  * 将 `Dockerfile` 的基础镜像修改为与当前 Playwright 版本匹配的 `mcr.microsoft.com/playwright/python:v1.61.0-jammy`。
  * 将 `requirements.txt` 中的 `playwright>=1.40.0` 更改为固定版本的 `playwright==1.61.0`，以防止以后库版本与基础镜像再次产生漂移不一致的问题。

---

## Bug 07: 抖音爬虫登录检测存在竞态条件，误判登录状态并导致自动关闭登录弹窗

* **发生时间**：2026-07-10
* **问题现象**：在爬虫任务执行过程中，主线程日志显示 `[登录检测] 无法成功调起登录弹窗，将直接尝试继续执行任务...` 并判定为“已登录状态”，但随后后台弹窗监控协程输出 `[弹窗检测] 成功关闭出现的登录弹窗: #douyin-login-new-id 下的 SVG`。此时因实际上未登录，爬虫无法正常拦截到视频列表接口，任务抓取失败。
* **主要根源**：
  * **竞态条件（Race Condition）**：`page.goto()` 触发页面加载后立即调用 `ensure_login()`。由于页面中的“登录”按钮及潜在的初始登录弹窗渲染有延迟，检测时二者皆不存在，因而被脚本误判定为已登录。
  * **后台监控冲突**：主线程在判定“已登录”后，立即将全局变量 `ENABLE_POPUP_MONITOR` 设置为 `True`，启动了后台弹窗监控协程。此时主线程刚才点击登录按钮调起的弹窗（或者滚动触发的弹窗）才刚刚被渲染，随后立马被后台监控协程误关闭。
* **解决方案**：
  * 重构登录状态判定，引入 `check_page_login_state(page, context)` 辅助函数，多重特征（包括 `[data-e2e="live-avatar"]` 头像元素及 `sessionid` Cookie）综合判定登录状态。
  * 引入页面加载和检测缓冲（3秒等待 + 5次轮询轮检测），确保页面元素加载完毕再进行登录状态判断。
  * 严格控制后台弹窗监测的启用时机，保证在主线程 `ensure_login` 确认已登录后再开启 `ENABLE_POPUP_MONITOR`，避免在用户/脚本处理初始登录阶段被后台监控程序误杀弹窗。
  * 对于无法调起弹窗且没有 Session Cookie 的情况，禁止盲目返回 `True` 糊弄过关，直接抛错/终止任务，提高逻辑严密性。

---

## Bug 08: 网页作品列表重复拦截引发的数据膨胀以及大上限翻页切换脱节

* **发生时间**：2026-07-10
* **问题现象**：设定爬取上限大于单页数据量（如 30）时，虽然日志中显示 30 个不同视频被拦截了评论，但在最后的规整阶段却只保存了 18 条唯一的视频数据。且日志中显示在第 19 个视频时其处理 ID 错误折返到了第 1 个视频。
* **主要根源**：
  * **列表重复累加**：在页面初加载、扫码登录刷新时，同一个作品列表请求（每次返回 18 条视频）被拦截了 3 次，因无去重机制直接 `extend`，导致 `post_videos_raw` 内存列表虚高至 54 条重复视频，使计划抓取上限被锁定为 30。
  * **循环状态脱节**：在进入画廊详情模态框按下 `ArrowDown` 翻页时，虽然浏览器确实向前跳转到了第 19 个新视频，但脚本的内层循环是按照虚高的 54 条列表索引迭代。在第 19 次循环中，脚本从列表中读取到的是之前重复的第 1 个视频 ID。因此脚本判定“第 1 个视频已在缓存中”，从而认为“成功翻页至第 1 个视频”。最后规整筛选时，由于实际被处理的 ID 集合去重后只有前 18 个，导致第 19-30 个真正截获的新视频数据被保存筛选逻辑无情丢弃。
* **解决方案**：
  * **拦截器去重**：修改拦截逻辑，向 `post_videos_raw` 追加视频时增加 `aweme_id` 去重校验，彻底防止数据虚胖。
  * **第一重保险（主页下滚预加载）**：点击卡片前，若已拦截数量少于 `max_videos`，模拟下滚主页多次直到加载满额或触底。
  * **第二重保险（翻页越界自愈）**：翻页切换时如果列表已无下一项，先模拟下键触发浏览器自动分页请求，并挂起循环 8 秒轮询等待新数据填充以完成自愈并动态取得 `next_vid`，极大提高了在大爬取量下的程序稳定性。

---

## Bug 09: 主页无更新时依然调用浏览器交互造成低效与风控，且 URL 翻页验证未覆盖 modal_id 格式

* **发生时间**：2026-07-10
* **问题现象**：
  1. 即使博主毫无视频更新，爬虫依然必须点击首个卡片进入画廊详情，再判定重复后退出，消耗了大量交互资源和时间。
  2. 实现了精准新视频点击及部分视频跳过逻辑后，若切换到已爬取视频分支，会因为 URL 校验失败导致 `transition_success` 超时报错退出。
* **主要根源**：
  1. **缺少早期检测机制**：主页拦截作品后没有立刻比对数据，缺乏早期退出逻辑。
  2. **URL 正则只匹配标准路径**：`parse_active_video_id` 仅支持 `/video/(\d+)` 的正则提取，而通过主页列表打开的模态窗 URL 是 `?modal_id=(\d+)`，导致已爬取视频翻页时提取 ID 为 `None`，自愈检验判定失败。
* **解决方案**：
  - **早期更新比对**：遍历 `post_videos_raw` 作品，找到第一个在本地数据中缺失的视频索引 `start_idx`。若所有视频全部存在，主页层级直接打断安全退出，无需点击作品。
  - **精准定位点击**：有更新时，根据 `target_vid` 构造定位选择器 `a[href*="/video/{target_vid}"]`，跳过已爬取的置顶视频直接点击，并对齐循环起始索引。
  - **扩展 URL 校验兼容性**：修改 `parse_active_video_id` 正则为 `r'(?:video/|modal_id=|group_id=|reflow_video_id=)(\d+)'`，同时支持标准页面和模态框页面 ID 的解析。

---

## Bug 10: 选题列表与时间流热评用户名显示为 undefined 且缺少点赞数展示与排序

* **发生时间**：2026-07-11
* **问题现象**：在选题列表和作品时间流的“脱敏热门评论与作者互动监控”折叠面板下，评论的用户名显示为 `undefined`，且未显示每条评论的点赞数，评论列表也未按点赞数进行任何排序。
* **主要根源**：
  * **用户名显示 undefined**：数据字段中存储的用户名字段有 `speaker` (原始详情数据) 和 `user` (重算后的数据)。而前端 `app.js` 只读取了 `c.user`，在加载原始详情数据时导致其显示为 `undefined`。
  * **缺少点赞展示与排序**：前端渲染时未包含点赞数 (对应字段 `likeCount` 或 `likes`) 的 DOM 拼接，也没有对 `commentsList` 数组在遍历前按点赞数进行 `sort` 降序重排。
* **解决方案**：
  * 对用户名进行兼容性处理：`c.speaker || c.user || "匿名"`。
  * 对点赞数字段进行兼容性提取：`c.likeCount !== undefined ? c.likeCount : (c.likes !== undefined ? c.likes : 0)`。
  * 在渲染前端 HTML 模板前，对评论数组根据提取的点赞数进行 `sort` 降序重排。
  * 采用 Flex 布局结构在用户名右侧优雅地添加 `👍 ${likes}` 展示点赞数，使杂志化排版风格更加饱满。

---

## Bug 11: 自动提取报告定位时未显式调用 commit 导致事务回滚，造成博主定位丢失且仪表盘提示“暂无对标”

* **发生时间**：2026-07-11
* **问题现象**：在服务启动或上传最新诊断报告时，控制台虽然成功打印了 `[Category Sync] Updated blogger '小A' category to '理财思维与成长心智诊断者'`，但已覆盖分类大盘依然显示为“暂无博主分类数据，请先录入对标账号”，刷新页面后 category 字段被重置丢失。
* **主要根源**：
  * **未提交事务 (No Commit)**：`sync_blogger_category_from_html` 接口中在执行 `UPDATE bloggers SET category = ? ...` 时，判定了如果传入的 `conn_or_cursor` 具有 `execute` 属性就直接调用。但在该分支中**缺少了 `conn_or_cursor.commit()`**。因为未提交事务，在连接被 `close()` 时 SQLite 进行了自动回滚（Rollback），导致数据未能成功写入磁盘。
  * **双击编辑交互冲突**：原先的主营定位修改需要弹出 prompt 对话框，不够素雅顺滑，且与同列表的名称、主页链接“原地双击编辑”的风格未能统一。
* **解决方案**：
  * **强制事务提交**：重构 `sync_blogger_category_from_html`，在修改 bloggers 表时优先从 connection 获取 cursor 并显式执行 `conn.commit()` 以保证物理持久化。同时，加入了对 cursor 的降级兼容性兜底，保障其在任何调用上下文都能稳健发挥作用。
  * **内联原地编辑升级**：在 `index.html` 的定位列渲染 `class="editable-field" data-field="category"`，在 `app.js` 的内联保存处理 `finishEdit` 中加入 category 字段分支逻辑，双击直接原地变为 input 框，失去焦点或按回车键立即完成网络提交保存。

---

## Bug 12: Google / OpenAI 智能体模型列表拉取及 CLI 版本与登录状态检测超时

* **发生时间**：2026-07-14
* **问题现象**：在前端点击“获取模型”按钮或进行 CLI 诊断时，后台日志打印拉取超时（“❌ 运行超时！进程未在 3 秒内响应”），且无法获取真实的可用模型列表，被迫启用本地硬编码的保底模型列表；另外，点击单个“获取模型”按钮或进页面时，系统会错误地同时并发拉取两边的模型逻辑。
* **主要根源**：
  * **网络与代理延迟**：在 Windows 系统上或者网络代理环境下，拉起 CLI 进程 (`agy` / `codex`) 本身有额外的进程创建开销，且拉取模型列表通常需要对 Google 或 OpenAI 网关发起 HTTP/HTTPS 请求，容易受到代理、DNS 解析、握手建立等延迟影响，3 秒的 `timeout` 极易被击穿。
  * **登录状态/版本检测偏短**：原先 CLI 诊断中，读取版本信息和登录状态的 `timeout` 分别为 2 秒，也存在因系统高负载或命令行启动慢导致的误判风险。
  * **页面初始化过度获取**：在前端 `app.js` 页面初始化 `loadOAuthPageData` 时，无论用户是否需要，都会自动并发向后端请求 Google 和 OpenAI 两方的可用模型列表并生成拉取任务，导致两个日志文件在相同时间段被写入，使得用户在“任务日志”中误以为它们被点击一个按钮同时触发了。
* **解决方案**：
  * 将 `web/backend/app.py` 中 `agy models` 与 OpenAI `/models` 网络拉取的 `timeout` 从 3 秒提升至 **15 秒**。
  * 将 `web/backend/app.py` 中检测 CLI 可执行文件版本 and 登录状态的 `timeout` 从 2 秒提升至 **5 秒**。
  * 完全移除了 `web/frontend/app.js` 中进入“智能体授权”页面时自动拉取两边模型的逻辑，仅在用户点击对应服务方的“获取模型”按钮时才按需触发单侧的拉取，提升页面切换速度，避免冗余的后台子进程耗用资源。

---

## Bug 13: Google 智能体模型列表拉取成功后前端下拉框模型名称显示不全 (被空格截断)

* **发生时间**：2026-07-14
* **问题现象**：点击“获取模型”成功后，前端“Google CLI 运行模型”下拉框中原本类似 `Gemini 3.5 Flash (Medium)`、`Claude Sonnet 4.6 (Thinking)` 的完整模型名被截断，只显示首个单词，即 `Gemini`、`Claude`、`GPT-OSS`。
* **主要根源**：
  * **按空格错误分割 (Splitting Bug)**：在 `web/backend/app.py` 内部解析 CLI 输出行的模型列表时，代码中包含 `model_id = line.split()[0]`。此逻辑会将模型名称按照空格进行切片，并仅提取第一个单词作为模型 ID 返回给前端，导致了后续全部词组（如版本号、级别说明等）丢失。
* **解决方案**：
  * 将 `web/backend/app.py` 中的解析逻辑重构，直接提取去除两端空白的整行内容 `parsed_models.append(line)` 作为完整的模型标识符，完美保留了包括空格和括号在内的所有模型细节。

---

## Bug 14: 智能体可用模型列表未进行本地持久化，导致每次页面刷新或重载后列表丢失

* **发生时间**：2026-07-14
* **问题现象**：虽然能通过点击“获取模型”成功从 CLI 和网络拉取最新的模型列表并临时填充到下拉框中，但这些获取的模型列表并未持久化到本地配置文件中，用户一旦刷新页面或重新进入“智能体授权”选项卡，下拉框就会重置回原始极简的硬编码保底模型列表，必须再次手动拉取。
* **主要根源**：
  * **缺少配置存盘支持**：后端配置项 schema (`DEFAULT_SETTINGS` 与 `SettingsUpdate`) 缺少存放拉取到的可用模型列表的字段，且在 `/api/auth/cli/models` 接口返回成功数据时，没有将拉取到的最新 `models` 写入系统配置 `config.json` 中。
* **解决方案**：
  * 在 `web/backend/app.py` 的全局 `DEFAULT_SETTINGS` 默认属性字典与 `SettingsUpdate` 接收表单模型中增加了 `google_models_list` 与 `openai_models_list` 两个字段。
  * 重构了模型获取接口 `/api/auth/cli/models`：当列表获取成功后，会自动加载现有设置、更新对应的 `models_list`、执行 `save_settings` 完成磁盘持久化。
  * 在前端 `web/frontend/app.js` 的 `loadOAuthPageData` 页面初始化方法中增加判断：若本地已持久化了对应的模型列表，则自动解析列表并动态渲染模型下拉选择框的所有项，从而在免去频繁冗余拉取的同时，保证了最新模型列表在页面重载后依旧存在。---

## Bug 15: 抖音画廊切换到第二个视频后无法定位并点击评论按钮，以及 Windows GBK 控制台下的编码崩溃问题

* **发生时间**：2026-07-14
* **问题现象**：
  1. 爬虫在爬取博主多条视频时，第一个视频顺利进入画廊并打开评论区。但按下向下键切换到第二个视频后，程序提示 `[校验] 未检测到评论 API 响应，尝试点击评论按钮...` 随后以未定位到评论按钮报错退出。
  2. 在 Windows 中文 locale 环境（系统默认 GBK 编码）下，流水线主脚本 `pipeline.py` 运行完毕或抛错输出时，由于控制台打印包含 emoji（如 `✅`, `❌`, `🎉`），导致解释器抛出 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'` 编码错误崩溃。
* **主要根源**：
  1. **全局定位漂移**：`click_comment_button` 以前采用 `page.locator().first` 在全局范围内查找第一个可见的评论按钮。在画廊模式中，除了当前活动视频滑块外，网页列表中其他的视频卡片 DOM 节点依然存在。切换到第二个视频后，全局的 `first` 匹配到的依然是已被滑走的第一个视频的评论按钮。因为其已被隐藏，导致 `btn.is_visible()` 校验失败，无法对眼前正在播放的第二个视频进行正确的评论区展开。
  2. **默认管道 GBK 约束**：FastAPI 后端使用 subprocess 读取 pipeline stdout 时开启了 UTF-8 管道读取，但 Windows 下 Python 进程若没有配置输出流编码，在重定向输出时会继承系统 GBK 控制台环境，导致打印非 GBK 字符（emoji）时产生编码冲突崩溃。
* **解决方案**：
  * **精准容器隔离**：重构 `click_comment_button(page, vid)` 与 `hover_share_button(page, vid)`，支持传入当前处理的视频 ID (`vid`)。函数在定位时，优先匹配 `[data-e2e="feed-active-video"][data-e2e-vid="{vid}"]` 确定唯一的激活视频卡片容器，然后在该容器内部通过 `container.locator()` 检索评论和分享，完全隔离了背景 DOM 的干扰。如果未传入 vid，则依次降级到通用激活容器和全局兜底。
  * **输出编码重载**：在 `pipeline.py` 头部的 import 区，加入 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` 重配置流编码，使其无论控制台如何设置都始终以 UTF-8 输出，与后端对接管口对齐。

---

## Bug 16: Google 智能体 CLI 授权重定向死锁与 Windows 无头终端运行崩溃问题 (0xC000013A)

* **发生时间**：2026-07-15
* **问题现象**：
  1. 在无真实 TTY 的后台 Python 重定向管道中启动 Google `agy` 登录交互流时，Go 语言命令行工具会因无法渲染 Bubbletea TUI 而抛出 `0xC000013A` (3221225786) 崩溃退出。
  2. 即使运行 `agy models` 检测状态，如果未登录，其依然会尝试渲染 TUI，且由于 Pipe 重定向启动了 Go 运行时的 block buffering 块缓冲，导致授权 URL 堵在内存中无法刷出，与后台 stdin 形成永久死锁。
* **主要根源**：
  - Windows 控制台句柄绕过：Go 语言的命令行选单与终端 TUI 库（如 Bubbletea）在 Windows 下默认尝试直接开启物理控制台句柄 `CONOUT$` / `CONIN$`。当 Python 使用常规 `subprocess.PIPE` 管道重定向时，Go 会绕开管道直接向物理终端设备读写，导致输出无法被 Python 捕获，且输入无法从网页端送入，造成死锁卡死。
  - 无物理 TTY 运行 Bubbletea 会导致 0xC000013A (3221225786) 崩溃。
* **解决方案**：
  - **基于 xterm.js + PTY WebSocket 的真网页终端重构**：
    - **前端集成**：在前端载入 `xterm.js` 与 `xterm-addon-fit` 库，代替原有的 `<pre>` 日志框。慢速渲染导致挤压的部分已被 `fitAddon.fit()` 彻底解决。初始化时自动调用 `term.focus()` 获得输入焦点。
    - **点击与粘贴监听**：对终端容器绑定 `click` 强制聚焦监听。同时绑定 `paste` 粘贴事件，一旦用户右键或快捷键粘贴，自动从剪贴板提取 Token 并通过 WebSocket 发给后端伪终端，实现零死角密钥回填。
    - **后端双向伪终端（PTY）桥接**：
      - Windows 平台：使用 `pywinpty` 的 `PtyProcess` 产生 ConPTY 物理会话，使 `agy` 能够获取真实的终端窗口环境，完美呈现 Bubbletea 彩色字符画及选单。
      - Linux/Docker 平台：使用标准库 `pty` 模块进行 `pty.fork()` 进行 POSIX PTY 伪终端桥接。
      - 架设全新的 WebSocket 路由 `/api/auth/terminal/ws` 进行字符双向流式传递，进程退出时自动刷新绑定状态。

---

## Bug 17: Windows 子进程命令参数丢失与智能体任务日志非流式输出问题

* **发生时间**：2026-07-16
* **问题现象**：
  1. 在 Windows 环境下启动单视频拆解时，`agy` 进程完全没有执行任何有意义的操作便以退出码 `1` 退出，且生成的日志文件仅有头部 Header。
  2. 智能体分析过程中，前端的日志监控界面一直处于空白状态，直到整个任务彻底运行结束，日志才“一瞬间”全部吐出来，缺乏过程感知。
* **主要根源**：
  - Windows 环境下如果在使用 `subprocess.Popen` 时开启了 `shell=True`，且传入的命令是一个参数列表（List），则 cmd.exe 只会把列表的第一项（即可执行程序路径 `agy`）作为命令执行，后面的所有参数均会被截断丢弃。
  - Python 对 `process.stdout` 的内置迭代器（如 `for line in process.stdout`）在读取重定向流时存在内部块缓存，在缓冲区未满（通常为 4KB）或没有接收到 EOF 之前不会向下产出，导致日志无法流式传输。
* **解决方案**：
  - **传参安全性控制**：将 `shell` 参数统一强限制为 `False`（`shell=False`）。在不启用 shell 解释器的前提下，操作系统内核能安全且完整地将参数列表（List）传给子进程。
  - **流式无缓冲日志输出**：改用底层 `readline()` 循环无缓冲读取，每读取到一行数据，立即使用 `"a"` 模式打开日志追加写入并 `flush()` 刷新磁盘缓存后关闭，彻底打通了到前端控制台的流式展现通道。

---

## Bug 18: Google 智能体 agy CLI 沙箱检测路径错乱与全盘搜寻问题

* **发生时间**：2026-07-16
* **问题现象**：
  - 智能体进程在后台运行时，不断尝试访问用户的系统目录 `C:\Users\Administrator\.gemini\antigravity-cli\scratch` 或对 C 盘开展大量的文件遍历，提示 `skills/hothook/SKILL.md` 技能定义文件和数据库无法找到，生成的文件也无法保存到项目的 output 文件夹中。
* **主要根源**：
  - `agy` CLI 运行智能体具有独立的沙箱隔离机制。若在 Prompt（-p）中只给出相对路径，它默认不会以 Python 程序的当前工作目录（CWD）为基准，而是在自己的用户配置盘中寻找，进而触发其全盘自动搜寻逻辑。
* **解决方案**：
  - **动态工作区装载**：在 `cmd` 参数中追加 `--add-dir` 参数并传入动态的当前项目根目录 `ROOT_DIR`。
  - **绝对路径强制绑定**：将内置 Prompt 模板中的技能定义文件（`SKILL.md`）、SQLite 本地数据库（`distiller.db`）以及最终报告与改写脚本的保存输出路径（`output`）全部重构为**基于运行环境动态生成的绝对路径**（例如在 Windows 下为 `D:/daima/...`，在 Docker 容器内为 `/app/...`）。从而引导智能体直接对目标位置进行读写，避免了盲目遍历系统盘。

---

## Bug 19: 系统设置页面加载时发生 TypeError: Cannot set properties of null (setting 'value') 崩溃

* **发生时间**：2026-07-27
* **问题现象**：在前端点击“系统设置”页面时，页面数据无法正常回显加载，控制台抛出 `TypeError: Cannot set properties of null (setting 'value') at loadSettingsPageData (app.js:2144:67)`。
* **主要根源**：前端 `app.js` 的 `loadSettingsPageData` 和 `handleSystemSettingsSubmit` 依赖于 `setting-google-login-cmd` 对应的 DOM 输入框元素来进行设置参数的回显和保存。然而，在 `index.html` 前期改版为双列网格布局后，不慎漏掉了 GOOGLE CLI 登录指令的 HTML 输入项，导致 `document.getElementById("setting-google-login-cmd")` 返回 `null`，引发 TypeError 崩溃。
* **解决方案**：在 `index.html` 的 `system-settings-form` 中补全了 ID 为 `setting-google-login-cmd` 的 `form-group` 容器和 `<input>` 输入框元素，使其与 `app.js` 的读写逻辑重新对齐。

---

## Bug 20: 对标账号表格 10 列表头与数据行 9 列不匹配引发错位，及缺少自动唤醒智能体 CLI 开关控制

* **发生时间**：2026-07-27
* **问题现象**：
  1. 对标账号 UI 表格管理页面中，所有数据单元格（如“主营定位”、“监控链接”）整体向左错位挪动了 1 列，最右侧“管理操作”列完全留空。
  2. 抓取流水线在完成数据导入后，会自动触发 `agy` CLI 唤醒智能体，缺乏可控制的关闭选项。
* **主要根源**：
  1. 表格 HTML 表头 `<thead>` 声明了 10 个 `<th>` 列（包含单独的“账号平台”列），但前端 `app.js` 的 `loadBloggersList` 渲染数据行时把 `b.name` 和 `platformBadge` 合并在第 1 个 `<td>` 中，导致 `<tbody>` 实际只有 9 个 `<td>` 单元格，造成全局向左挪位 1 列。
  2. 流水线 `pipeline.py` 步骤 3.5 无条件检测 Token 并自动拉起 `trigger_agent_cli`，前端和后端缺少对自动唤醒行为的控制开关。
* **解决方案**：
  1. 修改 `app.js`，将平台 Badge 独立拆分为第 2 个 `<td>` 单元格，调整空状态 `colspan="10"`，并在各 `<td>` 增加显式的 `white-space: nowrap` 与 `vertical-align: middle` 约束。
  2. 在 `style.css` 中为 `#table-bloggers-management` 增加全局防变形与最小列宽约束，禁止文本发生竖向折行。
  3. 在 `index.html` 的 `style.css` 与 `app.js` 引用中加入版本号 `?v=20260727_2` 强行击穿浏览器静态文件缓存，确保修改立即生效。
  4. 在后端配置及前端系统设置中增加 `enable_auto_agent` 开关项，并更新 `pipeline.py` 在 `trigger_agent_cli` 中增加对该开关的判断逻辑。关闭时，抓取导入完成后自动跳过 CLI 唤醒。

---

## Bug 21: 主页监控链接显示冗长 URL 且缺少单击跳转逻辑，及底层横向滚动条原生样式粗糙突兀

* **发生时间**：2026-07-27
* **问题现象**：
  1. 对标账号表格中的“主页监控链接”直接把完整长 URL 渲染出来，破坏了整体杂志界面的素雅美感；且无法在不双击编辑的前提下直接在浏览器打开该链接。
  2. 表格底部的横向滚动条使用了 Windows / 浏览器原生的灰色粗大带箭头的拉条（带白轨和方角），与整体高雅杂志风格极不协调。
* **主要根源**：
  1. 单元格将 raw `home_url` 完整填充在 `innerText` 中，缺少统一的 Link Badge 视觉包装以及单/双击手势分离监听。
  2. 未配置自定义 Webkit 及 Firefox 滚动条 CSS 规则，导致直接继承了操作系统的原生滚动条外观。
* **解决方案**：
  1. 在 `app.js` 中将链接列改渲染为精美的 `主页链接 ↗` Badge（未配置时显示 `未配置 (双击设置)`），并在节点上挂载 `data-url` 属性存放真实 URL 字符串。
  2. 引入防冲突手势：单击在 250ms 延迟后在新标签页 `window.open` 打开主页；双击在 250ms 内清除单击定时器，自动调起 `<input>` 并初始化填充真实 URL 字符串供编辑。
  3. 在 `style.css` 中为 `.table-container` 与全局加入定制的无底色极简滚动条样式 (`::-webkit-scrollbar`)，彻底移除底轨白色背景 (`background: transparent !important`) 与箭角按钮，使用统一的暖粘土纸面墨色 `var(--ink-tertiary)` 拖拽条，Hover 时高亮为复古陶红/暗夜金 (`var(--accent-primary)`)。
