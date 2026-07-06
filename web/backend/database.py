"""
Web 看板数据库模块 (database.py)
核心职责：管理 SQLite 数据库连接、表结构初始化及常用 CRUD 操作。
本模块采用 Python 内置 sqlite3，避免复杂的 SQLAlchemy 依赖，保证轻量级与健壮性。
"""

import os
import sqlite3

# 确定数据库的绝对路径，确保不论在哪个目录下运行都能正确存取在项目根目录的 data 目录下
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "distiller.db")

def get_db_connection():
    """获取 SQLite 数据库连接，默认行以字典/对象格式返回"""
    # 确保 data 目录存在
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    """初始化数据库表结构"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 理论与思维模型库
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT UNIQUE,
        niche TEXT,
        insight TEXT,
        pitfall TEXT,
        analogy TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. 行业快讯缓存表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS industry_news_cache (
        id TEXT PRIMARY KEY,
        title TEXT,
        content TEXT,
        source TEXT,
        url TEXT,
        published_at TEXT,
        scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. 对标博主主表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bloggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        home_url TEXT,
        avatar TEXT,
        total_notes INTEGER,
        video_count INTEGER,
        normal_count INTEGER,
        avg_likes INTEGER,
        avg_collects INTEGER,
        avg_comments INTEGER,
        total_likes INTEGER,
        total_collects INTEGER,
        total_comments INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. 博主笔记详情表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blogger_notes (
        id TEXT PRIMARY KEY,
        blogger_id INTEGER,
        title TEXT,
        desc TEXT,
        type TEXT,
        likes INTEGER,
        collects INTEGER,
        comments INTEGER,
        shares INTEGER,
        category TEXT,
        tags_json TEXT,
        comments_json TEXT,
        published_at TEXT,
        FOREIGN KEY(blogger_id) REFERENCES bloggers(id)
    );
    """)

    # 5. 博主蒸馏分析数据表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS blogger_distilled (
        blogger_id INTEGER PRIMARY KEY,
        category_stats_json TEXT,
        tag_freq_json TEXT,
        title_patterns_json TEXT,
        emoji_info_json TEXT,
        cta_info_json TEXT,
        structure_info_json TEXT,
        frequency_info_json TEXT,
        growth_info_json TEXT,
        opinion_candidates_json TEXT,
        writing_structure_json TEXT,
        value_words_json TEXT,
        distilled_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(blogger_id) REFERENCES bloggers(id)
    );
    """)

    # 6. 全网热搜缓存表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trending_topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        heat TEXT,
        source TEXT,
        url TEXT,
        synced_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()
    print(f"[DB] SQLite Database initialized successfully: {DB_PATH}")

if __name__ == "__main__":
    init_db()
