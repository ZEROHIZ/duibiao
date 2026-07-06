# 通用内容采集输出规范

> 用途：定义爬取完成后的标准化输出格式
> 范围：仅包含采集侧输出，不包含分析结果、不包含对比结果
> 版本：3.0.0

---

## 1. 输出目录

每个主体只输出 3 个文件：

```text
data/
  {subject_id}/
    profile.json
    contents_list.json
    contents_details.json
```

不得输出：

- `analysis.json`
- `compare_manifest.json`
- `compare_summary.json`

---

## 2. 通用规则

### 2.1 命名规则

- 所有 key 必须使用 `snake_case`
- 所有 key 必须使用英文
- 不得混用同义字段

### 2.2 类型规则

- 字符串：`string`
- 数字：`number`
- 布尔：`boolean`
- 时间：`iso8601 string`
- 列表：`array`
- 对象：`object`
- 缺失值：`null`

### 2.3 缺失值规则

- 缺失字符串：`null`
- 缺失数字：`null`
- 空列表：`[]`
- 空对象：`{}`

禁止：

- 用 `""` 表示缺失
- 用 `0` 表示未知
- 同一字段输出不同类型

### 2.4 时间规则

所有时间字段必须使用 ISO 8601：

```text
2026-05-05T12:00:00+08:00
```

### 2.5 原始数据保留

每个主体、内容、评论对象都必须保留：

- `platform_fields`
- `raw`

---

## 3. 输出文件职责

### 3.1 `profile.json`

输出账号/主体层信息。

### 3.2 `contents_list.json`

输出内容列表索引。

规则：

- 只放列表级信息
- 不放正文全文
- 不放完整评论

### 3.3 `contents_details.json`

输出内容详情。

规则：

- 放正文主文本
- 放标签、关键词、CTA
- 放评论
- 放内容级指标

---

## 4. 标准字段名

### 4.1 主体层标准字段

- `schema_version`
- `platform`
- `subject_id`
- `subject_role`
- `account_id`
- `nickname`
- `display_name`
- `profile_url`
- `avatar_url`
- `bio`
- `location`
- `language`
- `verification`
- `account_type`
- `content_count`
- `followers_count`
- `following_count`
- `likes_count`
- `total_view_count`
- `created_at`
- `collected_at`
- `tags`
- `platform_fields`
- `source`
- `raw`

### 4.2 内容层标准字段

- `content_id`
- `subject_id`
- `platform`
- `content_type`
- `content_format`
- `title`
- `subtitle`
- `summary`
- `content`
- `content_text_segments`
- `cover_url`
- `source_url`
- `publish_time`
- `update_time`
- `duration_sec`
- `language`
- `series_name`
- `episode_index`
- `tags`
- `mentions`
- `topic_terms`
- `keywords`
- `cta`
- `emoji_list`
- `links`
- `assets`
- `metrics`
- `comments_summary`
- `comments`
- `is_pinned`
- `is_top`
- `detail_collected`
- `platform_fields`
- `raw`

### 4.3 评论层标准字段

- `comment_id`
- `content_id`
- `parent_comment_id`
- `author_role`
- `author_name`
- `content`
- `like_count`
- `reply_count`
- `is_hot`
- `language`
- `created_at`
- `platform_fields`
- `raw`

---

## 5. 枚举值

### 5.1 `subject_role`

允许值：

- `target`
- `self`
- `peer`
- `sample`

### 5.2 `account_type`

允许值：

- `creator`
- `brand`
- `media`
- `personal`
- `organization`
- `unknown`

### 5.3 `content_type`

允许值：

- `article`
- `video`
- `post`
- `podcast`
- `newsletter`
- `thread`
- `course`
- `other`

### 5.4 `content_format`

允许值：

- `short_video`
- `long_video`
- `image_post`
- `video_post`
- `text_post`
- `carousel`
- `long_article`
- `short_article`
- `audio_episode`
- `livestream_clip`
- `thread_post`
- `other`

### 5.5 `author_role`

允许值：

- `reader`
- `author`
- `brand`
- `other`

---

## 6. 通用指标字段

所有内容对象的 `metrics` 必须使用以下字段名：

- `view_count`
- `impression_count`
- `reach_count`
- `like_count`
- `comment_count`
- `share_count`
- `save_count`
- `reply_count`
- `quote_count`
- `click_count`
- `subscribe_count`
- `follow_count`
- `lead_count`
- `conversion_count`
- `engagement_count`
- `engagement_rate`

规则：

- 平台有值则填数字
- 平台无值则填 `null`
- 不可靠推断值不得写入

### 6.1 `engagement_count`

如需计算，使用：

```text
engagement_count = like_count + comment_count + share_count + save_count + quote_count
```

### 6.2 `engagement_rate`

如需计算，使用：

```text
engagement_rate = engagement_count / view_count
```

规则：

- `view_count` 缺失时输出 `null`
- 除数为 `0` 时输出 `null`

---

## 7. `profile.json`

### 7.1 结构

```json
{
  "schema_version": "3.0.0",
  "platform": "youtube",
  "subject_id": "target_youtube_UC123",
  "subject_role": "target",
  "account_id": "UC123",
  "nickname": "创作者名称",
  "display_name": "创作者名称",
  "profile_url": "https://...",
  "avatar_url": "https://...",
  "bio": "简介文本",
  "location": null,
  "language": "zh-CN",
  "verification": null,
  "account_type": "creator",
  "content_count": 0,
  "followers_count": 0,
  "following_count": null,
  "likes_count": null,
  "total_view_count": null,
  "created_at": null,
  "collected_at": "2026-05-05T12:00:00+08:00",
  "tags": [],
  "platform_fields": {},
  "source": {
    "source_type": "api|manual|crawl|hybrid",
    "source_url": null
  },
  "raw": {}
}
```

### 7.2 必填字段

- `schema_version`
- `platform`
- `subject_id`
- `subject_role`
- `account_id`
- `nickname`
- `profile_url`
- `bio`
- `content_count`
- `collected_at`
- `platform_fields`
- `raw`

---

## 8. `contents_list.json`

### 8.1 结构

```json
{
  "schema_version": "3.0.0",
  "subject_id": "target_youtube_UC123",
  "platform": "youtube",
  "total_count": 0,
  "sorted_by": "publish_time_desc",
  "items": [
    {
      "content_id": "video_001",
      "subject_id": "target_youtube_UC123",
      "platform": "youtube",
      "content_type": "video",
      "content_format": "long_video",
      "title": "标题",
      "subtitle": null,
      "summary": null,
      "cover_url": "https://...",
      "source_url": "https://...",
      "publish_time": "2026-05-05T12:00:00+08:00",
      "update_time": null,
      "duration_sec": 600,
      "language": "zh-CN",
      "series_name": null,
      "episode_index": null,
      "is_pinned": false,
      "is_top": false,
      "detail_collected": false,
      "metrics": {
        "view_count": 0,
        "impression_count": null,
        "reach_count": null,
        "like_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "save_count": null,
        "reply_count": null,
        "quote_count": null,
        "click_count": null,
        "subscribe_count": null,
        "follow_count": null,
        "lead_count": null,
        "conversion_count": null,
        "engagement_count": null,
        "engagement_rate": null
      },
      "platform_fields": {},
      "raw": {}
    }
  ]
}
```

### 8.2 必填字段

文件级必填：

- `schema_version`
- `subject_id`
- `platform`
- `total_count`
- `items`

每个 item 必填：

- `content_id`
- `subject_id`
- `platform`
- `content_type`
- `content_format`
- `title`
- `source_url`
- `publish_time`
- `metrics`
- `platform_fields`
- `raw`

---

## 9. `contents_details.json`

### 9.1 结构

```json
{
  "schema_version": "3.0.0",
  "subject_id": "target_youtube_UC123",
  "platform": "youtube",
  "items": [
    {
      "content_id": "video_001",
      "subject_id": "target_youtube_UC123",
      "platform": "youtube",
      "content_type": "video",
      "content_format": "long_video",
      "title": "标题",
      "subtitle": null,
      "summary": null,
      "content": "正文、简介、字幕转录或正文拼接文本",
      "content_text_segments": [],
      "cover_url": "https://...",
      "source_url": "https://...",
      "publish_time": "2026-05-05T12:00:00+08:00",
      "update_time": null,
      "duration_sec": 600,
      "language": "zh-CN",
      "series_name": null,
      "episode_index": null,
      "tags": [],
      "mentions": [],
      "topic_terms": [],
      "keywords": [],
      "cta": [],
      "emoji_list": [],
      "links": [],
      "assets": {
        "images": [],
        "video": {
          "video_url": null,
          "cover_url": null
        },
        "audio": {
          "audio_url": null,
          "duration_sec": null
        }
      },
      "metrics": {
        "view_count": 0,
        "impression_count": null,
        "reach_count": null,
        "like_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "save_count": null,
        "reply_count": null,
        "quote_count": null,
        "click_count": null,
        "subscribe_count": null,
        "follow_count": null,
        "lead_count": null,
        "conversion_count": null,
        "engagement_count": null,
        "engagement_rate": null
      },
      "comments_summary": {
        "total_count": null,
        "top_comment_ids": []
      },
      "comments": [],
      "platform_fields": {},
      "raw": {}
    }
  ]
}
```

### 9.2 必填字段

文件级必填：

- `schema_version`
- `subject_id`
- `platform`
- `items`

每个 item 必填：

- `content_id`
- `subject_id`
- `platform`
- `content_type`
- `content_format`
- `title`
- `content`
- `source_url`
- `publish_time`
- `metrics`
- `comments`
- `platform_fields`
- `raw`

---

## 10. 评论对象

评论对象必须符合以下结构：

```json
{
  "comment_id": "comment_001",
  "content_id": "video_001",
  "parent_comment_id": null,
  "author_role": "reader",
  "author_name": null,
  "content": "评论正文",
  "like_count": 0,
  "reply_count": 0,
  "is_hot": false,
  "language": "zh-CN",
  "created_at": "2026-05-05T12:00:00+08:00",
  "platform_fields": {},
  "raw": {}
}
```

必填字段：

- `comment_id`
- `content_id`
- `author_role`
- `content`
- `created_at`
- `platform_fields`
- `raw`

---

## 11. 最小可用字段集

### 11.1 主体层

- `schema_version`
- `platform`
- `subject_id`
- `subject_role`
- `account_id`
- `nickname`
- `profile_url`
- `bio`
- `content_count`
- `collected_at`
- `platform_fields`
- `raw`

### 11.2 内容列表层

- `content_id`
- `subject_id`
- `platform`
- `content_type`
- `content_format`
- `title`
- `source_url`
- `publish_time`
- `metrics`
- `platform_fields`
- `raw`

### 11.3 内容详情层

- `content_id`
- `subject_id`
- `platform`
- `content_type`
- `content_format`
- `title`
- `content`
- `source_url`
- `publish_time`
- `metrics`
- `comments`
- `platform_fields`
- `raw`

### 11.4 评论层

- `comment_id`
- `content_id`
- `author_role`
- `content`
- `created_at`
- `platform_fields`
- `raw`

---

## 12. 验收规则

数据输出必须满足：

1. 仅输出 `profile.json`、`contents_list.json`、`contents_details.json`
2. 文件名正确
3. 顶层结构正确
4. 必填字段齐全
5. 字段类型正确
6. 时间格式正确
7. `metrics` 字段齐全
8. `platform_fields` 和 `raw` 保留

任一项不满足，视为不合格。

