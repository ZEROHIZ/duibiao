"""
.venv/Scripts/python.exe web/backend/app.py
FastAPI 后端应用入口 (app.py)
核心职责：启动 Web 服务，连接 SQLite，提供各项监控 API 接口，并挂载静态前端网页与物理蒸馏输出目录（/output）。
"""

import os
import sqlite3
import sys
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 引入本级数据库及导入模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_db_connection, init_db
from seed import seed_all
from importer import run_full_import

app = FastAPI(title="博主蒸馏器 Web 看板", description="信息源监控仪表盘 API")

# 支持跨域访问 (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------
# 启动时挂载逻辑
# ----------------------------------------------------------
@app.on_event("startup")
def startup_event():
    # 1. 初始化数据库表结构
    init_db()
    # 2. 播种冷启动种子数据 (理论模型与行业资讯)
    seed_all()
    # 3. 将本地已有的 data/ 下的博主 JSON 数据导进数据库
    run_full_import()


class KnowledgeCreate(BaseModel):
    topic: str
    niche: str
    insight: str
    pitfall: str
    analogy: str


class BloggerUrlUpdate(BaseModel):
    home_url: str


class BloggerCreate(BaseModel):
    name: str
    home_url: str = ""


# ----------------------------------------------------------
# API 端点实现
# ----------------------------------------------------------

@app.get("/api/dashboard")
def get_dashboard_summary():
    """获取仪表盘概览统计数据"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM knowledge_base;")
        knowledge_count = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) as cnt FROM industry_news_cache;")
        news_count = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) as cnt FROM bloggers;")
        bloggers_count = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) as cnt FROM trending_topics;")
        trending_count = cursor.fetchone()["cnt"]

        return {
            "status": "success",
            "data": {
                "knowledge_count": knowledge_count,
                "news_count": news_count,
                "bloggers_count": bloggers_count,
                "trending_count": trending_count
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/knowledge")
def get_knowledge_list(niche: str = Query(None), q: str = Query(None)):
    """获取思维模型列表，支持 niche 赛道和关键词 q 搜索"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM knowledge_base WHERE 1=1"
    params = []

    if niche:
        query += " AND niche LIKE ?"
        params.append(f"%{niche}%")
    if q:
        query += " AND (topic LIKE ? OR insight LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    
    query += " ORDER BY created_at DESC"

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/knowledge")
def create_knowledge_item(item: KnowledgeCreate):
    """手动添加一条思维模型卡片"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
        INSERT INTO knowledge_base (topic, niche, insight, pitfall, analogy)
        VALUES (?, ?, ?, ?, ?);
        """, (item.topic, item.niche, item.insight, item.pitfall, item.analogy))
        conn.commit()
        return {"status": "success", "message": f"Successfully created model: {item.topic}"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Model with topic '{item.topic}' already exists.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/news")
def get_industry_news():
    """获取行业快讯列表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM industry_news_cache ORDER BY published_at DESC;")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/trending")
def get_trending_topics():
    """获取全网热度资讯"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM trending_topics ORDER BY id ASC;")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/bloggers")
def get_bloggers_list():
    """获取已有的对标博主列表，联查其最新更新的笔记标题与时间"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 联合子查询抓取该博主时间戳最大的笔记作为最新动态
        cursor.execute("""
        SELECT b.id, b.name, b.home_url, b.total_notes, b.video_count, b.normal_count, 
               b.avg_likes, b.avg_collects, b.avg_comments, 
               b.total_likes, b.total_collects, b.total_comments,
               n.title as latest_note_title, n.published_at as latest_note_time
        FROM bloggers b
        LEFT JOIN blogger_notes n ON n.blogger_id = b.id AND n.id = (
            SELECT id FROM blogger_notes 
            WHERE blogger_id = b.id 
            ORDER BY published_at DESC, likes DESC 
            LIMIT 1
        )
        ORDER BY b.total_likes DESC;
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.put("/api/bloggers/{blogger_id}/home_url")
def update_blogger_home_url(blogger_id: int, body: BloggerUrlUpdate):
    """更新指定博主的个人主页链接"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Blogger not found.")

        cursor.execute("""
        UPDATE bloggers
        SET home_url = ?
        WHERE id = ?;
        """, (body.home_url, blogger_id))
        conn.commit()
        return {"status": "success", "message": "Home URL updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/bloggers")
def create_blogger(body: BloggerCreate):
    """录入新对标博主"""
    import sqlite3
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO bloggers (
            name, home_url, total_notes, video_count, normal_count, 
            avg_likes, avg_collects, avg_comments, 
            total_likes, total_collects, total_comments
        ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0);
        """, (body.name, body.home_url))
        conn.commit()
        
        cursor.execute("SELECT id FROM bloggers WHERE name = ?;", (body.name,))
        new_id = cursor.fetchone()["id"]
        return {
            "status": "success",
            "data": {
                "id": new_id,
                "name": body.name,
                "home_url": body.home_url
            }
        }
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Blogger with name '{body.name}' already exists.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/notes/all")
def get_all_notes_timeline(limit: int = Query(50)):
    """获取所有博主的作品总览时间轴，按发布时间倒序排列"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT n.id, n.title, n.desc, n.type, n.likes, n.collects, n.comments, n.shares, n.category, n.comments_json, n.published_at, b.name as blogger_name
        FROM blogger_notes n
        JOIN bloggers b ON n.blogger_id = b.id
        ORDER BY n.published_at DESC, n.likes DESC
        LIMIT ?;
        """, (limit,))
        rows = cursor.fetchall()
        notes = []
        for row in rows:
            r_dict = dict(row)
            import json
            try:
                r_dict["comments_list"] = json.loads(r_dict["comments_json"]) if r_dict["comments_json"] else []
            except Exception:
                r_dict["comments_list"] = []
            r_dict.pop("comments_json", None)
            notes.append(r_dict)
        return notes
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/bloggers/{name}/distill")
def get_blogger_distillation(name: str):
    """获取单个博主的深度蒸馏认知层结构化数据"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 获取博主主表数据
        cursor.execute("SELECT * FROM bloggers WHERE name = ?;", (name,))
        blogger_row = cursor.fetchone()
        if not blogger_row:
            raise HTTPException(status_code=404, detail=f"Blogger '{name}' not found in database.")
        
        blogger = dict(blogger_row)
        blogger_id = blogger["id"]

        # 获取蒸馏分析字段
        cursor.execute("SELECT * FROM blogger_distilled WHERE blogger_id = ?;", (blogger_id,))
        distill_row = cursor.fetchone()
        
        distilled = {}
        if distill_row:
            d_dict = dict(distill_row)
            # 解析其中的所有 JSON 字符串为前端可直接访问的字典或数组
            json_fields = [
                "category_stats_json", "tag_freq_json", "title_patterns_json",
                "emoji_info_json", "cta_info_json", "structure_info_json",
                "frequency_info_json", "growth_info_json", "opinion_candidates_json",
                "writing_structure_json", "value_words_json"
            ]
            for field in json_fields:
                clean_name = field.replace("_json", "")
                try:
                    import json
                    distilled[clean_name] = json.loads(d_dict[field]) if d_dict[field] else {}
                except Exception:
                    distilled[clean_name] = {}
        
        return {
            "blogger": blogger,
            "distilled": distilled
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/bloggers/{name}/notes")
def get_blogger_notes(name: str, limit: int = Query(50)):
    """获取单个博主的爆款笔记列表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 获取外键 id
        cursor.execute("SELECT id FROM bloggers WHERE name = ?;", (name,))
        blogger_row = cursor.fetchone()
        if not blogger_row:
            raise HTTPException(status_code=404, detail=f"Blogger '{name}' not found.")
        
        blogger_id = blogger_row["id"]

        cursor.execute("""
        SELECT id, title, desc, type, likes, collects, comments, shares, category, tags_json, comments_json, published_at
        FROM blogger_notes
        WHERE blogger_id = ?
        ORDER BY likes DESC
        LIMIT ?;
        """, (blogger_id, limit))
        
        rows = cursor.fetchall()
        notes = []
        for row in rows:
            r_dict = dict(row)
            # 解析 JSON 数组
            import json
            try:
                r_dict["tags"] = json.loads(r_dict["tags_json"]) if r_dict["tags_json"] else []
                r_dict["comments_list"] = json.loads(r_dict["comments_json"]) if r_dict["comments_json"] else []
            except Exception:
                r_dict["tags"] = []
                r_dict["comments_list"] = []
            
            # 删除多余的 json 字符串字段减小传输体积
            r_dict.pop("tags_json", None)
            r_dict.pop("comments_json", None)
            notes.append(r_dict)
            
        return notes
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/bloggers/{name}/files_status")
def get_blogger_files_status(name: str):
    """检测博主的物理蒸馏文件存在性，并返回静态访问路径"""
    import urllib.parse
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
    
    report_filename = f"{name}_蒸馏报告.html"
    skill_filename = f"{name}_创作指南.skill/SKILL.md"
    soul_filename = f"{name}_创作指南.skill/SOUL.md"
    
    report_exists = os.path.exists(os.path.join(output_dir, report_filename))
    skill_exists = os.path.exists(os.path.join(output_dir, skill_filename))
    soul_exists = os.path.exists(os.path.join(output_dir, soul_filename))
    
    def get_url(path_str):
        path_str = path_str.replace("\\", "/")
        parts = path_str.split("/")
        encoded_parts = [urllib.parse.quote(part) for part in parts]
        return "/output/" + "/".join(encoded_parts)

    return {
        "status": "success",
        "data": {
            "report": {
                "exists": report_exists,
                "url": get_url(report_filename) if report_exists else None
            },
            "skill": {
                "exists": skill_exists,
                "url": get_url(skill_filename) if skill_exists else None
            },
            "soul": {
                "exists": soul_exists,
                "url": get_url(soul_filename) if soul_exists else None
            }
        }
    }


# ----------------------------------------------------------
# 前端静态文件托管
# ----------------------------------------------------------
# 挂载 output 目录用于访问物理蒸馏文件
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
if os.path.exists(OUTPUT_DIR):
    app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
    print(f"[FastAPI] Mounted output directory: {OUTPUT_DIR}")

# 获取前端资源的路径
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
else:
    print(f"[FastAPI] Warning: Frontend directory '{FRONTEND_DIR}' does not exist yet. Please create it.")


if __name__ == "__main__":
    import uvicorn
    # 端口绑定为 8000
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
