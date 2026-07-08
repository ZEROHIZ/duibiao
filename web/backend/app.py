"""
.venv/Scripts/python.exe web/backend/app.py
FastAPI 后端应用入口 (app.py)
核心职责：启动 Web 服务，连接 SQLite，提供各项监控 API 接口，并挂载静态前端网页与物理蒸馏输出目录（/output）。
"""

import os
import sqlite3
import sys
import json
import threading
import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
# 异步后台语音转录 Worker (支持静默下载、Whisper 翻译与数据库增量回写)
# ----------------------------------------------------------
def transcription_worker_loop():
    print("[语音转录后台 Worker] 启动成功，开启待转录视频扫描...")
    
    # 动态把 scripts/pachopngjiaoben 加载到路径，便于复用转录代码
    pachopng_dir = os.path.join(ROOT_DIR, "scripts", "pachopngjiaoben")
    if pachopng_dir not in sys.path:
        sys.path.insert(0, pachopng_dir)
        
    try:
        from convert_douyin_notes import transcribe_with_retry
    except ImportError as ie:
        print(f"[语音转录后台 Worker] 导入转录模块失败: {ie}")
        return

    while True:
        try:
            # 1. 加载配置参数
            config_path = os.path.join(ROOT_DIR, "data", "config.json")
            whisper_url = "http://192.168.110.30:7211/transcribe"
            whisper_model = "medium"
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        whisper_url = cfg.get("whisper_url", whisper_url)
                        whisper_model = cfg.get("whisper_model", whisper_model)
                except:
                    pass
            
            # 2. 查询需要转录的视频（desc 是包含 http 的视频直链，或者标记了转录重试且未达上限的记录）
            db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            pending_notes = cursor.execute("""
                SELECT n.id, n.desc, n.blogger_id, b.name as blogger_name 
                FROM blogger_notes n
                JOIN bloggers b ON n.blogger_id = b.id
                WHERE n.type = 'video' AND (
                    n.desc LIKE 'http://%' OR 
                    n.desc LIKE 'https://%' OR 
                    n.desc LIKE '[转录失败_第%'
                )
            """).fetchall()
            conn.close()
            
            if pending_notes:
                import re
                print(f"[语音转录后台 Worker] 扫描到 {len(pending_notes)} 个待转录/重试视频。")
                for note in pending_notes:
                    note_id = note["id"]
                    raw_desc = note["desc"]
                    blogger_name = note["blogger_name"]
                    
                    # 确定真正的视频 URL 和当前的重试次数
                    video_url = raw_desc
                    retry_count = 0
                    if raw_desc.startswith("[转录失败_第"):
                        match = re.search(r'第(\d+)次重试', raw_desc)
                        if match:
                            retry_count = int(match.group(1))
                        # 从中解析出真正的 http 地址
                        idx = raw_desc.find("http")
                        if idx != -1:
                            video_url = raw_desc[idx:]
                            
                    print(f"[语音转录后台 Worker] 正在转录视频 [{note_id}] (第 {retry_count + 1} 次尝试), 链接: {video_url}")
                    success, text = transcribe_with_retry(video_url, whisper_url, model=whisper_model, retries=2)
                    
                    # 3. 回写数据库
                    conn = sqlite3.connect(db_path, timeout=30.0)
                    cursor = conn.cursor()
                    
                    final_text = ""
                    if success:
                        final_text = text
                        cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                        print(f"[语音转录后台 Worker] 视频 [{note_id}] 转录成功，内容已回填。")
                    else:
                        if retry_count < 3:
                            # 允许重试，更新状态标记为下一轮重试
                            final_text = f"[转录失败_第{retry_count + 1}次重试]: {video_url}"
                            cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                            print(f"[语音转录后台 Worker] 视频 [{note_id}] 本轮转录失败，标记以待下轮重试：{text}")
                        else:
                            # 达到上限，放弃重试
                            final_text = f"[转录失败_已达上限]: {video_url}"
                            cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                            print(f"[语音转录后台 Worker] 视频 [{note_id}] 转录失败已达上限，停止重试：{text}")
                            
                    conn.commit()
                    
                    # 4. 同步更新 processed 目录下的 JSON 归档文件
                    try:
                        processed_file = os.path.join(ROOT_DIR, "data", "processed", f"{blogger_name}_notes_details.json")
                        if os.path.exists(processed_file):
                            with open(processed_file, "r", encoding="utf-8") as f:
                                details_list = json.load(f)
                            for item in details_list:
                                if str(item.get("_feed_id")) == str(note_id):
                                    item["note"]["desc"] = text if success else f"[转录失败]: {text}"
                                    break
                            with open(processed_file, "w", encoding="utf-8") as f:
                                json.dump(details_list, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"[语音转录后台 Worker] 同步更新 JSON 归档文件失败: {e}")
                        
                    conn.close()
                    time.sleep(1) # 视频间隔安全休眠
            else:
                time.sleep(10) # 任务空闲休眠
        except Exception as e:
            print(f"[语音转录后台 Worker] 循环出错: {e}")
            time.sleep(10)

def start_transcription_worker():
    t = threading.Thread(target=transcription_worker_loop, daemon=True)
    t.start()


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
    # 4. 启动异步后台转录服务
    start_transcription_worker()


class KnowledgeCreate(BaseModel):
    topic: str
    niche: str
    insight: str
    pitfall: str
    analogy: str


class BloggerUrlUpdate(BaseModel):
    home_url: str


class BloggerNameUpdate(BaseModel):
    name: str


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


@app.put("/api/bloggers/{blogger_id}/name")
def update_blogger_name(blogger_id: int, body: BloggerNameUpdate):
    """更新指定博主的名称"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Blogger not found.")
        
        # 检查新名字是否与其他博主冲突
        cursor.execute("SELECT id FROM bloggers WHERE name = ? AND id != ?;", (body.name, blogger_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Blogger name already exists.")

        cursor.execute("""
        UPDATE bloggers
        SET name = ?
        WHERE id = ?;
        """, (body.name, blogger_id))
        conn.commit()
        return {"status": "success", "message": "Blogger name updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/bloggers/{blogger_id}")
def delete_blogger(blogger_id: int):
    """删除指定的对标博主及相关全部数据"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Blogger not found.")
        
        # 开启事务，级联删除该博主的其它所有关联数据
        cursor.execute("DELETE FROM blogger_distilled WHERE blogger_id = ?;", (blogger_id,))
        cursor.execute("DELETE FROM blogger_notes WHERE blogger_id = ?;", (blogger_id,))
        cursor.execute("DELETE FROM bloggers WHERE id = ?;", (blogger_id,))
        conn.commit()
        return {"status": "success", "message": "Blogger and all associated data deleted successfully."}
    except Exception as e:
        conn.rollback()
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
# 抖音数据爬取及流水线同步 API (队列管理版)
# ----------------------------------------------------------
import uuid
import queue
import threading
import subprocess
import json
from datetime import datetime

# FIFO 任务排队队列
task_queue = queue.Queue()

# 全局内存任务字典 (task_id -> task_info)
active_crawl_tasks = {}
tasks_lock = threading.Lock()

# 默认设置参数
DEFAULT_SETTINGS = {
    "whisper_url": "http://192.168.110.30:7211/transcribe",
    "whisper_model": "medium",
    "max_videos": 5
}

def get_settings_path():
    return os.path.join(ROOT_DIR, "data", "config.json")

def load_settings():
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_SETTINGS, **data}
        except Exception as e:
            print(f"[FastAPI] Failed to read config.json: {e}")
    return DEFAULT_SETTINGS.copy()

def save_settings(data):
    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"[FastAPI] Failed to save config.json: {e}")
        return False

# 后台单线程串行 Worker
def queue_worker():
    while True:
        try:
            task_id = task_queue.get()
            if task_id is None:
                break
                
            with tasks_lock:
                if task_id not in active_crawl_tasks:
                    task_queue.task_done()
                    continue
                task_info = active_crawl_tasks[task_id]
                task_info["status"] = "running"
                task_info["started_at"] = datetime.now().isoformat()
            
            blogger = task_info["blogger"]
            log_path = task_info["log_path"]
            
            # 读取配置参数并支持任务级别覆盖
            settings = load_settings()
            max_videos = task_info.get("max_videos") or settings.get("max_videos", 5)
            whisper_url = settings.get("whisper_url", "http://192.168.110.30:7211/transcribe")
            whisper_model = settings.get("whisper_model", "medium")
            
            python_exe = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
                
            cmd = [
                python_exe,
                os.path.join(ROOT_DIR, "scripts", "pachopngjiaoben", "pipeline.py"),
                "--max-videos", str(max_videos),
                "--whisper-url", whisper_url
            ]
            if blogger != "all":
                cmd.extend(["--blogger", blogger])
                
            # 强制子进程以 UTF-8 编码模式运行以防 Windows GBK 终端乱码
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
                
            try:
                with open(log_path, "w", encoding="utf-8") as log_file:
                    log_file.write(f"=== 流水线任务 {task_id} 启动 (博主: '{blogger}') ===\n")
                    log_file.write(f"配置参数: 抓取上限={max_videos}, Whisper模型={whisper_model}, Whisper接口={whisper_url}\n\n")
                    
                    process = subprocess.Popen(
                        cmd,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        cwd=ROOT_DIR
                    )
                    process.wait()
                    
                with tasks_lock:
                    if process.returncode == 0:
                        active_crawl_tasks[task_id]["status"] = "success"
                    else:
                        active_crawl_tasks[task_id]["status"] = "failed"
                    active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
            except Exception as err:
                with tasks_lock:
                    active_crawl_tasks[task_id]["status"] = "failed"
                    active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                try:
                    with open(log_path, "a", encoding="utf-8") as log_file:
                        log_file.write(f"\n[线程错误] 运行 pipeline 异常: {err}\n")
                except:
                    pass
            
            task_queue.task_done()
        except Exception as e:
            print(f"[Queue Worker] Exception: {e}")

# 开启后台队列消费线程
worker_thread = threading.Thread(target=queue_worker, daemon=True)
worker_thread.start()


@app.get("/api/settings")
def get_settings_endpoint():
    return load_settings()

class SettingsUpdate(BaseModel):
    whisper_url: str
    whisper_model: str
    max_videos: int

@app.post("/api/settings")
def update_settings_endpoint(settings: SettingsUpdate):
    data = {
        "whisper_url": settings.whisper_url,
        "whisper_model": settings.whisper_model,
        "max_videos": settings.max_videos
    }
    if save_settings(data):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save settings")

@app.post("/api/crawl/run")
def run_crawler_pipeline(blogger: str = "all", max_videos: int = None):
    task_id = str(uuid.uuid4())
    log_dir = os.path.join(ROOT_DIR, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{task_id}.log")
    
    with tasks_lock:
        active_crawl_tasks[task_id] = {
            "id": task_id,
            "blogger": blogger,
            "status": "queued",
            "log_path": log_path,
            "max_videos": max_videos,
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "finished_at": None
        }
        
    task_queue.put(task_id)
    return {"status": "success", "task_id": task_id}

@app.get("/api/crawl/tasks")
def get_all_crawl_tasks():
    with tasks_lock:
        tasks_list = list(active_crawl_tasks.values())
    tasks_list.sort(key=lambda x: x["created_at"], reverse=True)
    
    clean_list = []
    for t in tasks_list:
        clean_list.append({
            "id": t["id"],
            "blogger": t["blogger"],
            "status": t["status"],
            "created_at": t["created_at"],
            "started_at": t["started_at"],
            "finished_at": t["finished_at"]
        })
    return clean_list

@app.post("/api/crawl/clear")
def clear_finished_tasks():
    global active_crawl_tasks
    with tasks_lock:
        retained_tasks = {}
        for tid, t in active_crawl_tasks.items():
            if t["status"] in ["queued", "running"]:
                retained_tasks[tid] = t
        active_crawl_tasks = retained_tasks
    return {"status": "success"}

def analyze_task_step(logs):
    if not logs:
        return "排队中"
    
    # 检查是否有错误/失败
    if "错误：" in logs or "运行发生异常错误:" in logs or "❌ 阶段失败" in logs or "Failed to read logs" in logs:
        for line in reversed(logs.split("\n")):
            if "错误：" in line or "运行发生异常错误:" in line or "❌ 阶段失败" in line:
                return f"同步出错: {line.strip()}"
        return "同步异常终止"
        
    if "🎉 同步流水线全部成功！" in logs or "流水线同步运行汇总: 1/1 成功" in logs:
        return "同步完成"
        
    if "[验证码拦截]" in logs or "手动滑块解锁" in logs:
        return "遇到验证码拦截 (等待手动滑块解锁)"
        
    # 从流水线反向查找当前运行的阶段
    current_phase = ""
    for line in reversed(logs.split("\n")):
        if ">>> 开始执行阶段:" in line:
            current_phase = line.split(">>> 开始执行阶段:")[-1].strip()
            break
            
    # 从爬虫日志反向查找细分动作
    for line in reversed(logs.split("\n")):
        if "模拟键盘按下" in line or "ArrowDown" in line:
            return f"{current_phase} - {line.strip()}"
        if "当前处理视频 ID" in line:
            return f"{current_phase} - {line.strip()}"
        if "正在访问目标主页" in line:
            return f"{current_phase} - 正在打开抖音主页"
            
    if current_phase:
        return current_phase
        
    return "正在执行"

@app.get("/api/crawl/status/{task_id}")
def get_crawler_status(task_id: str):
    with tasks_lock:
        if task_id not in active_crawl_tasks:
            return {"status": "error", "message": "Task not found"}
        task_info = active_crawl_tasks[task_id]
        
    log_path = task_info["log_path"]
    logs = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                logs = "".join(lines[-150:])
        except UnicodeDecodeError:
            try:
                # 尝试用 GBK（带 errors="replace"）读取以解决 Windows 编码兼容性问题
                with open(log_path, "r", encoding="gbk", errors="replace") as f:
                    lines = f.readlines()
                    logs = "".join(lines[-150:])
            except Exception as e:
                logs = f"Failed to read logs (GBK): {e}"
        except Exception as e:
            logs = f"Failed to read logs: {e}"
            
    # 分析日志得出当前正在运行或卡住的步骤
    current_step = analyze_task_step(logs)
    
    # 扫描与当前任务运行时间匹配的截图文件
    screenshots = []
    screenshots_dir = os.path.join(ROOT_DIR, "screenshots")
    if os.path.exists(screenshots_dir) and task_info.get("started_at"):
        try:
            from datetime import datetime, timedelta
            started_dt = datetime.fromisoformat(task_info["started_at"])
            for filename in os.listdir(screenshots_dir):
                if filename.lower().endswith(".png"):
                    filepath = os.path.join(screenshots_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    mtime_dt = datetime.fromtimestamp(mtime)
                    # 允许 5 秒的系统启动误差
                    if mtime_dt >= started_dt - timedelta(seconds=5):
                        screenshots.append(f"/screenshots/{filename}")
        except Exception as err:
            print(f"Error scanning screenshots: {err}")
            
    return {
        "status": task_info["status"],
        "blogger": task_info["blogger"],
        "logs": logs,
        "current_step": current_step,
        "screenshots": screenshots
    }




# ----------------------------------------------------------
# 前端静态文件托管
# ----------------------------------------------------------
# 挂载 output 目录用于访问物理蒸馏文件
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
if os.path.exists(OUTPUT_DIR):
    app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
    print(f"[FastAPI] Mounted output directory: {OUTPUT_DIR}")

# 挂载 screenshots 目录用于前端排查截图访问
SCREENSHOTS_DIR = os.path.join(ROOT_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")
print(f"[FastAPI] Mounted screenshots directory: {SCREENSHOTS_DIR}")

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
