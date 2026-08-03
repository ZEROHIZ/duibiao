# 博主蒸馏器 API 部署与调用指南 (API_DOCKER_GUIDE.md)

本文档提供博主蒸馏器系统核心 API 的调用指南，支持 **对标账号录入 API** 以及 **Faster Whisper 语音转录服务 API**。

---

## 一、 对标账号录入 API (`POST /api/bloggers`)

该接口用于向系统添加需要监控与蒸馏的对标博主。针对**手机端（iOS 快捷指令、Android Tasker、快捷剪贴板）**操作场景，系统已原生支持**极简快捷别名映射**。

### 1. 接口地址与请求头
* **URL**: `POST http://<服务器IP>:8000/api/bloggers`
* **Content-Type**: `application/json`

---

### 2. 请求参数说明 (支持极简别名)

| 参数名 | 类型 | 必选 | 默认值 | 快捷别名 / 兼容写法说明 |
| :--- | :--- | :--- | :--- | :--- |
| `home_url` | String | **是** | - | 博主个人主页链接 |
| `name` | String | 否 | 自动提取 | 博主昵称（若留空，后台自动爬取提取） |
| `platform` | String | 否 | `douyin` | **平台快捷映射**：<br>• 抖音：`"抖音"`, `"dy"`, `"douyin"`<br>• 小红书：`"小红书"`, `"xhs"`, `"xiaohongshu"`<br>• B站：`"B站"`, `"bili"`, `"bilibili"` |
| `account` | String | 否 | `01` | **得到账号快捷映射**：<br>• 阿拉伯数字：`"1"`, `"01"` $\rightarrow$ 自动映射为 `account_01`<br>• `"2"`, `"02"` $\rightarrow$ 自动映射为 `account_02`<br>• 也支持账号别名，或写全 `biji_account_id` |
| `topic` | String | 否 | - | **得到知识库快捷映射**：<br>• 直接填**知识库名称**（如 `"默认知识库"`, `"AI卡片库"`）<br>• 或填 8 位 Alias（如 `"40Dk9QrY"`）<br>• 填新名字自动在得到创建该知识库！ |
| `is_transcribe`| Integer| 否 | `1` | 是否开启自动语音转录：`1` 开启，`0` 关闭 |

---

### 3. 手机端 / 快捷指令 JSON 调用示例

#### 示例 A：手机端极简调用 (推荐)
仅需 4 个简单字段，支持中文和阿拉伯数字：
```json
{
  "home_url": "https://v.douyin.com/xxxxxx/",
  "platform": "抖音",
  "account": "1",
  "topic": "默认知识库"
}
```

#### 示例 B：快捷短码调用 (更极简)
```json
{
  "home_url": "https://v.douyin.com/xxxxxx/",
  "platform": "dy",
  "account": "01",
  "topic": "AI卡片库"
}
```

#### 示例 C：cURL 命令行调用
```bash
curl -X POST "http://localhost:8000/api/bloggers" \
     -H "Content-Type: application/json" \
     -d '{
           "home_url": "https://v.douyin.com/xxxxxx/",
           "platform": "抖音",
           "account": "1",
           "topic": "默认知识库"
         }'
```

#### 示例 D：Python (requests) 脚本示例
```python
import requests

url = "http://localhost:8000/api/bloggers"
payload = {
    "home_url": "https://v.douyin.com/xxxxxx/",
    "platform": "抖音",
    "account": "1",
    "topic": "默认知识库"
}

res = requests.post(url, json=payload)
print(res.json())
```

---

### 4. 返回结果示例
```json
{
  "status": "success",
  "task_id": "biji_add_a1b2c3d4",
  "data": {
    "id": 12,
    "name": "待爬取博主_a1b2c3",
    "home_url": "https://v.douyin.com/xxxxxx/",
    "is_transcribe": 1,
    "platform": "douyin"
  }
}
```

---

## 二、 Faster Whisper 语音转录 API (`POST /transcribe`)

如需单独使用 Docker 部署的 Faster Whisper 语音转写服务，请参考本章节。

### 1. Docker 部署方式

#### A. 启动 CPU 模式 (轻量级)
```bash
docker run -d \
  --name whisper-api \
  -p 8000:8000 \
  -e MODE=cpu \
  -v /root/whisper_models:/app/models \
  ghcr.io/<USERNAME>/faster-whisper:master
```

#### B. 启动 GPU 模式 (高性能)
```bash
docker run -d \
  --name whisper-api \
  --gpus all \
  -p 8000:8000 \
  -e MODE=gpu \
  -v /root/whisper_models:/app/models \
  ghcr.io/<USERNAME>/faster-whisper:master
```

---

### 2. 转录接口说明 (`POST /transcribe`)

#### 请求参数 (Form-Data)
| 参数名 | 类型 | 必选 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `file` | File | 是 | - | 支持 MP3, WAV, MP4 等格式 (MP4 会自动转码) |
| `model` | String | 否 | `medium` | Whisper 模型 ID (推荐 `deepdml/faster-whisper-large-v3-turbo-ct2`) |
| `initial_prompt` | String | 否 | - | 初始提示，用于引导模型识别专业词汇、控制标点。 |
| `max_duration` | Float | 否 | `0` | 合并断句的时长（秒）。`0` 表示不合并。 |
