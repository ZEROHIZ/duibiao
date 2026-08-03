# 博主蒸馏器 API 部署与调用指南 (API_DOCKER_GUIDE.md)

本文档提供博主蒸馏器系统核心 API 的调用指南，支持 **对标账号录入 API** 以及 **Faster Whisper 语音转录服务 API**。

---

## 一、 对标账号录入 API (`POST /api/bloggers`)

该接口用于向系统添加需要监控与蒸馏的对标博主。针对**手机端（iOS 快捷指令、Android Tasker、微信/剪贴板一键分享）**操作场景，系统支持**完整剪贴板文本自动提取 URL** 以及**快捷别名映射**。

### 1. 接口地址与请求头
* **URL**: `POST http://<服务器IP>:8000/api/bloggers`
* **Content-Type**: `application/json`

---

### 2. 请求参数说明 (标注必选与可选)

| 参数名 | 类型 | 必填状态 | 默认值 | 说明与智能提取规则 |
| :--- | :--- | :--- | :--- | :--- |
| `home_url` | String | **【必填】** | - | **对标主页链接 / 复制的完整分享文本**：<br>可以直接粘贴 App 复制的整段复杂分享文本（包含标题、带有 `@` 的昵称与杂乱字符），后端会自动通过正则从中截取标准的 `https://...` 纯链接！ |
| `name` | String | **【可选】** | 自动提取 | 博主昵称：若留空，后台将自动从 `home_url` 的分享文本中提取（例如解析 `@你好我是大Jerry`），提取不到时自动分配临时标识。 |
| `platform` | String | **【可选】** | 自动推导 | **账号平台**（若留空，系统会自动根据 URL 域名自动智能识别！）：<br>• 抖音：`"抖音"`, `"dy"`, `"douyin"`<br>• 小红书：`"小红书"`, `"xhs"`, `"xiaohongshu"`<br>• B站：`"B站"`, `"bili"`, `"bilibili"` |
| `account` | String | **【可选】** | `01` | **得到账号**（支持数字与简写）：<br>• 阿拉伯数字：`"1"`, `"01"` $\rightarrow$ 映射为 `account_01`<br>• `"2"`, `"02"` $\rightarrow$ 映射为 `account_02`<br>• 亦可直接填账号别名，或使用全称 `biji_account_id` |
| `topic` | String | **【可选】** | - | **得到知识库**：<br>• 直接填**知识库名称**（如 `"默认知识库"`, `"AI卡片库"`）<br>• 或填 8 位 Alias（如 `"40Dk9QrY"`）<br>• 填新名字时后台自动在得到为您创建该知识库！ |
| `is_transcribe`| Integer| **【可选】** | `1` | 是否开启自动语音转录：`1` 开启（默认），`0` 关闭 |

---

### 3. 手机端分享文本直接粘贴调用示例

#### 场景 1：粘贴小红书完整分享文本（自动提取小红书链接、昵称与平台）
你可以直接将 App 里复制的**完整文本**赋值给 `home_url`，其它参数全留空，系统自动提取：

```json
{
  "home_url": "@你好我是大Jerry 在小红书收获了10.6K次赞与收藏，查看Ta的主页>> https://xhslink.cn/m/9anZdM0puje"
}
```
* **系统自动解析结果**：
  * 提取链接：`https://xhslink.cn/m/9anZdM0puje`
  * 识别平台：`xiaohongshu`
  * 提取昵称：`你好我是大Jerry`

#### 场景 2：粘贴抖音复杂口令/带尾巴的分享文本
```json
{
  "home_url": "5- 长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/TgDW28duOCY/ 9@8.com :5pm",
  "account": "1",
  "topic": "默认知识库"
}
```
* **系统自动解析结果**：
  * 提取链接：`https://v.douyin.com/TgDW28duOCY/`
  * 识别平台：`douyin`
  * 分配账号：`account_01`
  * 关联知识库：`默认知识库`

---

### 4. 代码调用示例

#### A. cURL 命令行示例
```bash
curl -X POST "http://localhost:8000/api/bloggers" \
     -H "Content-Type: application/json" \
     -d '{
           "home_url": "@你好我是大Jerry 在小红书收获了10.6K次赞与收藏，查看Ta的主页>> https://xhslink.cn/m/9anZdM0puje",
           "account": "1",
           "topic": "默认知识库"
         }'
```

#### B. Python (requests) 示例
```python
import requests

url = "http://localhost:8000/api/bloggers"
payload = {
    # 粘贴复制的任意复杂长文本，API 自动切出标准 URL
    "home_url": "5- 长按复制此条消息... https://v.douyin.com/TgDW28duOCY/ 9@8.com :5pm",
    "account": "01",
    "topic": "AI卡片库"
}

res = requests.post(url, json=payload)
print(res.json())
```

---

### 5. 返回结果示例
```json
{
  "status": "success",
  "task_id": "biji_add_a1b2c3d4",
  "data": {
    "id": 15,
    "name": "你好我是大Jerry",
    "home_url": "https://xhslink.cn/m/9anZdM0puje",
    "is_transcribe": 1,
    "platform": "xiaohongshu"
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
| 参数名 | 类型 | 必填状态 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `file` | File | **【必填】** | - | 支持 MP3, WAV, MP4 等格式 (MP4 会自动转码) |
| `model` | String | **【可选】** | `medium` | Whisper 模型 ID (推荐 `deepdml/faster-whisper-large-v3-turbo-ct2`) |
| `initial_prompt` | String | **【可选】** | - | 初始提示，用于引导模型识别专业词汇、控制标点。 |
| `max_duration` | Float | **【可选】** | `0` | 合并断句的时长（秒）。`0` 表示不合并。 |
