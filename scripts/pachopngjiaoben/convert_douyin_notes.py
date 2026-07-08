import sys as _sys, io as _io  # Windows GBK 终端 emoji 兼容
if _sys.stdout and hasattr(_sys.stdout, 'buffer') and getattr(_sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    try:
        _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (ValueError, AttributeError):
        pass
if _sys.stderr and hasattr(_sys.stderr, 'buffer') and getattr(_sys.stderr, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    try:
        _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    except (ValueError, AttributeError):
        pass

"""
convert_douyin_notes.py - 抖音爬取数据格式转换与转录工具

该脚本用于将抖音爬取下来的原始作品与评论数据（JSON 字典格式）转换为
blogger-distiller 推荐的标准 notes_details.json 结构（数组格式）。
如果作品的正文 (desc) 字段是一个视频链接，脚本将自动下载该视频并调用
Whisper API 服务 进行语音转文字，最后将转录出的文本回填到 desc 字段中。

功能特性：
1. 识别并下载视频 URL，调用 Whisper API 进行转录（支持超时、错误重试与临时文件清理）。
2. 将字典结构规整为标准数组格式。
3. 扁平/嵌套评论层级映射，智能识别博主作者评论。
4. 运行 verify.py 校验转换后的数据质量。
"""

import os
import sys
import json
import time
import re
import tempfile
import argparse
import requests
import struct

# 确保能正确引入同目录下的 verify 模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from verify import check_content_completeness, check_note_count, check_time_field, check_duplicates
except ImportError:
    # 兜底：如果直接运行且未在 PythonPath 下
    check_content_completeness = None
    check_note_count = None
    check_time_field = None
    check_duplicates = None


def get_mp4_duration(file_path):
    """
    通过解析 MP4 的 mvhd 盒子，提取视频时长（秒）。不需要任何第三方依赖。
    如果解析失败，返回 None。
    """
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'rb') as f:
            data = f.read(1024 * 1024) # 读取前 1MB
            idx = data.find(b'mvhd')
            if idx == -1:
                return None
            version = data[idx + 4]
            if version == 0:
                timescale = struct.unpack('>I', data[idx + 16 : idx + 20])[0]
                duration = struct.unpack('>I', data[idx + 20 : idx + 24])[0]
            elif version == 1:
                timescale = struct.unpack('>I', data[idx + 24 : idx + 28])[0]
                duration = struct.unpack('>Q', data[idx + 28 : idx + 36])[0]
            else:
                return None
            if timescale > 0:
                return duration / timescale
    except Exception as e:
        print(f"⚠️ 解析 MP4 时长出错: {e}")
    return None


def download_video(url, dest_path):
    """
    流式下载视频文件，带有超时和代理规避
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def transcribe_video(video_path, whisper_url, model="medium", timeout=600):
    """
    将下载的视频发送至 Whisper API 接口进行转录
    """
    with open(video_path, 'rb') as f:
        files = {
            'file': f
        }
        data = {
            'model': model,
            'max_duration': 7.0,  # 7秒左右合并断句
        }
        # 动态指定超时时间
        response = requests.post(whisper_url, files=files, data=data, timeout=timeout)
        response.raise_for_status()
        res_json = response.json()
        segments = res_json.get("segments", [])
        text = "".join([seg.get("text", "") for seg in segments]).strip()
        return text


def transcribe_with_retry(video_url, whisper_url, model="medium", retries=3):
    """
    下载并转录视频的主函数，支持失败重试与临时视频文件清理。
    返回: (success_bool, result_str)
    """
    temp_dir = tempfile.gettempdir()
    temp_video = os.path.join(temp_dir, f"temp_transcribe_{int(time.time())}_{os.getpid()}.mp4")
    
    print(f"-> 开始下载视频链接: {video_url}")
    try:
        download_video(video_url, temp_video)
        print(f"-> 视频下载成功，暂存至: {temp_video}")
    except Exception as e:
        print(f"❌ 视频下载失败: {e}")
        if os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except:
                pass
        return False, f"视频下载失败: {e}"

    # 动态分析视频的时长，决定转录的超时时间
    duration = get_mp4_duration(temp_video)
    if duration:
        # 转录计算需要一定耗时：
        # - 在 CPU 模式下，一般转录时间大致为视频时长的 0.5 到 1 倍左右（如 1 分钟视频需要 30 到 60 秒）
        # - 在 GPU 模式下，一般在 0.1 到 0.2 倍左右。
        # 为确保绝对安全且能适应 CPU/GPU 等不同环境，我们将超时时间动态设定为：视频时长（秒）的 1.5 倍 + 120 秒兜底（最少 120 秒）。
        dynamic_timeout = max(120, int(duration * 1.5) + 120)
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        print(f"-> 视频时长: {minutes}分{seconds}秒 (共 {int(duration)} 秒)，动态设置转录超时为 {dynamic_timeout} 秒")
    else:
        # 解析失败时，回退到 10 分钟默认超时
        dynamic_timeout = 600
        print(f"-> 无法读取视频时长，回退到默认转录超时 600 秒")

    transcription_text = ""
    success = False
    for attempt in range(1, retries + 1):
        print(f"-> 正在调用 Whisper API 进行语音转文字 (尝试 {attempt}/{retries}), API: {whisper_url} ...")
        try:
            transcription_text = transcribe_video(temp_video, whisper_url, model=model, timeout=dynamic_timeout)
            print("🎉 语音识别转录成功！")
            success = True
            break
        except Exception as e:
            print(f"⚠️ 第 {attempt} 次转录尝试失败: {e}")
            if attempt < retries:
                time.sleep(2)
            else:
                print("❌ 语音识别转录最终失败。")
                transcription_text = f"语音转录失败: {e}"

    # 清理临时文件
    if os.path.exists(temp_video):
        try:
            os.remove(temp_video)
            print("-> 临时视频文件已成功清理。")
        except Exception as cleanup_err:
            print(f"⚠️ 清理临时文件失败: {cleanup_err}")

    return success, transcription_text


def map_comments(raw_comments, blogger_name=None):
    """
    将原始评论列表映射为标准格式，处理多级嵌套和作者标识
    """
    if not raw_comments:
        return []

    # 1. 扁平结构映射所有评论，便于寻找父级
    mapped_by_cid = {}
    for idx, c in enumerate(raw_comments):
        # 兼容不同字段名
        cid = c.get("cid") or c.get("id") or f"gen_cid_{idx}"
        nickname = c.get("user", {}).get("nickname", "未知用户")
        
        is_author = False
        if blogger_name and nickname == blogger_name:
            is_author = True
        if c.get("label_text") == "作者":
            is_author = True
            
        mapped_comment = {
            "content": c.get("text") or c.get("content") or "",
            "likeCount": int(c.get("digg_count") or c.get("likeCount") or c.get("like_count") or 0),
            "speaker": nickname,
            "is_author": is_author,
            "subComments": []
        }
        
        mapped_by_cid[str(cid)] = {
            "raw": c,
            "mapped": mapped_comment
        }

    # 2. 组装父子树状结构
    top_level_comments = []
    for cid, item in mapped_by_cid.items():
        raw = item["raw"]
        mapped = item["mapped"]
        reply_id = raw.get("reply_id")
        
        # 2a. 处理原始数据中已内嵌的子评论（如果有）
        sub_raw_list = raw.get("reply_comment") or raw.get("reply_comment_list") or []
        if isinstance(sub_raw_list, list):
            for sub_raw in sub_raw_list:
                sub_mapped = map_comments([sub_raw], blogger_name)
                if sub_mapped:
                    sub_mapped[0]["reply_to"] = mapped["speaker"]
                    mapped["subComments"].append(sub_mapped[0])
                    
        # 2b. 处理扁平列表中通过 reply_id 指向的子评论
        if reply_id and str(reply_id) != "0":
            parent_cid = str(reply_id)
            if parent_cid in mapped_by_cid:
                parent_mapped = mapped_by_cid[parent_cid]["mapped"]
                reply_to_reply_id = raw.get("reply_to_reply_id")
                reply_to_speaker = parent_mapped["speaker"]
                if reply_to_reply_id and str(reply_to_reply_id) in mapped_by_cid:
                    reply_to_speaker = mapped_by_cid[str(reply_to_reply_id)]["mapped"]["speaker"]
                
                mapped["reply_to"] = reply_to_speaker
                parent_mapped["subComments"].append(mapped)
            else:
                # 找不到父评论，则作为顶级评论兜底
                top_level_comments.append(mapped)
        else:
            # 顶级评论
            top_level_comments.append(mapped)

    return top_level_comments


def convert_notes(input_data, whisper_url, model="medium", blogger_name=None, existing_notes_map=None, skip_transcribe=False):
    """
    主转换逻辑：把原始字典结构转换为标准列表结构，回填视频转录文本
    """
    output_list = []
    
    # 支持输入为 dict 或者直接就是已转换的 list 容错
    if isinstance(input_data, list):
        items_to_process = {str(idx): item for idx, item in enumerate(input_data)}
    elif isinstance(input_data, dict):
        items_to_process = input_data
    else:
        print("❌ 输入格式错误：必须为 JSON 对象或数组")
        sys.exit(1)

    total = len(items_to_process)
    for idx, (feed_id, note_data) in enumerate(items_to_process.items(), 1):
        print(f"\n==================== 处理进度 ({idx}/{total}) ====================")
        raw_note = note_data.get("note", {})
        desc = raw_note.get("desc", "")
        
        # 判断 desc 是否是链接
        is_link = False
        if isinstance(desc, str):
            if desc.startswith("http://") or desc.startswith("https://"):
                is_link = True
                
        # 优先读取已存在的转录内容进行复用
        existing_desc = None
        if existing_notes_map and str(feed_id) in existing_notes_map:
            existing_desc = existing_notes_map[str(feed_id)]

        if existing_desc:
            print(f"检测到视频 [{feed_id}] 已经有转录文本，复用该文本以避免重复调用 Whisper API。")
            desc_val = existing_desc
        elif is_link:
            if skip_transcribe:
                print(f"检测到 desc 为链接，已设置 skip-transcribe，保留原始 URL 快速入库。")
                desc_val = desc
            else:
                print(f"检测到 desc 为链接，执行第一轮视频转录...")
                success, desc_val = transcribe_with_retry(desc, whisper_url, model=model)
                if not success:
                    print(f"⚠️ 第一轮转录失败，将作为待重试项保留 URL。")
                    desc_val = desc  # 第一轮失败，保留原始 URL 以便后续重试
        else:
            print(f"desc 为文本，直接跳过转录。")
            desc_val = desc

        # 规整互动数据
        raw_interact = raw_note.get("interactInfo", {})
        interact_info = {
            "likedCount": str(raw_interact.get("likedCount") or raw_interact.get("liked_count") or 0),
            "collectedCount": str(raw_interact.get("collectedCount") or raw_interact.get("collected_count") or 0),
            "commentCount": str(raw_interact.get("commentCount") or raw_interact.get("comment_count") or 0),
            "sharedCount": str(raw_interact.get("sharedCount") or raw_interact.get("shared_count") or raw_interact.get("shareCount") or raw_interact.get("share_count") or 0),
        }

        # 映射单个笔记
        mapped_item = {
            "_feed_id": str(feed_id),
            "_meta": {
                "privacy_version": "v2",
                "source": "douyin_converted",
                "converted_at": int(time.time())
            },
            "note": {
                "noteId": str(raw_note.get("noteId") or raw_note.get("note_id") or feed_id),
                "title": raw_note.get("title") or raw_note.get("display_title") or raw_note.get("displayTitle") or "",
                "desc": desc_val,
                "type": raw_note.get("type") or "normal",
                "time": int(raw_note.get("time") or raw_note.get("create_time") or raw_note.get("publish_time") or 0),
                "interactInfo": interact_info
            },
            "comments": {
                "list": map_comments(note_data.get("comments", {}).get("list", []), blogger_name)
            }
        }
        
        output_list.append(mapped_item)
        
    # ==================== 转录失败项循环重试逻辑 ====================
    if not skip_transcribe:
        max_retry_loops = 5
        for loop in range(1, max_retry_loops + 1):
            # 统计当前仍然是 http 链接的未完成项
            failed_items = []
            for item in output_list:
                d = item["note"]["desc"]
                if isinstance(d, str) and (d.startswith("http://") or d.startswith("https://")):
                    failed_items.append(item)

            if not failed_items:
                print("\n🎉 所有视频已成功转录完成！")
                break

            print(f"\n==================== 开启第 {loop}/{max_retry_loops} 轮全部失败项重试 (共 {len(failed_items)} 个) ====================")
            
            for idx, item in enumerate(failed_items, 1):
                url = item["note"]["desc"]
                title = item["note"]["title"]
                print(f"\n-> 重试项 ({idx}/{len(failed_items)}): {title}")
                success, desc_val = transcribe_with_retry(url, whisper_url, model=model)
                if success:
                    item["note"]["desc"] = desc_val
                else:
                    print(f"⚠️ 第 {loop} 轮重试该项依然失败。")

        # 最终报告失败项目
        final_failed = [item for item in output_list if isinstance(item["note"]["desc"], str) and (item["note"]["desc"].startswith("http://") or item["note"]["desc"].startswith("https://"))]
        if final_failed:
            print(f"\n⚠️ 警告：经过 {max_retry_loops} 轮重试后，仍有 {len(final_failed)} 个视频转录失败，已保留原始视频链接。")
            for item in final_failed:
                print(f"   - 标题: {item['note']['title']} | 链接: {item['note']['desc']}")
    else:
        print("\n已设置 skip-transcribe，跳过第一阶段格式转换中的实时转录与重试逻辑。")
        
    return output_list


def main():
    parser = argparse.ArgumentParser(description="抖音爬取数据格式转换与转录工具")
    parser.add_argument("-i", "--input", required=True, help="输入的原始抖音 JSON 文件路径")
    parser.add_argument("-o", "--output", default=None, help="输出的标准格式 JSON 文件路径 (如果不指定，默认在输入目录下以 <博主昵称>_notes_details.json 命名)")
    parser.add_argument("-b", "--blogger", required=True, help="博主昵称（用于识别作者评论，并作为默认的输出文件名）")
    parser.add_argument("--whisper-url", default="http://192.168.110.30:7211/transcribe", help="Whisper API transcribe 接口地址")
    parser.add_argument("--model", default="medium", help="Whisper 模型名称")
    parser.add_argument("--skip-transcribe", action="store_true", help="是否跳过 Whisper 语音转文字（快速入库模式）")
    args = parser.parse_args()

    # 1. 检查输入文件
    if not os.path.exists(args.input):
        print(f"❌ 输入文件不存在: {args.input}")
        sys.exit(1)

    # 自动计算默认的输出路径
    if not args.output:
        input_dir = os.path.dirname(os.path.abspath(args.input))
        args.output = os.path.join(input_dir, f"{args.blogger}_notes_details.json")

    print(f"正在加载输入数据: {args.input}")
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            input_data = json.load(f)
    except Exception as e:
        print(f"❌ 读取/解析输入 JSON 文件失败: {e}")
        sys.exit(1)

    # 2. 读取已存在的目标输出以复用已有的转录结果
    existing_notes_map = {}
    if os.path.exists(args.output):
        print(f"检测到已存在输出目标文件: {args.output}，正在读取已转录文本...")
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                existing_list = json.load(f)
                if isinstance(existing_list, list):
                    for item in existing_list:
                        feed_id = item.get("_feed_id")
                        desc = item.get("note", {}).get("desc")
                        if feed_id and desc and not (isinstance(desc, str) and (desc.startswith("http://") or desc.startswith("https://"))):
                            existing_notes_map[str(feed_id)] = desc
            print(f"  成功载入 {len(existing_notes_map)} 条已完成转录的文本记录。")
        except Exception as e:
            print(f"  ⚠️ 读取已有输出文件失败: {e}")

    # 3. 执行转换
    converted_list = convert_notes(
        input_data=input_data,
        whisper_url=args.whisper_url,
        model=args.model,
        blogger_name=args.blogger,
        existing_notes_map=existing_notes_map,
        skip_transcribe=args.skip_transcribe
    )

    # 3. 保存输出
    print(f"\n正在保存至目标路径: {args.output}")
    try:
        output_dir = os.path.dirname(os.path.abspath(args.output))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(converted_list, f, ensure_ascii=False, indent=2)
        print("🎉 转换并回填完成！")
    except Exception as e:
        print(f"❌ 写入输出 JSON 文件失败: {e}")
        sys.exit(1)

    # 4. 执行 verify.py 数据质量校验
    if check_content_completeness:
        print("\n==================== 开始进行数据质量校验 ====================")
        try:
            # V1: 完整性校验
            ok, msg = check_content_completeness(converted_list)
            print(msg)
            
            # V2: 数量校验（此处将以实际转换的数量为基准进行展示）
            print(check_note_count(converted_list, len(converted_list)))
            
            # V3: 时间字段校验
            print(check_time_field(converted_list))
            
            # V4: 重复校验
            print(check_duplicates(converted_list))
            print("==============================================================")
        except Exception as ve:
            print(f"⚠️ 执行数据校验模块时出错: {ve}")


if __name__ == "__main__":
    main()
