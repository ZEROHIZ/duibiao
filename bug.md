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

---

## Bug 22: 得到 (biji.com) 爬虫未联动系统无头模式配置且日志输出缓冲延迟

* **发生时间**：2026-07-27
* **问题现象**：
  1. 在【系统设置】中把【浏览器运行模式】切换为“关闭无头模式”后，点击一键同步得到文案仍旧在后台静默运行，没有弹出 Chrome 浏览器视窗。
  2. 任务日志文件无法实时更新输出，必须等到 120 秒超时或任务结束后才一次性爆出所有日志。
* **主要根源**：
  1. `app.py` 中的 `/api/biji/sync` 路由未读取 `settings.get("headless")` 配置项，未给 `biji_browser.py` 追加 `--headful` 参数。
  2. Python 默认在 `subprocess` 中对标准输出 (stdout) 进行 C-stdio 缓冲区留存，导致日志被缓存在内存中直到进程退出；且得到的同步任务未注册进全局 `active_crawl_tasks` 字典中。
* **解决方案**：
  1. 在 `app.py` 的 `/api/biji/sync` 路由中自动读取 `load_settings()` 中的 `headless` 开关，当非无头时自动追加 `--headful` 命令行参数。
  2. 强制传入 `PYTHONUNBUFFERED=1` 环境变量，并在 `run_biji_thread` 中采用按行读取 (`process.stdout`) 与 `f.flush()` 实时清刷写盘。
  3. 将得到的同步任务注册到 `active_crawl_tasks` 中，并在前端点击同步按钮时自动跳转至【任务日志】选项卡，支持秒级实时刷新日志与扫码二维码展示。

---

## Bug 23: 得到 (biji.com) 登录二维码未渲染至任务控制台下方的截图排查区域

* **发生时间**：2026-07-27
* **问题现象**：得到抓取提示未登录并生成登录二维码截图后，前端【任务日志】控制台下方的“异常/验证码截图排查”区域仍旧保持隐藏空白，未自动将生成的二维码图片展示给用户扫码。
* **主要根源**：
  1. `app.py` 中的 `/api/crawl/status/{task_id}` 接口对日志历史文件查询时，默认返回了空数组 `screenshots: []`，且未能识别 `biji_qr_account_01.png` 截图文件。
  2. `analyze_task_step` 未将得到截取二维码日志识别为 `⚠️ 等待微信扫码登录中...` 步骤。
* **解决方案**：
  1. 在 `app.py` 的 `/api/crawl/status/{task_id}` 路由中扩充截图扫描逻辑：不论是从任务字典还是日志文件读取，均自动扫描 `screenshots/` 目录下匹配任务时间或包含 `biji_qr` 的二维码图片，并回传 URL 数组。
  2. 升级 `analyze_task_step`，精准捕捉“登录二维码已截取”日志行，设置状态为 `⚠️ 等待微信扫码登录中...`。
  3. 在 `app.js` 中适配得到提示：“⚠️ 请使用微信或得到 APP 扫码登录”，自动展开并渲染下方二维码图片。

---

## Bug 24: `get_crawler_status` 检索历史日志时报 `NameError: name 'timedelta' is not defined`

* **发生时间**：2026-07-27
* **问题现象**：在前端【任务日志】中点击查看历史 `biji_sync_xxx.log` 任务日志时，黑框控制台中弹出 `Failed to read agent logs: name 'timedelta' is not defined` 报错。
* **主要根源**：`app.py` 中的 `/api/crawl/status/{task_id}` 路由在计算历史日志截图时间差时调用了 `timedelta(minutes=10)`，但该函数作用域内未从 `datetime` 模块中导入 `timedelta`。
* **解决方案**：在 `get_crawler_status` 函数体内加入 `from datetime import timedelta`，彻底解决未定义报错。

---

## Bug 25: 抖音对标爬虫标题省略号截断与得到盲目遍历无干博主问题

* **发生时间**：2026-07-28
* **问题现象**：
  1. 抖音对标爬虫抓取入库的视频标题末尾带有 `...` 省略号，未能保存完整文本。
  2. 得到同步引擎运行时盲目循环得到关注列表中的所有随机账号（如“方师傅”），未以本地数据库待转录博主为驱动目标。
* **主要根源**：
  1. `douyin_crawler.py` 的 `extract_title` 函数（第 93 行）对超过 25 字符的标题强制执行了 `[:25] + "..."` 截断。
  2. `biji_browser.py` 缺乏对本地数据库 `bloggers` 及 `blogger_notes` 待转录状态的先置过滤。
* **解决方案**：
  1. 移除 `douyin_crawler.py` 中 `extract_title` 的 25 字符强制截断代码，直接保留完整的 `desc` 标题行，从源头消灭 `...`。
  2. 将 `biji_browser.py` 重构为**目标驱动**机制：先检索本地主库待转录对标博主卡片；已有 `biji_url` 直接直连作品页，无 `biji_url` 才走知识库寻路与原主页 `url` 比对绑定。
  3. 提取 `3.json` 原视频 URL 中的抖音数字 ID (`aweme_id`) 与主表 `blogger_notes.id` 100% 绝对精确对齐，并使用全名覆盖修复旧数据库中的省略标题。

---

## Bug 26: 早期历史抓取导致主库博主卡片 `biji_url` 脏数据污染问题

* **发生时间**：2026-07-28
* **问题现象**：博主『基米叫兽』在主库中的 `biji_url` 被写入了得到博主『方师傅』的作品页链接（`...followName=方师傅`），导致在【直连模式】下直接打开了方师傅的作品页。
* **主要根源**：在引入【目标驱动】重构之前的历史运行版本中，旧的模糊匹配逻辑在尝试同名关联时，误将未匹配到得到卡片的『基米叫兽』(ID: 314) UPDATE 绑定了方师傅的得到 Follow ID 和 URL。
* **解决方案**：
  1. 运行数据修复脚本 `fix_polluted_urls.py`，清除了 `bloggers` 表中所有 `biji_url` 参数与 `bloggers.name` 不匹配的脏数据。
  2. 在 `biji_browser.py` 中引入**自动链接污染自愈校验**：在【直连模式】前自动解析 `biji_url` 中的 `followName` 参数，若发现与当前博主名不匹配，实时清空脏链接并重转入寻路模式，彻底杜绝历史错链导致张冠李戴。

---

## Bug 27: 前端任务日志查看历史误显二维码截图与控制台非实时更新问题

* **发生时间**：2026-07-28
* **问题现象**：
  1. 无论点击列表中的哪个历史任务日志（即使是成功完成的旧日志），下方都固定显示一张包含 `biji_qr_account_01.png` 的二维码截图。
  2. 右侧控制台在任务执行过程中没有实时刷出最新日志，必须手动刷新页面或重新点击“查看日志”按钮才显示新日志。
* **主要根源**：
  1. `app.py` 中的 `get_crawler_status` 接口使用了 `or "biji_qr" in filename` 的无条件判断，导致任何日志查询都匹配并返回了最新的二维码截图 URL。
  2. `app.py` 返回的 JSON 顶层 `status: "success"` 是 HTTP API 成功标志，而前端 `app.js` 在 `pollConsoleLog` 中误将 `json.status === "success"` 当作任务完成标记，导致第一次请求返回后立即误把 `setInterval` 轮询定时器给 `clearInterval` 销毁了！
* **解决方案**：
  1. 在 `app.py` 中严格限制截图过滤：取该任务的实际运行时间窗口（`started_dt - 5分钟` ~ `task_end_dt + 5分钟`），只有落在此时间段内生成的截图才返回；历史无关日志返回空截图数组 `[]`，隐藏截图盒。
  2. 在 `app.py` 的返回 JSON 中增加独立的 `task_status` 字段（区分 API 响应状态与任务运行状态）；在 `app.js` 中修改为 `json.task_status === "success" || json.task_status === "failed" || json.task_status === "completed"` 时才停止轮询，确保任务运行期间按 1.5 秒频率持续无缝推流实时日志！

---

## Bug 28: 任务刚启动显示“成功”随后又跳回“进行中”与需手动点击查看日志问题

* **发生时间**：2026-07-28
* **问题现象**：
  1. 点击触发任务后，任务列表第一秒显示绿色“成功”，几秒后又突然跳回黄色/红色“进行中”。
  2. 触发新任务后，控制台不会自动选定该新任务，仍需要人工点击“查看日志”按钮。
* **主要根源**：
  1. `app.py` 中的 `get_all_agent_tasks` 在扫描日志文件时，忽略了内存中 `active_crawl_tasks` 的实时 `running` 状态，仅凭借 `(now - file_mtime) < 12秒` 来猜测。当进程初始化或日志写入停顿超过 12 秒时，接口直接默认赋了 `status = "success"`（误显示为“成功”）；稍后日志文件写入刷更新时间，又变回了 `running`（变回“进行中”）。
  2. `app.js` 的 `loadSettingsPageTasks` 在渲染完列表后，未自动联动选定 `status === "running"` 的最新任务。
* **解决方案**：
  1. 在 `app.py` 的 `get_all_agent_tasks` 中，优先查询 `active_crawl_tasks` 内存中的真实运行状态。只要后台线程还在 `running`，任务状态 100% 固定为 `running`，彻底消灭状态跳变。
  2. 在 `app.js` 中实现**新运行任务自动盲选聚焦**：当检测到列表中有新启动的 `running` 任务或尚未选定任务时，自动触发 `selectConsoleTask` 自动切入最新任务并开启实时控制台推流。

---

## Bug 29: GET `/api/crawl/tasks` 报 HTTP 500 `KeyError: 'created_at'` 报错

* **发生时间**：2026-07-28
* **问题现象**：前端在轮询 `/api/crawl/tasks` 接口时，控制台抛出 `HTTP 500 Internal Server Error`，日志显示 `KeyError: 'created_at'`。
* **主要根源**：`biji_sync` 在向内存字典 `active_crawl_tasks` 注册运行任务时，使用了 `"task_id"` 和 `"started_at"` 键名，但缺少了 `"id"` 和 `"created_at"` 字段；导致 `get_all_crawl_tasks` 执行 `tasks_list.sort(key=lambda x: x["created_at"])` 时因为不存在 `created_at` 键而抛出 KeyError 异常。
* **解决方案**：
  1. 在 `biji_sync` 的 `active_crawl_tasks` 注册字典中补齐 `"id"` 与 `"created_at"` 字段。
  2. 在 `get_all_crawl_tasks` 中使用 `.get("created_at") or .get("started_at") or ""` 进行防御性安全取值，防止因字段缺漏导致 500 崩溃。

---

## Bug 30: 同步建库 Locator 匹配超时与前端阻塞同步提交 500 异常

* **发生时间**：2026-07-29
* **问题现象**：
  1. 点击“保存并录入”触发新建得到知识库时，后端抛出 `❌ [得到建库异常]: Locator.click: Timeout 30000ms exceeded. waiting for locator("text=创建知识库")`，接口返回 `HTTP 500 Internal Server Error`。
  2. 前端提交表单时同步等待 Playwright 建库导致页面长时间卡顿。
  3. 博主昵称为非必填项，但原前端表单强制要求填入。
* **主要根源**：
  1. `biji_browser.py` 中的 `is_visible()` 没有等待元素加载完成，直接误落入 `else` 分支去查找包含 `创建知识库` 纯文本的选择器，而实际网页元素为带有空白符的 `div.create-item`。
  2. 前端在点击“保存并录入”时同步等待建库 HTTP 接口返回，未遵循“先保存本地数据库，再后台异步建库与关注”的解耦原则。
* **解决方案**：
  1. 在 `biji_browser.py` 中使用 `create_btn.wait_for(state="visible", timeout=8000)` 显式等待创建按钮挂载并可点击。
  2. 重构提交逻辑：点击“保存并录入”时，后端立即将博主写入 SQLite 数据库并返回 `200 OK`，得到自动建库（`create_biji_topic`）与博主关注（`add_blogger_to_biji`）全部放在后台异步线程中完成，且有无头模式严格读取系统全局设置。
  3. 将前端“博主昵称”设为可选（非必填）；若留空，后台自动赋临时标识并在爬取时由得到 / 爬虫自动补充替换真正的博主昵称。

---

## Bug 31: 有头模式配置未生效与得到建库任务缺失推流日志

* **发生时间**：2026-07-29
* **问题现象**：
  1. 用户在【系统设置】中切换为“关闭无头模式”（有头模式）后，保存并录入博主时，依然没有桌面浏览器窗口弹出。
  2. 点击“保存并录入”后，【任务日志】面板左侧列表没有产生任务记录，右侧控制台无日志输出，无法感知推进到了哪一步。
* **主要根源**：
  1. 系统配置存储的键名为 `headless`（布尔值或 `"true"`/`"false"`），而 `get_headless_setting()` 此前误读取了不存在的键名 `headless_browser`，导致默认一直回退至 `True`（无头模式）。
  2. `create_blogger` 后台的得到建库/关注线程未向全局 `active_crawl_tasks` 任务队列注册 `task_id`，且未将日志写入 `data/logs/{task_id}.log`，导致前端任务队列与控制台无法抓取推流。
* **解决方案**：
  1. 修复 `get_headless_setting()`，使其优先读取 `headless` 字段，兼容字符串与布尔类型解析。
  2. 升级 `create_blogger`：启动后台得到流程时，自动生成 `biji_add_xxx` 任务注册至 `active_crawl_tasks` 内存队列，并将每一步操作（`[步骤 1/2]`、`[得到建库成功]`、`[步骤 2/2]`）实时写入对应的日志文件。
  3. 前端提交成功后自动带入 `task_id` 自动切换至【任务日志】选项卡，高亮并自动聚焦推流该任务。

---

## Bug 32: 得到 Playwright 页面挂死卡顿与缺失粒度步骤点击日志

* **发生时间**：2026-07-29
* **问题现象**：在得到自动建库/关注过程中，页面打开后长时间卡住（1 分 25 秒），且控制台缺少每一步查找按钮、点击按钮的具体操作日志。
* **主要根源**：
  1. `biji_browser.py` 中的 `page.goto()` 误使用了 `wait_until="networkidle"`，得到页面后台存在持久化的 WebSockets / 轮询连接，导致 Playwright 一直等待 80 多秒直至超时。
  2. `BijiBrowserEngine` 内部的 `create_biji_topic` 和 `add_blogger_to_biji` 仅使用了标准 `print()` 输出，未向全局 `log_func` 日志句柄推送，导致 UI 无法同步看到“点击『添加』按钮”、“点击『订阅直播/博主』”等细粒度日志。
* **解决方案**：
  1. 将 `page.goto()` 的等待策略统一替换为 `wait_until="domcontentloaded"`，秒级加载完成。
  2. 在 `BijiBrowserEngine` 中增加 `log_func` 回调机制，并在每一步 DOM 查找、按钮点击、Tab 切换、弹窗填值、网络拦截（1.json / 2.json）处都增加详细的 `self.log("🌐/🔍/👆/✍️ ...")` 步骤日志，实现 100% 全全透明可视化推流。

---

## Bug 33: Uvicorn 热重载监听路径遗漏与下拉菜单项定位异常

* **发生时间**：2026-07-29
* **问题现象**：修改 `scripts/biji_browser.py` 后，后端日志仍显示 `waiting for locator("//*[contains(text(), '订阅直播/博主')]")`，修改未实时生效。
* **主要根源**：
  1. Uvicorn 在 `app.py` 中被配置为 `reload_dirs=["web"]`，仅监听 `web` 目录，未监听 `scripts/` 目录。代码修改后，运行中的 Python 进程未重新加载内存中的模块。
  2. 下拉菜单项包含 `data-reka-collection-item=""` 及 `role="menuitem"` 特殊属性。
* **解决方案**：
  1. 修改 `app.py` 的 Uvicorn 配置为 `reload_dirs=["web", "scripts"]`，确保修补任何 Playwright 脚本后自动触发热重载生效。
  2. 根据最新的 `4.json` HTML 结构，将菜单选择器精准更新为 `[role='menuitem']:has-text('订阅直播/博主'), [data-reka-collection-item]:has-text('订阅直播/博主')`。

---

## Bug 34: `save_biji_url_to_db` 缺失与 2.json 异步响应竟态导致数据库回写空置

* **发生时间**：2026-07-29
* **问题现象**：得到关注任务提示 `🎉 [完成] 博主已成功处理完毕！` 且日志显示 `📡 拦截到 2.json 关注响应成功! FollowID: 1341267`，但 SQLite 数据库中的 `biji_url` 和 `biji_follow_id` 依然显示为 `NULL`。
* **主要根源**：
  1. `biji_browser.py` 内部调用了 `self.save_biji_url_to_db(...)`，但 `BijiBrowserEngine` 类中没有定义该方法（方法定义缺失），导致运行时抛出 `AttributeError` 并被外层 `except` 捕获拦截。
  2. 点击确定按钮后仅 `time.sleep(3)` 便立刻读取 `captured_follow_info` 字典；由于网络请求延时，`2.json` 响应在第 3.5 秒才完成拦截，导致读取时字典仍为空，提前落入了 `else` 自愈分支；而自愈分支又因为博主名字为 `待爬取博主_xxx` 被跳过，最终导致 `biji_url` 未能生成。
* **解决方案**：
  1. 在 `BijiBrowserEngine` 类中补齐并优化 `save_biji_url_to_db` 方法，增加根据 `name` / `home_url` / `biji_topic_alias` 进行保底回写的 SQL 逻辑。
  2. 点击确定后增加显式轮询等待：`for _ in range(20): if captured_follow_info: break; time.sleep(0.5)`（最长等待 10s 拦截），彻底消灭竟态延迟，确保 100% 成功捕获并回写至 SQLite！

---

## Feature 35: 博主数据表格新增“浏览器账号”与“得到知识库 / biji_url”两列可视化呈现

* **完成时间**：2026-07-29
* **新增需求**：在【灵感数据总览-对标博主表格】中新增两列，分别直观展示：1) 该博主绑定的得到浏览器账号；2) 该博主归属的得到知识库名称/别名以及 `biji_url` 保存状态。
* **实现方案**：
  1. 后端 `app.py` 的 `GET /api/bloggers` 增加 `b.biji_account, b.biji_topic_name, b.biji_topic_alias, b.biji_url, b.biji_follow_id` 字典字段输出。
  2. 前端 `index.html` 增加 `<th>浏览器账号</th>` 与 `<th>得到知识库 / biji_url</th>` 表头。
  3. 前端 `app.js` 增加专属 Badge 与跳转链接渲染：若保存了 `biji_url`，显示 `✅ 得到链接 ↗` 点击可在新标签页直接跳转打开得到知识库订阅页；若为空则显示 `⚠️ 未保存 URL`。

---

## Bug 36: `GET /api/bloggers` 500 异常（SQLite 字段名 `biji_browser_id` 匹配问题）

* **发生时间**：2026-07-29
* **问题现象**：访问主页时接口 `GET /api/bloggers` 返回 500 Internal Server Error 错误。
* **主要根源**：SQL 查询中写为了 `b.biji_account`，而 SQLite `bloggers` 表中的对应字段真实列名为 `biji_browser_id`，抛出 `sqlite3.OperationalError: no such column: b.biji_account` 错误。
* **解决方案**：将 SQL 查询更新为 `b.biji_browser_id as biji_account`，接口恢复 200 OK 且输出完全正确。

---

## Bug 37: 删除博主后服务重启自动“复活”问题

* **发生时间**：2026-07-29
* **问题现象**：在前端表格中点击“删除”博主后数据库记录被删除，但当 Web 服务重启或触发热重载后，该博主又重新出现在博主列表中。
* **主要根源**：
  在系统启动时，`startup_event()` 会自动调用 `importer.py` 的 `run_full_import()` 扫描 `data/` 目录下的 `[博主名]_analysis.json`。此前删除博主接口 `DELETE /api/bloggers/{id}` 只删除了 SQLite 数据库记录，未清理磁盘 `data/` 目录下的 `[博主名]_analysis.json` 磁盘缓存文件。导致服务重启时，`importer.py` 重新扫描到该文件并再次将其写回 SQLite 数据库。
* **解决方案**：
  升级 `delete_blogger` (`DELETE /api/bloggers/{id}`) 接口，在删除 SQLite 数据库级联记录的同时，同步删除 `data/[博主名]_analysis.json` 和 `data/processed/[博主名]_notes_details.json` 磁盘缓存文件，彻底断绝重启恢复问题！

---

## Bug 38: 2.json 响应中过渡态名称过滤与真实博主名自动替换

* **发生时间**：2026-07-29
* **问题现象**：得到关注刚提交时 `2.json` 返回的 `name` 可能是 `GET笔记正在帮你订阅...` 或临时占位名 `待爬取博主_xxx`，导致后续提取或回写跳过。
* **主要根源**：未对 `2.json` 中的 `name` 键值建立有效性过滤机制，且未在捕获到真实博主名（如 `小A学财经`、`方师傅`）时自动替换 SQLite 中的 `待爬取博主_xxx` 临时记录。
* **解决方案**：
  1. 在 `biji_browser.py` 中新增 `is_valid_name()` 过滤函数，屏蔽 `GET笔记正在帮你订阅`、`待订阅` 及 `待爬取` 等过度态与临时名字。
  2. 自动从 `2.json` 抽取合规的真实博主名拼装 `biji_url` (`followName=...`)，并自动将 SQLite 数据库中的 `待爬取博主_xxx` 名字更正替换为抓取到的真实博主名。

---

## Feature 39: 前端推流日志感知“数据库回写成功”实时更新列表博主名称

* **完成时间**：2026-07-29
* **新增需求**：后台从 `2.json` 拦截并回写替换真实博主名称（如将 `待爬取博主_xxx` 替换为 `方师傅` / `小A学财经`）时，前端列表需无缝实时刷出新名字，无需用户手动按 F5。
* **解决方案**：
  在 `app.js` 的任务日志推流与轮询回调中增加实时监听：当日志文本出现 `💾 [数据库回写成功]` 或任务变为 `success`/`completed` 时，自动静默触发 `loadBloggersList()`，无感刷新前端表格，使真正的博主名字与 `biji_url` 瞬间秒级同步出海！

---

## Bug 40: 2.json `url` 双向 URL 比对与单行 SQL `target_id` 精准更新

* **发生时间**：2026-07-29
* **问题现象**：得到 `2.json` 返回多个已订阅博主时，未比对主页 URL 导致抓错名字，且在数据库回写时抛出 `UNIQUE constraint failed: bloggers.name` 错误。
* **主要根源**：
  1. 此前拦截 `2.json` 列表时只抽取了 `(name, follow_id)` 字典，没有把 `2.json` 里的 `url`（如 `https://v.douyin.com/xxx` 或 `douyin.com/user/xxx`）提取出来与当前录入的 `home_url` 进行双向归一化比对。
  2. SQL `UPDATE` 语句中的 `WHERE name = ? OR home_url = ? OR (biji_url IS NULL AND biji_topic_alias = ?)` 过于宽泛，当同一个知识库下有多个 `biji_url` 为 `NULL` 的占位记录时，一次性匹配并修改了多行记录，导致多行试图改为同一个名字触发 SQLite UNIQUE 约束机制报错。
* **解决方案**：
  1. 在 `biji_browser.py` 中增加 `normalize_url()` 和 `captured_follow_map` 映射字典，拦截 `2.json` 时提取 `item.get("url")` 并在解析时**优先与当前的 `home_url` 进行 URL 归一化比对**，确保 100% 拿到正确的博主条目。
  2. 重构 `save_biji_url_to_db` SQL 写入逻辑：**先精确查出目标记录的唯一主键 `target_id`**，然后强锁定 `WHERE id = target_id` 进行单行精确更新，彻底斩断多行全量覆盖导致的 `UNIQUE constraint failed` 崩溃！

---

## Bug 41: `NameError: name 'captured_follow_info' is not defined` 变量未定义异常

* **发生时间**：2026-07-29
* **问题现象**：得到关注任务在等待 2.json 回调时抛出 `⚠️ [关注流程提示/异常]: name 'captured_follow_info' is not defined` 报错。
* **主要根源**：重构 `captured_follow_map` 映射字典时，局部作用域中的 `captured_follow_info` 声明被意外遗漏，导致轮询 `if captured_follow_info:` 时抛出 `NameError` 错误。
* **解决方案**：在 `add_blogger_to_biji` 作用域头部显式补充声明 `captured_follow_info = {}`，并将轮询判断条件兼容更新为 `if captured_follow_map or captured_follow_info:`。

---

## Bug 42: 自愈重新加载知识库后未点击"博主" Tab 导致 2.json 无法触发

* **发生时间**：2026-07-29
* **问题现象**：得到关注任务在等待 2.json 超时后进入自愈分支，日志显示"重新加载知识库刷新获取已订阅列表"，但随后依然直接完成（`🎉 [完成] 博主已成功处理完毕！`），未能真正从 2.json 拿到 `follow_id`，`biji_url` 仍为空。
* **主要根源**：`add_blogger_to_biji` 自愈逻辑中（L411），`page.goto(topic_url, wait_until="domcontentloaded")` 仅刷新页面，但得到知识库主页打开后默认展示的不是"博主"列表 Tab，`v1/web/follow` 接口不会被自动触发，因此 `captured_follow_map` 依然是空的，后续比对自然失败。
* **解决方案**：在 `page.goto()` 之后，补充用 `expect_response` 上下文管理器等待 `v1/web/follow` 响应，并在其中主动定位并点击"博主" Tab（多策略选择器：`.n-tabs-tab:has-text('博主'), [data-name='blogger'], xpath=//*[contains(text(),'博主')]`），确保页面真正触发博主列表 API 请求，`captured_follow_map` 被正常填充，自愈成功率达 100%。

---

## Bug 43: 全新 Docker 挂载空目录下未自动初始化 SQLite 表结构引发全量 API HTTP 500 异常

* **发生时间**：2026-07-31
* **问题现象**：在全新的电脑上通过 Docker 运行服务（例如 `-v "${PWD}/data:/app/data" -v "${PWD}/output:/app/output"` 挂载全新的本地空目录）时，打开前端网页发现：
  1. 浏览器账号无法创建（提示 `POST /api/biji/accounts` 500 Internal Server Error）。
  2. 对标博主信息与沙箱列表完全无法加载（提示 `GET /api/bloggers` 和 `GET /api/biji/accounts` 500 Internal Server Error）。
* **主要根源**：
  - 在全新部署或全新的空 `./data` 挂载目录中，物理文件 `distiller.db` 尚未存在。
  - FastAPI 后端 `app.py` 原先依赖本地预存的数据库文件，**在应用启动时未自动触发 `init_db()`、`migrate_database()` 与 `upgrade_db_schema()`**。
  - 当前端发起 API 请求时，SQLite 打开数据库后因为找不到 `bloggers` 和 `biji_browser_accounts` 等数据表，直接抛出 `sqlite3.OperationalError: no such table` 异常，被 FastAPI 捕获返回了 500 服务器错误。
* **解决方案**：
  - 在 `web/backend/app.py` 启动入口封装并显式调用 `ensure_database_initialized()`。
  - 保证在任何全新部署或空挂载目录下，后端服务一启动即可 100% 自动顺序建表并完成 Migration 热升级（自动创建 `bloggers`、`biji_browser_accounts` 等全部依赖表），彻底消灭全新环境部署下的 500 报错。

---

## Bug 44: 得到文案自动化同步缺少后台 Cron 定时更新调度机制

* **发生时间**：2026-08-01
* **问题现象**：得到文案同步原先仅支持在前端手动点击 `⚡ 一键同步得到文案` 触发，缺乏后台自动周期性（如每 1 小时、3 小时、6 小时、12 小时、24 小时）定时搜寻与抓取更新的自动化配置。
* **主要根源**：未在 `app.py` 中构建专属的守护进程 Timer 轮询线程，前端「博主监控管理」顶部操作栏缺少对应的定时参数配置弹窗与状态指示徽章。
* **解决方案**：
  1. 重构 `web/backend/app.py` 中的 `GET /api/biji/schedule` 与 `POST /api/biji/schedule` REST API 接口，支持双模式定时（`daily` 每天固定时间点如 `03:00` 与 `interval` 自定义每隔 X 小时如 1/3/6/12/24 小时），并持久化到 `config.json`。
  2. 精简交互设计：彻底移除 `单博主抓取上限 (max_posts)` 限制（改由博主音视频转录队列驱动全量抓取），移除手动选择浏览器账号下拉框（改由后端根据各博主数据库记录的 `biji_browser_id` 自动匹配驱动其各自关联的沙箱环境）。
  3. 后端 `biji_auto_sync_scheduler_loop` Daemon 后台轮询守护线程自动根据设定的 `daily` 或 `interval` 模式秒级计算触发点，实时自动抓取并在「任务日志」展示。

---

## Bug 45: 「博主监控管理」表格「浏览器账号」列仅显示原始 ID (`account_01`) 缺乏映射昵称直观显示

* **发生时间**：2026-08-01
* **问题现象**：在「博主监控管理」数据表格中，「浏览器账号」一列直接显示了数据库物理标识（如 `account_01`），而无法直观展示用户在后台设置的别名/昵称（如 `得到账号_01` 或 `Get达人`）。
* **主要根源**：后端 `GET /api/bloggers` 在 SQL 查询时未对 `biji_browser_accounts` 数据表进行 `LEFT JOIN` 联查，仅返回了 `b.biji_browser_id` 原始值；前端 `app.js` 仅将其当做 raw string 呈现。
* **解决方案**：
  1. 重构后端 `GET /api/bloggers` 查询 SQL，增加 `LEFT JOIN biji_browser_accounts a ON a.account_id = b.biji_browser_id`，并通过 `COALESCE(NULLIF(a.alias_name, ''), NULLIF(a.nickname, ''), b.biji_browser_id, '得到账号_01')` 优先导出友好别名 `biji_account_name`。
  2. 前端 `app.js` 渲染时优先展示 `biji_account_name`（如 `得到账号_01`），同时通过 `title` 属性保留原始凭据 ID（`account_01`）鼠标悬浮提示，兼顾直观性与开发调试需求。

---

## Bug 46: 「全网热点」列表全量渲染导致 DOM 卡顿，且 GSAP 动画 Stagger 导致 4 号之后项目呈半透明/隐藏

* **发生时间**：2026-08-01
* **问题现象**：打开「全网热点」页面时，网页产生卡顿，且排在 4 号及以后的热搜词显示为半透明、淡化甚至完全无法看到。
* **主要根源**：
  1. 数据库 `trending_topics` 存在相同标题的重复记录，`SELECT *` 一次性查出数十条重复数据全量渲染，导致 DOM 卡顿。
  2. 前端使用 `gsap.from(".trending-item", { opacity: 0, stagger: 0.05 })`，导致后续批次的元素在动画计算期间被强行锁死在 `opacity: 0` / 半透明遮罩状态。
* **解决方案**：
  1. 重构后端 `GET /api/trending` SQL 查询，采用 `GROUP BY title` 自动去重，只保留最新条目。
  2. 重构前端 `loadTrendingTopicsData`，实现**分批懒加载 (Batch Lazy Loading)** 与 **`IntersectionObserver` 触底自动装载**，首屏仅装载 10 条，秒级极速渲染。
  3. 修复动画遮罩：在 GSAP 动画中增加 `clearProps: "all"` 并在 CSS 中强化透明度，确保所有编号热点 100% 清晰呈现。

---

## Bug 47: Go / Bubbletea 命令行 TUI 在无 TTY 管道中黑屏崩溃 (0xC000013A) 与 PTY 窗口尺寸未初始化死锁

* **发生时间**：2026-08-01
* **问题现象**：在 Docker 容器或无物理控制台环境中，拉起 `agy` / `codex` 等基于 Go / Bubbletea 框架的命令行交互工具时，终端控制台黑屏无任何字符输出，或者在 Windows 重定向管道中直接抛出 `0xC000013A` (3221225786) 崩溃退出。
* **主要根源**：
  1. **缺少物理 TTY 句柄**：Go 语言的 Bubbletea 选单库要求真实的 TTY 终端与 ANSI 交互环境。如果直接使用 Python `subprocess.PIPE` 进行标准流重定向，Go 进程会因为检测不到 TTY 或尝试访问底层控制台句柄而崩溃。
  2. **PTY 初始尺寸为 0x0**：在 Linux POSIX 环境下使用 `pty.fork()` 创建伪终端时，若未显式调用 `ioctl` 设定初始窗口宽高（默认为 0 行 0 列），会导致 Bubbletea 一直等待 `SIGWINCH` 窗口 resize 变化信号，陷入永久死锁。
* **解决方案**：
  1. **跨平台双 PTY 会话驱动**：
     - Windows 宿主机：使用 `winpty.PtyProcess` (`PtyProcess.spawn`) 产生 ConPTY 物理会话。
     - Linux / Docker 容器：使用标准库 `pty.fork()`。
  2. **显式窗口尺寸与环境变量注入**：
     - 在 `pty.fork()` 的子进程 (`pid == 0`) 中，必须使用 `struct.pack("HHHH", 24, 80, 0, 0)` 打包并通过 `fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)` 注入 `24x80` 初始列宽。
     - 必须显式声明环境变量 `os.environ["TERM"] = "xterm-256color"` 与 `os.environ["COLORTERM"] = "truecolor"`，唤醒彩色 TUI 界面渲染。

---

## Bug 48: Web PTY 终端接收 OAuth 长 URL 在 80 列宽度切分后导致 Google 返回 400

* **发生时间**：2026-08-01
* **问题现象**：在网页 Web Terminal 中完成 Google OAuth 授权时，点击终端打印出来的授权 URL 链接跳转，Google 提示 `400. 出现了错误。服务器无法处理该请求，因为其格式不正确`。
* **主要根源**：
  * 伪终端默认列宽设为 80 列。当命令行工具打印极长的 OAuth URL（通常 >200 字符，包含 `client_id` 与 `state`）时，PTY 会自动向输出文本中插入 `\n` 换行符。
  * 前端正则若简单按行解析超链接，会把 URL 在中途截断（丢失后半截关键的 `state=` 校验参数），或者在拼接时保留了内部的换行符与空格，导致构造出的 URL 格式破损。
* **解决方案**：
  * 前端使用流式文本拼接器，在提取 URL 前利用正则表达式彻底剔除内部的空白与换行符 (`[\r\n\t\s"'>]+`)。
  * 在校验提取出的字符串完整包含 `https://` 与 `state=` 关键参数后，才在 DOM 中替换生成可直接点击的 `<a href="..." target="_blank">` 按钮，保障一键跳转 100% 正确。

---

## Bug 49: Windows 环境下 `subprocess.Popen` `shell=True` 导致 CLI 命令行参数列表截断与管道块缓冲

* **发生时间**：2026-08-01
* **问题现象**：
  1. 在 Windows 宿主机下拉起 `agy` 智能体拆解任务时，`agy` 没有任何报错但瞬间以退出码 `1` 结束，且未执行任何拆解动作。
  2. 智能体运行过程中，前端的任务日志控制台一直处于空白状态，直到整个任务完全结束才一瞬间吐出全量日志。
* **主要根源**：
  1. **`shell=True` 参数截断**：在 Windows 下如果 `subprocess.Popen` 开启了 `shell=True` 且传入的是命令参数列表 (`['agy', 'analyze', '--verbose']`)，Windows `cmd.exe` 解释器只把列表第一项 `agy` 作为命令执行，后面的参数列表全被抛弃。
  2. **C-stdio 块缓冲区存留**：Python subprocess 在捕获标准输出时默认对管道启用了 C-stdio 4KB 块缓冲，在缓冲区填满前不会向文件写入，导致日志无法实时推送到前端。
* **解决方案**：
  1. 将 `subprocess.Popen` 的 `shell` 参数统一强控制为 `False`（若使用 shell 必须拼接为单条 String），确保 Windows 内核原封不动将 List 参数传给子进程。
  2. 强制传递 `PYTHONUNBUFFERED=1` 环境变量，并在 Python subprocess 管道读取循环中使用无缓冲 `bufsize=0` 和按行 `f.flush()` 强行冲刷磁盘，打通秒级实时推流通道。

---

## Bug 50: 得到 (biji.com) 寻路模式缺失知识库遍历调试日志与 1.json 网络接口未拦截捕获时 self.topics_data 为空问题

* **发生时间**：2026-08-01
* **问题现象**：得到抓取引擎在寻路模式下直接跳转各个知识库但静默跳过了“博主” Tab 的点击，且未打印任何点击与卡片对比的调试日志，最终提示 `⚠️ 未在得到中找到博主『xxx』的对应关注卡片，跳过。`。
* **主要根源**：
  1. **expect_response 包包裹错误与静默吞异常**：原代码用 `expect_response` 尝试同时包裹 `page.goto` 和 `blogger_tab.click()`，若页面未完全渲染或 `xpath=//*[text()='博主']` `is_visible` 校验失败（如包含子节点或空白符），代码落入 `except:` 默默 `pass` 忽略，未进行点击且未打印任何日志。
  2. **比对逻辑过于严格**：博主名比对原仅支持 `f_name == b_name` 完全相等，当得到关注卡片上的名字为 `小A学财经` 而本地主库博主名为 `小A` 时比对失败。
* **解决方案**：
  1. 解耦 `page.goto` 与 `blogger_tab` 点击，引入多重备用选择器（如 `.n-tabs-tab:has-text('博主')`, `div[role='tab']:has-text('博主')`）。
  2. 补全寻路过程中【打开页面】->【定位 Tab】->【点击 Tab 触发 2.json】->【输出所有捕获到的卡片】->【比对 URL 与名称】的**全流程粒度日志**。
  3. 增强匹配规则：支持 URL 规范化/ID 提取匹配、完全同名匹配，以及双向模糊包含匹配（如 `小A` 与 `小A学财经`）。
  4. 引入**关键点击节点现场截屏与强终止机制**（`capture_error_screenshot`）：在“添加”、“订阅直播/博主”、“抖音博主 Tab”、“确定”以及寻路“博主 Tab”等核心点击节点上，一旦定位或点击失败，禁止静默跳过，立刻截取 Viewport 全屏快照保存至 `screenshots/biji_error_*.png` 并抛出 RuntimeError 终止流程，供 Web 看板自动抓取展现现场。

---

## Bug 51: Google 智能体 OAuth 终端登录时缺失代理环境变量注入导致的换码失败问题

* **发生时间**：2026-08-03
* **问题现象**：在 Docker 容器或设置了 HTTP 代理（如 `http://host.docker.internal:7890`）的网络环境下，在网页端点击开启 Google 智能体授权登录时，命令行控制台报错 `Got an error: token exchange failed: Post "https://oauth2.googleapis.com/token"`。
* **主要根源**：
  * 后端 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L3048) 中在拉起终端登录 WebSocket / PTY 进程时，注入代理环境变量的判断条件写死为了 `if proxy_url and provider == "openai":`。
  * 导致当 `provider == "google"` 时，全局配置的 `proxy_url` 被强行剥离过滤，没有注入到 `agy` / `opencode` 进程的环境变量中。Go 语言 CLI 工具在无代理状态下直接向 `https://oauth2.googleapis.com/token` 发起连接请求，进而导致网络超时与 Auth Token 交换失败。
* **解决方案**：
  * 修改 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L3048) 代理注入条件为 `if proxy_url:`，去掉对 `provider` 的限定。
---

## Bug 52: 拉取 Google CLI 可用模型列表时缺失代理注入与 5 秒超时异常 (`agy models timed out after 5 seconds`)

* **发生时间**：2026-08-03
* **问题现象**：在前端“智能体授权”页面点击拉取 Google 可用模型列表时，后台控制台报错 `❌ 请求模型进程超时或发生异常: Command '['/root/.local/bin/agy', 'models']' timed out after 5 seconds`。
* **主要根源**：
  * 后端 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L2835) 中的模型拉取路由 `/api/auth/cli/models` 在通过 `subprocess.run` 调用 `/root/.local/bin/agy models` 时，缺少了将 `proxy_url` 环境变量注入 `env` 的逻辑，导致容器内的 `agy` 进程在无代理环境下试图直连 Google 网关。
  * 同时，该接口写死了 `timeout=5` 秒超时，在代理握手或网络延迟较高时极易被击穿。
* **解决方案**：
  * 在 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L2835) 中的 `/api/auth/cli/models` 路由里补齐 `proxy_url` 大小写代理环境变量的注入。
---

## Bug 53: Google OAuth 授权 URL 自动捕获包含 Bubbletea TUI 底部导航页码杂质问题

* **发生时间**：2026-08-03
* **问题现象**：在 Web 终端进行 Google 授权登录时，网页端顶部自动弹出的“点击跳转授权”按钮中包裹的 OAuth URL 尾部带有 `(1–20of24lines)shift+up/downNavigate` 等无关的 TUI 终端翻页提示字符，点击跳转后 Google 提示 `400 / 格式不正确`。
* **主要根源**：
  * `agy` CLI 在伪终端（PTY）中渲染界面时，尾部会带有 TUI 的底栏翻页提示信息（如 `(1–20of24lines)shift+up/downNavigate`）。
  * 前端 `app.js` 的 `extractCleanAuthUrl` 在从终端流文本提取 URL 时，结束关键词列表未能匹配全小写的 `shift+up` 和 Unicode 连字符 `(1–`，导致 URL 末尾的 `state=` 参数后面粘连了这段翻页提示字符。
* **解决方案**：
  * 在 [app.js](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/frontend/app.js#L3714) 的 `extractCleanAuthUrl` 中扩充结束关键词列表（包含 `shift+up`, `lines)`, `(1–` 等）。
---

## Bug 54: FastAPI 启动报错 `NameError: name 'Request' is not defined`

* **发生时间**：2026-08-04
* **问题现象**：Docker 容器启动拉起 FastAPI 后端时控制台报错 `NameError: name 'Request' is not defined. Did you mean: 'requests'?` 导致容器不断崩溃重启。
* **主要根源**：在 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L14) 头部导入 `from fastapi import ...` 时，未导入 `Request` 类，而在 `/api/blogger/distill/run` 函数签名中声明了 `request: Request`。
---

## Bug 55 / 优化: 任务日志控制台默认截断 150 行导致排查困难，新增「全量无截断日志弹窗」

* **发生时间**：2026-08-04
* **问题现象**：在「任务日志」模块查看长时间运行或详细步骤日志时，实时控制台默认只截取末尾 150 行（`lines[-150:]`），导致日志开头的初始化过程、部分异常报错堆栈或完整参数上下文被截断，难以定位故障原因。
* **主要根源**：后端 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L4710) 的 `/api/crawl/status/{task_id}` 接口未提供无截断拉取模式，前端控制台亦缺乏“查看全量”与“一键复制”操作。
* **解决方案**：
  * 后端 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py#L4710) 的 `get_crawler_status` 支持 `full: bool = Query(False)` 查询参数。当传入 `full=true` 时，后端返回 100% 完整的日志全文。
  * 前端 [index.html](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/frontend/index.html) 在 Live Console 顶部右侧新增 **「📜 查看完整日志 (无截断)」** 与 **「📋 复制当前输出」** 两个按钮，底部新增全屏弹窗 `#modal-full-log`。
---

## Bug 56: 后端重复注册 `get_crawler_status` 接口覆盖 `full=true` 无截断逻辑，且弹窗默认滚动至底部

* **发生时间**：2026-08-04
* **问题现象**：在全量日志弹窗中点击打开日志时，顶部依然直接从第 13 条记录（`处理进度 (13/30)`）开始展示，前 12 条记录仍然缺失被截断。
* **主要根源**：
  1. [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py) 中定义了两个重名的 `@app.get("/api/crawl/status/{task_id}")` 路由（分别在 4293 行和 4715 行）。FastAPI 以后者为准，导致支持 `full=true` 的逻辑被底部未升级的旧函数直接覆盖。
  2. 前端 [app.js](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/frontend/app.js) 的 `openFullLogModal` 之前写死了 `contentEl.scrollTop = contentEl.scrollHeight`，导致打开弹窗时直接强制拉到底部，用户需要手动向上滑才能看到顶部记录。
* **解决方案**：
  * 彻底移除 [app.py](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/backend/app.py) 中重复的第二个 `get_crawler_status` 函数定义，确保全局唯一的 Endpoint 100% 响应 `full=true` 参数返回从第 1 行起的所有全量字符。
---

## Bug 57: 浏览器缓存 `index.html` 导致弹窗 DOM 节点不存在，触发 `TypeError: Cannot set properties of null`

* **发生时间**：2026-08-04
* **问题现象**：在前端点击「查看完整日志」时控制台报错 `Uncaught (in promise) TypeError: Cannot set properties of null (setting 'textContent') at HTMLButtonElement.openFullLogModal`。
* **主要根源**：用户浏览器缓存了老版本的 `index.html` HTML 模版，尚未载入底部的 `#modal-full-log` 容器，而 JavaScript 在尝试获取 `document.getElementById("full-log-modal-sub")` 时拿到 `null` 并直接修改其 `.textContent`。
* **解决方案**：
  * 在 [app.js](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/frontend/app.js#L4088) 的 `openFullLogModal` 中加入**强力防御机制**：检测到 `#modal-full-log` 缺失时，在 JS 内存中即时动态创建该 Modal 节点并注入到 `document.body` 中，并给全局 DOM 操作加上可选链保护（`titleSub?.textContent = ...`）。
  * 在 [index.html](file:///d:/daima/codex/蒸馏/blogger-distiller-main/web/frontend/index.html#L1192) 尾部将 `app.js` 的版本查询参数更新为 `?v=20260804_1`，强制刷新浏览器静态缓存。











