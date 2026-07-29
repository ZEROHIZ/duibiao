"""
Get 笔记 (biji.com) 数据库 Migration 扩充脚本
核心职责：
1. 建立 biji_browser_accounts 表，用于存储多账号 Playwright 浏览器 Session/Cookie 凭据与登录状态。
2. 建立 biji_transcripts 表，做为得到云端转录文案的独立解耦存储层。
3. 扩展 bloggers 表，增加得到账号绑定 (biji_browser_id)、知识库 (biji_topic_alias) 与 Follow ID 字段。
4. 扩展 blogger_notes 表，增加得到转录文案、摘要与原视频链接关联字段。
"""

import sqlite3
import os
import sys

def migrate_database(db_path="data/distiller.db"):
    """
    对 SQLite 数据库进行增量 Migration 升级，保持向下兼容性。
    """
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    abs_db_path = os.path.abspath(db_path)
    print(f"[Migration] 正在执行数据库升级... (目标库: {abs_db_path})")
    
    if not os.path.exists(abs_db_path):
        os.makedirs(os.path.dirname(abs_db_path), exist_ok=True)

    conn = sqlite3.connect(abs_db_path)
    cursor = conn.cursor()

    # 1. 创建得到多账号管理表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS biji_browser_accounts (
        account_id TEXT PRIMARY KEY,
        nickname TEXT,
        alias_name TEXT,
        user_id TEXT,
        status TEXT DEFAULT 'LOGGED_IN',
        last_login_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    try:
        cursor.execute("ALTER TABLE biji_browser_accounts ADD COLUMN alias_name TEXT;")
    except sqlite3.OperationalError:
        pass

    # 2. 创建得到的独立文案暂存表 (解耦存储层)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS biji_transcripts (
        post_id_str TEXT PRIMARY KEY,
        account_id TEXT,
        blogger_name TEXT,
        post_name TEXT,
        post_media_text TEXT,
        post_summary TEXT,
        post_cleaned_summary TEXT,
        original_video_url TEXT,
        original_video_id TEXT,
        post_update_time INTEGER,
        synced_to_main INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. 为 bloggers 表增加得到相关的属性列
    blogger_cols = [
        ("data_source", "TEXT DEFAULT 'tikhub'"),
        ("biji_browser_id", "TEXT DEFAULT 'account_01'"),
        ("biji_topic_id", "INTEGER"),
        ("biji_topic_alias", "TEXT"),
        ("biji_topic_name", "TEXT"),
        ("biji_follow_id", "INTEGER"),
        ("biji_url", "TEXT")
    ]
    for col_name, col_type in blogger_cols:
        try:
            cursor.execute(f"ALTER TABLE bloggers ADD COLUMN {col_name} {col_type};")
            print(f"  [bloggers] 成功添加列: {col_name}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    # 4. 为 blogger_notes 表增加得到转录文案相关列
    note_cols = [
        ("summary", "TEXT"),
        ("cleaned_summary", "TEXT"),
        ("cover_url", "TEXT"),
        ("original_video_url", "TEXT"),
        ("biji_post_alias", "TEXT"),
        ("biji_post_url", "TEXT")
    ]
    for col_name, col_type in note_cols:
        try:
            cursor.execute(f"ALTER TABLE blogger_notes ADD COLUMN {col_name} {col_type};")
            print(f"  [blogger_notes] 成功添加列: {col_name}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()
    print("[Migration] 数据库升级完成！✅")

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    
    migrate_database()
