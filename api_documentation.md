# 完整的后台外部对接 API 文档

本文档旨在详尽说明博主蒸馏器系统（FastAPI 后端）暴露出的所有可用 API。此文档既包含用于自动化对接 Codex/定时任务的**底层蒸馏引擎接口**，也包含外部系统用于查询 SQLite 数据库中**对标数据、爆款笔记、思维模型及热搜热点**的查询接口。

默认服务调用地址（Base URL）：`http://192.168.110.30:8899`

---

## 模块一：核心蒸馏自动化流对接 (Core Distillation Integration)

此部分接口用于实现 **获取待办 -> 拉取底稿 -> 提交报告/基因库** 的自动化流。

### 1. 获取待蒸馏博主列表
扫描并列出所有已经生成了分析底稿、但尚未落盘最终诊断或需要重新诊断的博主任务。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/distill/pending_tasks`
* **响应**：
```json
[
  {
    "blogger": "小A",
    "filename": "小A_AI蒸馏任务.md",
    "filepath": "output/_过程文件/原始素材/小A_AI蒸馏任务.md"
  }
]
```

### 2. 获取博主分析底稿正文
用于拉取指定博主蒸馏任务底稿的完整 Markdown 文本。此内容通常作为大模型分析的 Context（上下文输入）。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/distill/pending_tasks/{blogger}/content`
* **路径参数**：`blogger` (如：`小A`)
* **响应**：
```json
{
  "blogger": "小A",
  "content": "# 小A AI蒸馏底稿\n\n## 爆款笔记列表..."
}
```

### 3. 上传 AI 蒸馏/诊断结果
大模型提炼完成后，调用此接口将生成的文本数据回传给后端，后端会自动将其结构化物理落盘至 `output/`。
* **HTTP 方法**：`POST`
* **接口路径**：`/api/distill/upload`
* **请求头**：`Content-Type: application/json`
* **请求体 (Body)**：
  - `blogger` (string, 必填): 博主昵称（如 `小A`）
  - `mode` (string, 选填, 默认 `"A"`): `"A"` 为对标模式，`"B"` 为诊断模式
  - `report_html` (string, 必填): HTML 报告源代码
  - `skill_md` (string, 必填): 包含排版规约的 SKILL 技能文本
  - `soul_md` (string, 选填): 沉淀的灵魂属性或人设文本

---

## 模块二：数据库对标信息查询 (Benchmarking & Database)

用于外部调用以快速获取大盘博主生态与核心爆款内容，提供客观的外部对标参照系。

### 4. 获取博主大盘列表及对标信息
获取数据库中所有在监视/对标列表中的博主，包含全网转、评、赞的平均数据指标以及他们最近发布的一条动态摘要。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/bloggers`
* **响应示例**：
```json
[
  {
    "id": 1,
    "name": "某知名博主",
    "home_url": "https://v.douyin.com/xxx",
    "total_notes": 128,
    "video_count": 120,
    "normal_count": 8,
    "avg_likes": 15000,
    "avg_collects": 3200,
    "avg_comments": 800,
    "total_likes": 1920000,
    "total_collects": 409600,
    "total_comments": 102400,
    "latest_note_title": "这个冬天你一定要学会的10个穿搭技巧",
    "latest_note_time": "2026-07-08T10:00:00"
  }
]
```

### 5. 获取指定博主的爆款笔记
获取目标博主的笔记列表，默认按点赞量 `likes` 降序排列。可用于外部系统拉取爆款特征以供分析。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/bloggers/{name}/notes`
* **查询参数 (Query)**：
  - `limit` (int, 选填, 默认 `50`): 返回数据的最大条数
* **响应示例**：
```json
[
  {
    "id": 101,
    "title": "如何打造氛围感",
    "desc": "氛围感其实很简单，核心在于光影...",
    "type": "video",
    "likes": 88000,
    "collects": 12000,
    "comments": 4500,
    "shares": 3000,
    "category": "美妆/穿搭",
    "tags_json": "[\"穿搭\", \"氛围感\"]",
    "comments_json": "[{\"content\": \"太实用了！\", \"likes\": 200}]",
    "published_at": "2026-06-25T14:30:00"
  }
]
```

---

## 模块三：思维模型、灵感库与热点 (Knowledge & Trends)

用于调取项目中沉淀的业务模型框架和外部流式的行业新闻、热点资讯，便于大模型分析时充当强化提示词或背景知识。

### 6. 获取思维模型列表 (Knowledge Base)
获取沉淀下来的认知和方法论卡片集合，支持组合搜索。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/knowledge`
* **查询参数 (Query)**：
  - `niche` (string, 选填): 根据特定赛道筛选 (如 `美妆`, `编程`)
  - `q` (string, 选填): 模糊搜索关键词（匹配主题 `topic` 或见解 `insight`）
* **响应示例**：
```json
[
  {
    "id": 1,
    "topic": "黄金三秒开头法则",
    "niche": "短视频编导",
    "insight": "前3秒必须提供情绪价值或极度悬疑冲突。",
    "pitfall": "避免冗长的自我介绍，用户耐心极低。",
    "analogy": "就像你在超市试吃，第一口必须足够惊艳。",
    "created_at": "2026-07-09T18:00:00"
  }
]
```

### 7. 新增一条思维模型
外部 AI 在总结出绝妙的方法论后，可将其结构化回写至系统。
* **HTTP 方法**：`POST`
* **接口路径**：`/api/knowledge`
* **请求体 (Body)**：
```json
{
  "topic": "情绪共振循环",
  "niche": "私域运营",
  "insight": "基于弱联系打造共情点...",
  "pitfall": "过于商业化说教",
  "analogy": "和朋友深夜谈心"
}
```

### 8. 获取全网行业快讯 (Industry News)
拉取外部快讯缓存，支持外部应用感知宏观行业动向。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/news`
* **响应**：返回 `industry_news_cache` 表中的最新数据。

### 9. 获取全网热点趋势 (Trending Topics)
获取热搜与流量话题（可用于热点结合或流量借势分析）。
* **HTTP 方法**：`GET`
* **接口路径**：`/api/trending`
* **响应**：返回 `trending_topics` 列表，包含各平台热词和指数。

---

> [!TIP]
> **自动化集成与智能体配合指引**：如果您要在 Codex 中部署或调用本系统的 AI 智能体，标准执行逻辑为：
> 1. **获取任务底稿**：直接通过 `GET /api/distill/pending_tasks/{blogger}/content` 接口从后台拉取底稿内容，无需进行本地底稿文件检测。
> 2. **AI 分析与本地落盘**：加载底稿后，AI 智能体启动深度推理并生成 HTML 报告、SKILL.md 和 SOUL.md，首先将这三项物理产物写入到本地的 `output/` 对应路径。
> 3. **结果回传与同步**：在本地成功保存后，读取本地文件并以 POST 请求调用 `/api/distill/upload` 接口将最终生成的报告和技能文件内容上传给后端，确保 Web 看板系统可实时展现。
