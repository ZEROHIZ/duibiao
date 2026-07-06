# Faster Whisper API 部署与调用指南

本文档介绍如何使用 Docker 部署 Faster Whisper API 服务，并说明如何调用其接口。

---

## 1. Docker 部署方式

我们提供的 Dockerfile 支持 **CPU** 和 **GPU** 两种运行模式。

### A. 启动 CPU 模式 (轻量级)
适用于没有显卡或仅需简单测试的场景。
```bash
docker run -d \
  --name whisper-api \
  -p 8000:8000 \
  -e MODE=cpu \
  -v /root/whisper_models:/app/models \
  ghcr.io/<USERNAME>/faster-whisper:master
```

### B. 启动 GPU 模式 (高性能)
适用于生产环境，需宿主机已安装 NVIDIA Driver 和 NVIDIA Container Toolkit。
```bash
docker run -d \
  --name whisper-api \
  --gpus all \
  -p 8000:8000 \
  -e MODE=gpu \
  -v /root/whisper_models:/app/models \
  ghcr.io/<USERNAME>/faster-whisper:master
```

**环境变量说明：**
- `MODE`: `gpu` 或 `cpu`。设为 `gpu` 时，容器启动会自动安装必要的 NVIDIA 库。
- `WHISPER_MODEL_CACHE`: 容器内模型缓存路径（默认 `/app/models`）。建议通过 `-v` 挂载到宿主机以避免重复下载。

---

## 2. API 接口说明

### 接口地址
`POST /transcribe`

### 请求参数 (Form-Data)
| 参数名 | 类型 | 必选 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `file` | File | 是 | - | 支持 MP3, WAV, MP4 等格式 (MP4 会自动转码) |
| `model` | String | 否 | `medium` | Whisper 模型 ID (如: base, medium, 或 HF 仓库 ID) |
| `initial_prompt` | String | 否 | - | 初始提示，用于引导模型识别专业词汇、控制标点或提供上下文。 |
| `max_duration` | Float | 否 | `0` | 合并断句的时长（秒）。`0` 表示不合并，按原始断句输出。 |
| `device` | String | 否 | `auto` | 运行设备 (cpu, cuda, auto) |
| `cpu` | Boolean | 否 | `false` | 是否强制使用 CPU |

---

## 3. 可选模型 (model 参数)

你可以根据服务器配置选择不同的模型大小。模型越小速度越快，模型越大准确率越高。

| 模型名称 | 说明 | 推荐场景 |
| :--- | :--- | :--- |
| `tiny` | 极速，体积最小 | 实时转录测试 |
| `base` | 快速，体积小 | 简单对话识别 |
| `small` | 平衡型 | 普通视频字幕 |
| `medium` | **(默认)** 准确率较高 | 高质量字幕生成 |
| `large-v3` | 准确率最高 | 复杂环境、多语言、长视频 |
| `deepdml/faster-whisper-large-v3-turbo-ct2` | **(推荐)** 极致性能 | 追求高准确率且速度更快的场景 |

**注意**：如果是第一次使用某个模型，API 会自动从 Hugging Face 下载并缓存到挂载的目录中。

---

## 3. 调用示例

### 示例 A：使用 cURL (命令行)
**1. 按原始断句转录：**
```bash
curl -X POST "http://localhost:8000/transcribe" \
     -F "file=@/path/to/video.mp4"
```

**2. 指定模型并按 7 秒左右合并断句：**
```bash
curl -X POST "http://localhost:8000/transcribe" \
     -F "file=@/path/to/video.mp4" \
     -F "model=deepdml/faster-whisper-large-v3-turbo-ct2" \
     -F "max_duration=7" \
     -F "initial_prompt=这是一段关于健身和积酸的视频"
```

### 示例 B：使用 Python (requests)
```python
import requests

url = "http://localhost:8000/transcribe"
files = {'file': open('test.mp4', 'rb')}
data = {
    'model': 'medium',
    'max_duration': 7.0,
    'initial_prompt': '积酸, 增肌, 减脂'
}

response = requests.post(url, files=files, data=data)
print(response.json())
```

---

## 4. 返回结果示例
```json
{
  "language": "zh",
  "segments": [
    {
      "start": 0.0,
      "end": 7.5,
      "text": "这段文字是前7.5秒合并后的内容。"
    },
    {
      "start": 7.5,
      "end": 15.2,
      "text": "这是下一段合并后的内容。"
    }
  ],
  "srt": "1\n00:00:00,000 --> 00:00:07,500\n这段文字是前7.5秒合并后的内容。\n\n2\n00:00:07,500 --> 00:00:15,200\n这是下一段合并后的内容。\n"
}
```
