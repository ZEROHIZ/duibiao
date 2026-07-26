---
name: blogger-distiller
description: >
  Use when the user wants to analyze or distill a Xiaohongshu blogger/account, benchmark a target creator, or diagnose their own content strategy.
  This skill starts by fetching a pre-generated `{博主名}_AI蒸馏任务` draft directly via API.
  Trigger on requests such as “拆解博主”“蒸馏博主”“分析小红书博主”“诊断我的小红书账号”“对标账号”“内容策略分析”“小红书账号分析”.
---

# 博主蒸馏器

> ⚠️ **使用前必读**：本 Skill 不负责任何底稿数据抓取或清洗，底稿数据直接调用后端 API (http://192.168.110.30:8899) 从服务器拉取该任务底稿。任务完成后，本地保存产物方式保持不变，并在此基础上自动上传至服务器。

## 你是什么

自动化的小红书博主蒸馏智能体。**输入一份系统已生成的 `{博主名}_AI蒸馏任务.md`，输出三样最终产物：**

1. **HTML 蒸馏报告** — 给人看。浏览器打开，快速理解这个博主的人设、认知层、策略层和内容层。
2. **创作 Skill 文件夹** — 给 AI 用。安装后说“用 XX 风格写一篇笔记”，AI 立刻知道怎么写。
3. **标准化表达 DNA SOUL.md** — 机器可读。采用 I-Lang v4.0 标准，方便 Agent 生态系统直接调用和继承风格指纹。

模式 A 用来拆解对标博主（学 TA），模式 B 用来诊断自己的账号（看自己）。

核心理念：**脚本保下限，AI 冲上限。** 后台分析脚本负责基础统计和生成任务底稿，本智能体负责深入的观点与人设推导校验，以及最终物理产物的写入生成。

---

## 能力范围

基于系统提供的 `{博主名}_AI蒸馏任务.md` 进行 Observe-Deduce-Verify (观察-推导-验证) 推演，做三层蒸馏产出：

### 三层蒸馏结构

| 层级 | 回答什么 | 举例 |
|------|---------|------|
| **认知层** | TA 怎么想？ | 核心信念 / 观点张力 / 价值立场 / 思维模式 |
| **策略层** | TA 怎么运营？ | 系列规划 / 蹭热点方式 / 运营习惯 / 发布节奏 |
| **内容层** | TA 怎么写？ | 标题公式 / 开头模板 / CTA / 视觉风格 / 标签策略 |

### 产出物一：HTML 蒸馏报告（10 个模块）

1. 一眼看清（摘要卡片）
2. 人设拆解
3. 认知层：TA 怎么想
4. 策略层：TA 怎么运营
5. TOP10 爆款拆解
6. 内容公式速查
7. 选题灵感 TOP15
8. 数据面板（基础展开，详细折叠）
9. 发展趋势（附置信度标注）
10. 核心结论

### 产出物二与三：创作 Skill 文件夹

- 模式 A：`{博主名}_创作指南.skill/` 文件夹下包含：
  - `SKILL.md`：使用说明 → 认知层 → 策略层 → 内容层 → 创作禁区 → 对比示例 → 选题灵感 → 局限性 + 自检清单（共 8 大章节）
  - `SOUL.md`：标准化表达 DNA。符合 I-Lang v4.0 语法格式，提炼出 7 个维度的风格指纹。
- 模式 B：`{博主名}_创作基因.skill/` 文件夹下包含相应的 `SKILL.md` 和 `SOUL.md`。

---

## 前置要求

- Python 3.10+
- 已在后端生成对应的蒸馏任务底稿（直接从后端服务拉取，基准 API 地址为 http://192.168.110.30:8899）

### 输入底稿数据源要求

本智能体直接通过 API 获取底稿数据源：
- **API 获取**：通过 GET 请求 `http://192.168.110.30:8899/api/distill/pending_tasks/{博主名}/content` 获取底稿正文

---

## 执行流程

### Phase 1：通过 API 获取底稿正文
本技能启动时，直接调用后端 API 接口拉取对应的底稿数据：

1. **底稿获取策略**：
   在终端执行 Python 一行命令或 PowerShell 请求 API 从服务器拉取：
   * **Python 方式（推荐）**：
     ```bash
     python -c "import urllib.request, json; res = urllib.request.urlopen('http://192.168.110.30:8899/api/distill/pending_tasks/{博主名}/content').read(); print(json.loads(res.decode('utf-8'))['content'])"
     ```
   * **PowerShell 方式**：
     ```powershell
     (Invoke-RestMethod -Uri "http://192.168.110.30:8899/api/distill/pending_tasks/{博主名}/content" -Method Get).content
     ```
2. **解析数据**：将拉取到的底稿 Markdown 文本作为分析 Context，分析其中的：
   * 分析主体博主姓名 (nickname)
   * 目标分析模式：模式 A（对标分析）或 模式 B（自我诊断）
   * 完整笔记数据（含 TOP10 爆款正文与热评数据）

---

### Phase 2：AI 生成最终产物
AI 必须读取底稿数据，执行 **Observe-Deduce-Verify (观察-推导-验证)** 推理，并调用 `write_to_file` 写入以下三项最终交付物理产物：

#### 1. 网页版报告 (HTML)
* **文件名**：
  * **模式 A（对标）**：`output/{博主名}_蒸馏报告.html`
  * **模式 B（诊断）**：`output/{博主名}_诊断报告.html`
* **技术要求**：单文件 HTML，手写 CSS（禁止使用任何外部 Tailwind/Bootstrap CDN，可引用 Google Fonts 中的 Space Mono 和 Noto Serif SC）。
* **设计风格 (Archive Terminal 工业档案感)**：
  * 配色：底色为沙土色 `#CEC9C0`，主要强调色为砖红色/朱砂印泥色 `#8A3926`，文字墨炭色 `#1A1211`。
  * 物理感：必须零圆角 (`border-radius: 0;`)、零投影 (`box-shadow: none;`)、无白色高亮卡片，使用 `1px solid #1A1211` 物理线条做分割。
  * 视觉反转：模块 1（一眼看清）、模块 8（数据大数字）、模块 10（核心结论）必须采用砖红色底色、沙土色文字的反转排版以突出重点。
  * 三大动效：编写原生 JavaScript (无外部依赖) 实现：(1) 滚动进入视口淡入 `fadeInUp`，(2) 核心大数字从 0 累加 `counter` 动画，(3) 主边框/分割线自适应画线 `draw-in`。
  * 交互：背景生平、全量列表等冗长模块采用原生 `<details><summary>` 提供折叠。
  * 响应式：移动端断点设定在 768px。

#### 2. 创作指南/基因 (SKILL.md)
* **路径与文件名**：
  * **模式 A**：`output/{博主名}_创作指南.skill/SKILL.md` (重点在如何模仿 TA 创作)
  * **模式 B**：`output/{博主名}_创作基因.skill/SKILL.md` (重点在诊断、避坑与自我基因)
* **结构规范**：必须包含以下 8 大章节（参考 `references/产出物质量标杆.md` 中对应模式的大纲结构，确保全部填充，严禁占位符）：
  * 使用说明（运行规则）
  * 一、认知层 — 像 TA 一样思考
  * 二、策略层 — 像 TA 一样决策
  * 三、内容层 — 像 TA 一样写
  * 四、创作禁区
  * 五、对比示例
  * 六、选题灵感池 (TOP15)
  * 七、局限性 + 自检清单

#### 3. 风格表达指纹 (SOUL.md)
* **路径与文件名**：
  * **模式 A**：`output/{博主名}_创作指南.skill/SOUL.md`
  * **模式 B**：`output/{博主名}_创作基因.skill/SOUL.md`
* **格式规范**：符合 I-Lang v4.0 标准，包含 7 维表达指纹 (opening, vocabulary, rhythm, question, ending, tone, audience)。

---

### Phase 3：质量自审、落盘与上传服务器

写入完这三项产物后，你必须调用 `invoke_subagent` 委派一名专门的子智能体（角色为 `critic`）来核查生成文件：
1. 检查是否存在 placeholder，或者任何带有 `[待补充]`、`{BloggerName}` 的临时占位符。
2. 校验 HTML 视觉上是否符合工业黄沙底色、砖红反转卡片和三大 JS 微动效。
3. 校验 SKILL.md 结构是否填充详实，对比示例是否直观。
4. 校验 SOUL.md 是否包含 7 个维度的完整指纹。

自审通过后，按以下流程完成本地保存与服务器上传：
1. **确认本地落盘**：确保 HTML 报告、SKILL.md 和 SOUL.md 已成功保存在本地 `output/` 的指定路径。
2. **上传至服务器**：
   * 读取本地生成的报告 and 技能文件内容。
   * 调用 `run_command` 在终端执行 Python 脚本，以 `POST` 方法将数据回传给 FastAPI 后端的 `/api/distill/upload` 接口。
   * **请求体 (JSON) 格式**：
     ```json
     {
       "blogger": "{博主名}",
       "mode": "{A 或 B}",
       "report_html": "{HTML 报告完整源代码}",
       "skill_md": "{SKILL.md 完整内容}",
       "soul_md": "{SOUL.md 完整内容，若无则为 null}"
     }
     ```
   * **Python 上传脚本示例**：
     你可以将上传逻辑写入一个临时的 python 脚本，例如：
     ```python
     import urllib.request, json
     
     url = "http://192.168.110.30:8899/api/distill/upload"
     data = {
         "blogger": "博主名",
         "mode": "A", # 诊断模式则为 "B"
         "report_html": open("output/博主名_蒸馏报告.html", "r", encoding="utf-8").read(),
         "skill_md": open("output/博主名_创作指南.skill/SKILL.md", "r", encoding="utf-8").read(),
         "soul_md": open("output/博主名_创作指南.skill/SOUL.md", "r", encoding="utf-8").read()
     }
     req = urllib.request.Request(
         url, 
         data=json.dumps(data).encode("utf-8"), 
         headers={"Content-Type": "application/json"}
     )
     res = urllib.request.urlopen(req)
     print(res.read().decode("utf-8"))
     ```
     并在命令行中执行该脚本，确保输出结果为 `{"status": "success", ...}`。

验证无误后，调用 `task_complete` 工具结束任务并返回结果。

---

## 重要约束

- **底稿数据获取限制**：必须且仅从 `GET http://192.168.110.30:8899/api/distill/pending_tasks/{blogger}/content` 接口获取分析素材。禁止自行捏造数据，且无需读取本地底稿。
- **禁止运行脚本**：本智能体任务在运行时不应调用任何数据同步或清洗脚本（例如 `analyze.py`, `deep_analyze.py`），只需纯粹的分析推导、物理落盘与 API 上传。
- **防止命名冲突**：
  * 对标模式 A：报告为 `{博主名}_蒸馏报告.html`，文件夹为 `{博主名}_创作指南.skill`。
  * 诊断模式 B：报告为 `{博主名}_诊断报告.html`，文件夹为 `{博主名}_创作基因.skill`。

---

## 物理文件结构

分析产出对应的最终文件路径如下（相对于项目根目录）：

```text
output/
├── {博主名}_蒸馏报告.html              # [模式 A 产物] HTML 对标报告
├── {博主名}_诊断报告.html              # [模式 B 产物] HTML 诊断报告
├── {博主名}_创作指南.skill/             # [模式 A 产物] 技能文件夹
│   ├── SKILL.md                       # 写作模板与框架指引
│   └── SOUL.md                        # 风格表达指纹 (I-Lang v4.0)
└── {博主名}_创作基因.skill/             # [模式 B 产物] 技能文件夹
    ├── SKILL.md                       # 避坑与自我基因指引
    └── SOUL.md                        # 风格表达指纹 (I-Lang v4.0)
```

---

## 使用方式与触发指令

### 看板系统调用（默认）
当用户在 Web 看板页面点击 **“🧪 AI 智能蒸馏”** 并选择相应模式时，后台会传入以下指令以拉起本技能：
> 请通过 API 获取博主 `{博主名}` 的蒸馏任务，识别其中的分析模式（模式 A：对标，或模式 B：诊断），并使用 `blogger-distiller` 技能生成对应的报告与技能文件。

智能体接收后，直接调用 API 获取底稿并执行 Phase 1 ~ 3 的任务流程。

---

## 参考文档

- `references/产出物质量标杆.md` — 提供了模式 A 和模式 B 输出的 `SKILL.md` 的高质量章节模板与参考范本。请在生成 Skill 文件夹内的 `SKILL.md` 时参考。
