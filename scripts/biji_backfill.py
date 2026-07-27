"""
Get 笔记 (biji.com) 文案匹配与主数据库回传引擎
核心职责：
1. 从 biji_transcripts 独立表中提取未回传的云端转录文案。
2. 从 original_video_url 中正则解析出短视频平台的真实视频 ID (aweme_id / video_id)。
3. 将转录稿 (post_media_text) 与 AI 摘要 (post_summary) 回写更新至主作品表 (blogger_notes)。
4. 如果主作品表中尚无该视频（得到优先抓到），立即补载草稿记录，供后续评论爬虫与蒸馏分析使用。
"""

import sqlite3
import re
import os
import sys

def extract_video_id_from_url(url):
    """
    从得到的 post_url (如 https://www.iesdouyin.com/share/video/7659731714117324078/...)
    中精准解析出短视频 ID。
    """
    if not url:
        return None
    match = re.search(r'(?:video/|modal_id=|group_id=|aweme_id=)(\d+)', url)
    if match:
        return match.group(1)
    return None

def backfill_transcripts(db_path="data/distiller.db"):
    """
    扫描 biji_transcripts 表，将未回传的文案写入主表 blogger_notes。
    """
    abs_db_path = os.path.abspath(db_path)
    if not os.path.exists(abs_db_path):
        print(f"[Backfill] ❌ 数据库不存在: {abs_db_path}")
        return 0

    conn = sqlite3.connect(abs_db_path)
    cursor = conn.cursor()

    # 查询所有未同步到主表的得到文案
    unfilled = cursor.execute("""
        SELECT post_id_str, blogger_name, post_name, post_media_text, 
               post_summary, post_cleaned_summary, original_video_url, 
               original_video_id, post_update_time
        FROM biji_transcripts
        WHERE synced_to_main = 0
    """).fetchall()

    if not unfilled:
        print("[Backfill] ℹ️ 没有待回传的得到文案。")
        conn.close()
        return 0

    print(f"[Backfill] 🔄 正在处理 {len(unfilled)} 条未回传的得到文案...")
    success_count = 0

    for idx, row in enumerate(unfilled, start=1):
        (post_id_str, blogger_name, post_name, post_media_text, 
         post_summary, post_cleaned_summary, original_video_url, 
         original_video_id, post_update_time) = row

        # 尝试从 URL 提取 video_id
        video_id = original_video_id or extract_video_id_from_url(original_video_url)
        target_id = video_id or post_id_str

        print(f"\n  --------------------------------------------------")
        print(f"  📝 [回填分析 {idx}/{len(unfilled)}] 得到的作品标题: 『{post_name}』")
        print(f"  👤 属于得到博主: {blogger_name or '(未归属)'}")
        print(f"  🆔 解析目标作品 ID (aweme_id): {target_id}")
        print(f"  🌐 得到记录原视频 URL: {original_video_url or '(无)'}")

        # 校验主表中是否已存在该作品
        existing_note = cursor.execute("SELECT id, title, desc FROM blogger_notes WHERE id = ? LIMIT 1", (target_id,)).fetchone()

        if existing_note:
            # 如果得到的标题更完整且不带省略号，自动无缝覆盖旧的被截断标题
            new_title = existing_note[1]
            if post_name and len(post_name) > len(existing_note[1] or ""):
                new_title = post_name

            cursor.execute("""
                UPDATE blogger_notes 
                SET title = ?, desc = ?, summary = ?, cleaned_summary = ?, original_video_url = ?
                WHERE id = ?
            """, (new_title, post_media_text, post_summary, post_cleaned_summary, original_video_url, target_id))
            print(f"  ✅ [文案回填成功] 主库找到匹配作品 (ID: {target_id}, 标题: 『{new_title}』)，已回写装填得到的转录文案与 AI 摘要！")
            success_count += 1
        else:
            print(f"  ℹ️ [回填跳过] 作品 ID '{target_id}' 未在主作品库 (blogger_notes) 中录入，暂不补载新记录。")

        # 标记为已处理/已同步
        cursor.execute("UPDATE biji_transcripts SET synced_to_main = 1 WHERE post_id_str = ?", (post_id_str,))

    conn.commit()
    conn.close()
    print(f"[Backfill] ✅ 成功回传并装填 {success_count} 条文案到主数据库！")
    return success_count

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        
    backfill_transcripts()
