---
name: blogger-distiller
description: >
  Use when the user wants to analyze or distill a Xiaohongshu blogger/account, benchmark a target creator, or diagnose their own content strategy.
  This skill starts from a user-provided `<博主名>_notes_details.json` file and skips all data-collection steps.
  Trigger on requests such as “拆解博主”“蒸馏博主”“分析小红书博主”“诊断我的小红书账号”“对标账号”“内容策略分析”“小红书账号分析”.
---

# 博主蒸馏器

> ⚠️ **使用前必读**：本 Skill 不负责抓取数据，也不负责申请或配置 TikHub。流程从用户已经准备好的 `*_notes_details.json` 开始。

## 你是什么

自动化的小红书博主蒸馏工具。**输入一份用户提供的 `<博主名>_notes_details.json` 与可选的博主背景生平，输出三样最终产物：**

1. **HTML 蒸馏报告** — 给人看。浏览器打开，快速理解这个博主的人设、认知层、策略层和内容层。
2. **创作 Skill 文件夹** — 给 AI 用。安装后说“用 XX 风格写一篇笔记”，AI 立刻知道怎么写。
3. **标准化表达 DNA SOUL.md** — 机器可读。采用 I-Lang v4.0 标准，方便 Agent 生态系统直接调用和继承风格指纹。

模式 A 用来拆解对标博主（学 TA），模式 B 用来诊断自己的账号（看自己）。

核心理念：**脚本保下限，AI 冲上限。** 脚本负责确定性分析和蒸馏底稿，AI 负责观点与人设推导校验，以及最终产物生成。

---

## 能力范围

基于用户提供的 `*_notes_details.json` 做三层蒸馏产出：

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
- 模式 B：`{用户名}_创作基因.skill/` 文件夹下包含相应的 `SKILL.md` 和 `SOUL.md`。

### 分工

**脚本做 30%**（保下限）：
- 校验输入 JSON 是否可分析
- 统计分析（11 种标题模式、6 类 CTA、藏赞比、发布频率）
- 认知层粗提取（观点句候选、思维模式统计、价值词）
- 数据底稿 + AI 蒸馏任务生成

**AI 做 70%**（冲上限）：
- 生成 HTML 蒸馏报告
- 生成创作 Skill 文件夹
- 抽取信念、张力、框架、创作禁区、对比示例
- 因果分析、个性化建议、金句总结

---

## 前置要求

- Python 3.10+
- 用户已经提供对应的 `*_notes_details.json`
- 建议文件名尽量符合 `<博主名>_notes_details.json`，这样后续命名最稳定
- 如果文件名无法直接看出博主名，必须额外向用户确认用于产出物命名的“博主名”或“账号名”

### 输入文件要求

必须由用户直接提供或明确给出本地路径，例如：

- `./data/张三_notes_details.json`
- `D:\projects\distill\data\李四_notes_details.json`

最低要求是该 JSON 中包含可用于分析的笔记详情数据。Skill 默认它已经是“详情级”数据，而不是只有标题和互动数的列表数据。

---

## 执行流程

### Phase 0：收集最小输入

开始执行前，先向用户确认或从上下文中拿到以下信息：

1. `details_path`：`<博主名>_notes_details.json` 的本地路径
2. `user_mode`：`A` 或 `B`
3. `nickname`：用于生成报告和 Skill 名称的博主名 / 用户名
4. `bio`：（可选）博主生平背景信息，用于 Observe-Deduce-Verify 推导

如果用户只说“拆解这个博主”，但已经给了 JSON 文件，则：

- 优先从文件名推断 `nickname`
- 再让用户只补充 `A` / `B` 模式

如果用户给的是模式 B，且文件本身就是他自己的账号详情 JSON，则 `nickname` 使用用户账号名。

### Phase 0.5：前置交互

如果上下文里还没有足够信息，展示精简交互文案：

```text
欢迎使用博主蒸馏器。

这个版本直接从你提供的 `*_notes_details.json` 开始，不再采集数据。

请提供三项信息：
1. 详情 JSON 路径
2. 分析模式：A（拆解对标博主）或 B（诊断我的账号）
3. 产出物命名要使用的博主名 / 用户名
4. 博主生平背景信息（可选，例如学历、职业经历、生日等）
```

记录四个变量供后续流程使用：

- `details_path`
- `user_mode`
- `nickname`
- `bio`

### Phase 1：数据分析 + 认知层提取

运行：

```bash
python scripts/analyze.py "<details_path>" -o ./data
```

自动完成：

1. **数据清洗** — 解析 JSON，提取标题 / 正文 / 互动数据 / 评论 / 标签
2. **内容分类** — 基于笔记标签和高频关键词动态聚类，不预设任何领域
3. **标签统计** — 提取所有 `#` 话题标签，按频次排序 TOP20
4. **TOP10 + 评论洞察** — 高赞前 10 条的详情 + 热评精选
5. **认知层粗提取** — 观点句候选 / 高频价值词 / 写作结构统计
6. **[可选] 对比分析** — 当用户额外提供自己的账号详情 JSON 时，可使用 `--self`

输出文件：

- `{博主名}_analysis.json` — 结构化分析数据（含完整笔记列表、分类、观点句候选、高频价值词等）

### Phase 2：蒸馏底稿生成

运行：

```bash
python scripts/deep_analyze.py "./data/<博主名>_analysis.json" "<nickname>" \
  -o ./output --details "<details_path>" --mode <user_mode> [--bio "<生平背景>"]
```

脚本自动完成：

1. **基础统计面板** — 均赞 / 均藏 / 均评 / 爆款率 / 视频 vs 图文 / 藏赞比
2. **标题模式识别** — 11 种标题策略的使用比例和示例
3. **内容结构分析** — 正文长度分布、列表率、小标题率
4. **CTA 提取**
5. **Emoji 视觉分析**
6. **发布频率**
7. **发展趋势数据**
8. **观点句候选 / 高频价值词 / 写作结构**
9. **TOP10 数据包**
10. **AI 蒸馏任务（包含 Observe-Deduce-Verify 方法与 SOUL.md 指令）**

脚本产出：

- `{博主名}_数据底稿.md`
- `{博主名}_AI蒸馏任务.md`

### Phase 3：AI 生成最终产物

AI 必须读取 `AI蒸馏任务.md`，执行 **Observe-Deduce-Verify (观察-推导-验证)** 模型，生成以下最终交付物：

1. **HTML 报告**
   - 文件名：`{博主名}_蒸馏报告.html`
   - 技术要求：单文件 HTML，手写 CSS（禁止 Tailwind CDN），Google Fonts 引入 Space Mono + Noto Serif SC
   - 设计风格：Archive Terminal（工业档案感）；底色 `#CEC9C0`，主强调色 `#8A3926`，正文 `#1A1211`
   - 无圆角、无阴影、无白色卡片；模块 1 / 8 / 10 为砖红色反转背景
   - 三个动效：滚动 `fadeInUp` / 数字 `counter` / 分割线 `draw-in`（原生 JS）
   - 折叠面板用 `<details><summary>` 原生 HTML；响应式，移动端断点 768px
   - 字号系统：标签/元数据层 11-13px，正文内容层 14-16px，统计大数字 20px
   - 详细视觉规格见 `AI蒸馏任务.md` 的“技术要求”章节

2. **Skill 文件夹之 SKILL.md**
   - 模式 A：`{博主名}_创作指南.skill/SKILL.md`
   - 模式 B：`{用户名}_创作基因.skill/SKILL.md`

3. **Skill 文件夹之 SOUL.md（新增）**
   - 模式 A：`{博主名}_创作指南.skill/SOUL.md`
   - 模式 B：`{用户名}_创作基因.skill/SOUL.md`
   - 格式：符合 I-Lang v4.0 标准，包含 7 维表达指纹 (opening, vocabulary, rhythm, question, ending, tone, audience)

**⚠️ 关键契约：**
- 最终 Skill 不是单个 `.skill.md` 文件
- 最终 Skill 是一个可安装的文件夹
- 文件夹中至少必须有 `SKILL.md` 和 `SOUL.md`

### Phase 4：质量检查

运行校验时，最终产物应按以下口径验收：

- `{博主名}_蒸馏报告.html`
- `{博主名}_创作指南.skill/SKILL.md`
- `{博主名}_创作指南.skill/SOUL.md` (新增)

模式 B 时，后两项替换为：

- `{用户名}_创作基因.skill/SKILL.md`
- `{用户名}_创作基因.skill/SOUL.md` (新增)

如果最终产物缺失、为空、或 AI 仍输出成单个 `.skill.md` 文件，都视为不合格。

---

## 重要约束

- 不要自行补造 `notes_details.json`，必须使用用户提供的文件
- 不要退回去执行采集流程，不要要求用户配置 TikHub Token
- 不要调用 `crawl_blogger.py` 作为默认步骤
- 如果用户提供的不是详情 JSON，而只是列表 JSON，要先明确指出数据不够，再请用户补充 `*_notes_details.json`
- 如果文件名与实际博主名不一致，以用户明确指定的 `nickname` 为准

---

## 文件结构

```text
blogger-distiller/
├── SKILL.md                  # 你现在看的这个文件
├── run.py                    # 旧版一键入口（默认包含采集，不作为当前 Skill 主流程）
├── install.py                # 自动安装脚本
├── scripts/
│   ├── check_env.py          # 旧版环境检查脚本（当前 Skill 默认不调用）
│   ├── crawl_blogger.py      # 旧版采集脚本（当前 Skill 默认不调用）
│   ├── analyze.py            # Phase 1: 数据分析 + 认知层粗提取
│   ├── deep_analyze.py       # Phase 2: 数据底稿 + AI 蒸馏任务
│   ├── verify.py             # Phase 4: 数据校验模块
│   └── utils/
│       ├── tikhub_client.py
│       ├── endpoint_router.py
│       ├── endpoints.json
│       ├── adapters.py
│       ├── common.py
│       └── quality.py
└── references/
    └── 产出物质量标杆.md
```

---

## 使用方式

### 自然语言触发（推荐）

直接对 AI 说：

```text
拆解博主，并使用这个文件开始分析：<博主名>_notes_details.json
```

或：

```text
诊断我的小红书账号，这是我的 notes_details.json：<文件路径>
```

AI 必须先确认 `details_path`、`user_mode`、`nickname`，再继续后面的流程。

### 手动分步执行

```bash
cd blogger-distiller/

# Phase 1: 数据分析
python scripts/analyze.py "./data/<博主名>_notes_details.json" -o ./data

# Phase 2: 生成数据底稿和 AI 蒸馏任务
python scripts/deep_analyze.py "./data/<博主名>_analysis.json" "<博主名>" \
  -o ./output --details "./data/<博主名>_notes_details.json" --mode A
```

**注意：**
- 当前 Skill 默认从 `*_notes_details.json` 开始，不包含采集步骤
- `deep_analyze.py` 只负责生成数据底稿和 AI 蒸馏任务；最终 HTML 和 Skill 文件夹由宿主 AI 继续完成

---

## 多平台兼容性

| 平台 | 本机运行 | Python | 文件读写 | 测试状态 |
|------|---------|--------|---------|---------|
| CodeBuddy (WorkBuddy) | ✅ | ✅ | ✅ | ✅ 已验证 |
| Claude Code | ✅ | ✅ | ✅ | ✅ 已验证 |
| OpenClaw (本地) | ✅ | ✅ | ✅ | 待测试 |
| OpenClaw (云端) | ✅ | ✅ | ✅ | 待测试 |
| Codex | ✅ | ✅ | ✅ | ✅ 已验证 |

### 核心原则

1. 一份 `SKILL.md` 兼容 WorkBuddy / Claude Code / OpenClaw / Codex
2. 当前 Skill 以用户提供的 `*_notes_details.json` 作为唯一数据入口
3. 默认只调用分析与蒸馏脚本，不调用采集脚本
4. 如果用户数据不完整，先补数据，再进入分析流程

---

## 参考文档

- `references/产出物质量标杆.md` — 可作为产出结构和质量上限参考；若与当前 HTML / Skill 文件夹契约冲突，以本文件和操作手册为准
