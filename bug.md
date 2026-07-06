# Bug 档案记录

## 1. Windows 临时视频文件删除失败 (WinError 32)
- **发现时间**：2026-06-22
- **问题描述**：在 Windows 系统上运行转换脚本时，转录完成后删除临时视频文件报错：`[WinError 32] 另一个程序正在使用此文件，进程无法访问。`
- **问题根源**：`transcribe_video` 函数在使用 `requests.post` 上传文件时，直接执行了 `open(video_path, 'rb')`，但没有在使用后显式关闭文件句柄，导致句柄被 Python 进程持有，Windows 系统拒绝删除该文件。
- **解决方案**：使用 `with open(video_path, 'rb') as f:` 上传视频，确保请求发送完成后自动释放文件句柄。

## 2. Whisper 转录请求读取超时 (Read timeout) 与动态超时需求
- **发现时间**：2026-06-22
- **问题描述**：转录长视频时，客户端极易报错：`HTTPConnectionPool: Read timed out.`。硬编码固定超时值无法兼顾短视频效率与超长视频的执行安全。
- **问题根源**：转录计算所需时长和视频长度成正比。短视频转录极快，而长视频（如几十分钟）需要很长时间，在 CPU 模式下处理耗时更长，原有的 300 秒超时会频繁中断。
- **解决方案**：实现免第三方依赖的纯 Python MP4 视频时长快速解析器，读取 MP4 mvhd 盒子得到精确时长（秒）。然后，根据视频时长动态计算超时时间：`超时时间 = 视频秒数 * 1.5 + 120 秒` (最少 120 秒)。这实现了短视频迅速失败、长视频安全放行的动态超时机制。

## 3. deep_analyze.py 在详情数据直接嵌套 note 时提取正文失败
- **发现时间**：2026-06-23
- **问题描述**：传入 details JSON 运行 deep_analyze.py 时，无法正确提取正文内容，导致正文长度、CTA 等数据分析结果均显示为 0。
- **问题根源**：`note = item.get("data", {}).get("note", item)` 逻辑在 details 中包含 `note` 键而无 `data` 键时，错误地回退到了 `item`，未能解包 `item["note"]`，导致之后访问 `desc` 等属性失败。
- **解决方案**：判断如果 `note` 直接在 `item` 中且为字典类型，优先使用 `item["note"]`。

## 4. “作品总览时间线”子页签切换时博主列表依然可见
- **发现时间**：2026-07-07
- **问题描述**：在“对标灵感”页面点击“最新作品流总览”时，虽然作品时间线显示了出来，但是博主监控管理列表仍然展示在下方，两者同时可见，导致排版混乱。
- **问题根源**：`blogger-list-view` 容器只添加了 `active-subview` 类，未添加 `subview` 类。在切换页签的 JS 逻辑中，切换子视图是用 `document.querySelectorAll(".subview").forEach(...)` 来移除/添加 `active-subview` 类的。由于 `blogger-list-view` 缺少 `subview` 类，未能被 `querySelectorAll` 选中，导致其高亮状态无法被移除。
- **解决方案**：在 `index.html` 的 `blogger-list-view` div 容器上增加 `subview` 类名，即 `class="subview active-subview"`。
