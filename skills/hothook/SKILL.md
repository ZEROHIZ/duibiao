---
name: hothook
description: 分析并拆解数据库中已有的单篇爆款视频。当用户需要对某篇已抓取的视频进行深度分析、钩子（hook）拆解、脚本结构提取、爆款归因和改写建议时使用。本 Skill 避开所有浏览器数据采集，直接读取本地 SQLite 数据库中的视频及评论详情。
---

# HotHook (数据库驱动版)

## 核心规则

避开所有浏览器自动化采集与视频下载步骤。分析所需的数据完全从本地 SQLite 数据库 `data/distiller.db` 中的 `blogger_notes` 表中直接读取。

分析结果应以 Markdown 格式撰写，并最终调用 HTML 编译器脚本生成一份单文件 `.html` 格式的深度拆解报告，保存在 `output/` 目录下。

## 数据来源与字段映射

当需要对某篇视频进行拆解时，读取 `distiller.db` 中对应记录的以下字段：
- `title`：视频标题
- `desc`：完整口播逐字稿内容（由 Whisper 转录或导入的文本）
- `likes`, `collects`, `comments`, `shares`：点赞、收藏、评论、分享数（用于数据面板）
- `tags_json`：视频话题标签
- `comments_json`：评论区热评列表（用于评论洞察和归因）
- `published_at`：发布时间
- `blogger_name`（联查自 `bloggers` 表）：视频所属博主姓名

---

## 视频拆解执行流程

### Phase 1：定位与读取数据

1. **定位视频**：根据用户提供的**视频 ID**（`blogger_notes` 中的 `id`）或**视频标题关键字**，在 SQLite 数据库中查询目标视频。
2. **查询 SQL 示例**：
   ```sql
   SELECT n.id, n.title, n.desc, n.likes, n.collects, n.comments, n.shares, n.category, n.tags_json, n.comments_json, n.published_at, b.name as blogger_name
   FROM blogger_notes n
   JOIN bloggers b ON n.blogger_id = b.id
   WHERE n.id = '<视频ID>' OR n.title LIKE '%<关键字>%';
   ```
3. **确认数据完整性**：
   - 提取 `desc` 作为逐字稿数据源。
   - 解析 `comments_json`（JSON 数组）提取热评列表。
   - 提取互动计数用于互动率计算。

---

### Phase 2：AI 深度分析与推理

读取上述数据库字段后，遵循 [references/breakdown_templates.md](file:///d:/daima/codex/蒸馏/blogger-distiller-main/skills/hothook/references/breakdown_templates.md) 的模板规范，开展深度拆解。**你必须严格按照带序号的模块标题（如 `## 01 / 数据面板`）进行输出**，前端编译器将根据这些标题自动生成高级工业风网格排版。

1. **`## 01 / 一眼看清 (数据面板)`**：
   - 必须包含一个 Markdown 表格，列出：点赞数、收藏数、评论数、分享数、互动率（(赞+藏+评)/播放量）。
   - 编译器会自动将此模块渲染为深色反转背景，并将此表格转为动态翻牌器 Dashboard。
2. **`## 02 / Hook 拆解`**：
   - 提取前 5 秒核心钩子原文（加粗高亮冲突点）。
   - 判定 Hook 类型。阐述滑停机制。
   - 给出改写建议时，如有条件推演，请使用特殊的 If-Then 引用块语法：
     `> [IF-THEN] IF: 如果目标是... THEN: 建议改写为...`
     编译器会自动渲染特殊的警告块样式。
3. **`## 03 / 原文逐字稿提取`**：
   - 直接展示从 `desc` 字段中提取的完整转录文本。适当分段。
4. **`## 04 / 叙事结构图谱`**：
   - 用 Markdown 表格拆解：段落/时间戳 | 内容摘要 | 核心作用 | 技巧点。
5. **`## 05 / 爆款归因剖析`**：
   - 提炼 2-3 个爆款机制，必须引用逐字稿或热评原文作为**事实证据**。
6. **`## 06 / 改写落地方向`**：
   - 给出 3 个针对性的改编方向及大纲。同样可多使用 `> [IF-THEN]` 语法。
7. **`## 07 / 核心结论`**：
   - 提炼 3 个可复刻动作和 3 个避坑指南。编译器会自动将此模块渲染为深色底色。

---

### Phase 3：生成单文件 HTML 报告

1. **写入临时 Markdown**：将上述深度拆解的分析结果整理为标准的 Markdown 文本，并临时写入 `output/report.md`。
2. **调用 HTML 编译器**：
   在终端执行 Python 脚本，将 Markdown 报告编译为杂志感排版风格的单文件 HTML：
   ```bash
    python skills/hothook/scripts/generate_single_html_report.py --markdown output/report.md --out output/<视频标题>_单视频拆解.html --title "HotHook 单视频拆解报告 — <视频标题>"
   ```
3. **清理临时文件**：删除临时的 `output/report.md`。
4. **交付产物**：返回生成的 HTML 报告的物理路径 `output/<视频标题>_单视频拆解.html`。

---

## 质量合格红线

- **严禁捏造数据**：数据面板的数字必须与 SQLite 数据库中查询出的数字完全一致。
- **逐字稿真实性**：逐字稿模块必须使用 `desc` 里的真实口播文本，绝不允许凭空撰写或精简重写。
- **证据与结论绑定**：分析“为什么能爆”时，必须引用逐字稿的句子或 `comments_json` 里的热评作为事实证据支撑，禁止主观臆测。
- **单文件交付**：最终产物必须是单个自包含的 HTML 文件，禁止仅在对话框中输出纯 Markdown 文本。
