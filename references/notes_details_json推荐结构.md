# `notes_details.json` 推荐结构

这份文档用于说明当前 `blogger-distiller` 在“跳过采集、直接分析”模式下，推荐接收的 `*_notes_details.json` 结构。

结论先说：

- 当前分析主流程只依赖 `*_notes_details.json`
- 推荐格式是：**顶层为数组，每个元素代表 1 条笔记详情**
- 最稳妥的结构是：`{ _feed_id, _meta, note, comments }`

---

## 1. 推荐标准结构

这是推荐直接产出的 **标准 JSON 结构**：

```json
[
  {
    "_feed_id": "67f8c1d00000000012012345",
    "_meta": {
      "privacy_version": "v2"
    },
    "note": {
      "noteId": "67f8c1d00000000012012345",
      "title": "我的书桌改造，终于不乱了",
      "desc": "这次把书桌重新整理了一遍，核心思路是先分区，再决定每个区域只放一种功能的物品。桌面如果同时承担办公、护肤、收纳三个任务，就一定会乱。#书桌改造 #收纳",
      "type": "normal",
      "time": 1712345678,
      "interactInfo": {
        "likedCount": "1234",
        "collectedCount": "456",
        "commentCount": "78",
        "sharedCount": "12"
      }
    },
    "comments": {
      "list": [
        {
          "content": "这个分区思路很清楚，收藏了",
          "likeCount": 23,
          "speaker": "读者1",
          "is_author": false,
          "subComments": [
            {
              "content": "我也觉得特别适合小空间",
              "speaker": "作者",
              "is_author": true,
              "reply_to": "读者1"
            }
          ]
        }
      ]
    }
  }
]
```

---

## 2. 中文解释版示例

下面这份是 **给人看的伪 JSON**，方便做字段映射。  
它 **不是严格 JSON**，不要直接喂给程序。

```json
[
  {
    "_feed_id（笔记ID，必填，建议与 note.noteId 保持一致）": "67f8c1d00000000012012345",
    "_meta（内部元信息，可选）": {
      "privacy_version（脱敏版本，可选。没有可不传）": "v2"
    },
    "note（笔记主体，必填）": {
      "noteId（笔记ID，必填）": "67f8c1d00000000012012345",
      "title（笔记标题，建议填写）": "我的书桌改造，终于不乱了",
      "desc（笔记正文，强烈建议完整填写；这是最关键字段）": "这里放完整正文内容，支持带 #标签",
      "type（内容类型，必填；图文填 normal，视频填 video）": "normal",
      "time（发布时间戳，建议填 Unix 秒级时间戳）": 1712345678,
      "interactInfo（互动数据，建议完整填写）": {
        "likedCount（点赞数，建议字符串）": "1234",
        "collectedCount（收藏数，建议字符串）": "456",
        "commentCount（评论数，建议字符串）": "78",
        "sharedCount（分享数，建议字符串）": "12"
      }
    },
    "comments（评论容器，建议保留）": {
      "list（评论数组；没有评论也建议传 []）": [
        {
          "content（评论正文）": "这个分区思路很清楚，收藏了",
          "likeCount（评论点赞数）": 23,
          "speaker（评论者显示名；推荐直接用“读者1 / 读者2 / 作者”）": "读者1",
          "is_author（是否作者本人）": false,
          "subComments（子评论数组，可为空）": [
            {
              "content（子评论正文）": "我也觉得特别适合小空间",
              "speaker（子评论者显示名）": "作者",
              "is_author（是否作者本人）": true,
              "reply_to（回复给谁，可选）": "读者1"
            }
          ]
        }
      ]
    }
  }
]
```

---

## 3. 字段优先级

下面是实际分析脚本最关心的字段，按重要程度排序：

| 字段 | 是否必须 | 用途 |
|------|----------|------|
| `note.desc` | 强必须 | 正文分析、观点句提取、标签提取、内容结构分析 |
| `note.noteId` 或 `_feed_id` | 强必须 | 去重、定位笔记 |
| `note.title` | 强建议 | TOP10 展示、标题模式分析 |
| `note.interactInfo.likedCount` | 强建议 | 按赞排序、爆款判断 |
| `note.interactInfo.collectedCount` | 建议 | 收藏统计 |
| `note.interactInfo.commentCount` | 建议 | 评论统计 |
| `note.interactInfo.sharedCount` | 建议 | 分享统计 |
| `note.type` | 建议 | 区分图文 / 视频 |
| `note.time` | 建议 | 发布频率、趋势分析 |
| `comments.list` | 建议 | 评论洞察、TOP10 热评展示 |

---

## 4. 最低可兼容版本

如果你现在只想先跑通，不想一次性补齐全部字段，最低可以先做成这样：

```json
[
  {
    "_feed_id": "67f8c1d00000000012012345",
    "note": {
      "noteId": "67f8c1d00000000012012345",
      "title": "标题",
      "desc": "完整正文内容",
      "type": "normal",
      "time": 1712345678,
      "interactInfo": {
        "likedCount": "100",
        "collectedCount": "20",
        "commentCount": "5",
        "sharedCount": "1"
      }
    },
    "comments": {
      "list": []
    }
  }
]
```

这已经能满足当前 `analyze.py` 的主要读取逻辑。

---

## 5. 无评论时怎么传

推荐这样传：

```json
{
  "_feed_id": "67f8c1d00000000012012345",
  "note": {
    "noteId": "67f8c1d00000000012012345",
    "title": "标题",
    "desc": "完整正文",
    "type": "normal",
    "time": 1712345678,
    "interactInfo": {
      "likedCount": "100",
      "collectedCount": "20",
      "commentCount": "0",
      "sharedCount": "1"
    }
  },
  "comments": {
    "list": []
  }
}
```

不要省略成没有 `comments`，虽然脚本大多能兜底，但统一带上最稳。

---

## 6. 采集失败 / 内容受限时怎么传

如果某条笔记抓失败，但你又想保留占位信息，推荐这样传：

```json
[
  {
    "_error": "content restricted",
    "_content_restricted": true,
    "_feed_id": "67f8c1d00000000012012345",
    "_title": "这条笔记因权限限制未拿到正文"
  }
]
```

这类条目会被主分析流程跳过正文分析，但仍可作为“受限笔记”保留记录。

---

## 7. 关于博主背景生平（Bio）的传递

博主的背景生平信息（如学历、职业背景、星座生日等，用于 Observe-Deduce-Verify 推导模型）属于**博主级别的全局信息**，而非单条笔记的属性。

因此，生平信息**不存放在 `notes_details.json` 笔记列表结构中**，而是直接在运行蒸馏脚本时通过参数传递：
- 命令行参数：`--bio "生平描述文本"`
- 运行示例：`python run.py "博主昵称" --bio "清华计算机本，5年算法工程师，摩羯座"`

这样可以保持数据结构的简洁性，避免在每条笔记中产生冗余数据。

---

## 8. 兼容建议

如果你在做上游兼容层，建议按下面的映射统一到推荐结构：

| 你的字段 | 统一成 |
|----------|--------|
| `id` / `note_id` | `note.noteId` |
| `displayTitle` / `display_title` | `note.title` |
| `content` / `description` | `note.desc` |
| `create_time` / `publish_time` | `note.time` |
| `liked_count` / `likes` | `note.interactInfo.likedCount` |
| `collected_count` / `collects` | `note.interactInfo.collectedCount` |
| `comment_count` / `comments_count` | `note.interactInfo.commentCount` |
| `shared_count` / `shares` | `note.interactInfo.sharedCount` |
| `comment_list` | `comments.list` |
| `nickname` / `user_name` | 评论中的 `speaker` |

---

## 9. 推荐做法

最推荐的落地策略是：

1. 你的兼容层统一输出本文第 1 节的标准结构
2. 所有计数字段统一为字符串
3. `comments.list` 永远存在，没有评论就传空数组
4. `desc` 尽量传完整正文，不要只传摘要
5. `time` 尽量传秒级时间戳，便于频率分析

如果后面你要，我也可以继续帮你补一份：

- “最小 JSON Schema”
- “TypeScript / Python dataclass 定义”
- “旧字段到新字段的转换函数模板”
