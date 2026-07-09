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
from typing import Optional, List
import requests
from datetime import datetime

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

    import re
    import contextlib

    while True:
        try:
            # 1. 加载配置参数并校验是否启用转录
            config_path = os.path.join(ROOT_DIR, "data", "config.json")
            whisper_url = "http://192.168.110.30:7211/transcribe"
            whisper_model = "medium"
            enable_transcribe = True
            transcribe_interval = 5
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        whisper_url = cfg.get("whisper_url", whisper_url)
                        whisper_model = cfg.get("whisper_model", whisper_model)
                        enable_transcribe = cfg.get("enable_transcribe", True)
                        transcribe_interval = int(cfg.get("transcribe_interval", 5))
                except:
                    pass
            
            if not enable_transcribe:
                # 若未开启转录，等待触发事件或扫描间隔时间（分钟）后继续检查
                transcribe_trigger_event.wait(timeout=transcribe_interval * 60)
                transcribe_trigger_event.clear()
                continue
            
            # 2. 查询需要转录的视频（desc 是包含 http 的视频直链，或者标记了转录重试且未达上限的记录）
            db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
            conn = sqlite3.connect(db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            pending_notes = cursor.execute("""
                SELECT n.id, n.title, n.desc, n.blogger_id, b.name as blogger_name 
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
                print(f"[语音转录后台 Worker] 扫描到 {len(pending_notes)} 个待转录/重试视频。")
                for note in pending_notes:
                    # 再次加载配置，保证在循环执行期间如果用户关闭了开关，能立即感知并退出
                    if os.path.exists(config_path):
                        try:
                            with open(config_path, "r", encoding="utf-8") as f:
                                cfg = json.load(f)
                                if not cfg.get("enable_transcribe", True):
                                    print("[语音转录后台 Worker] 用户在任务执行间隙关闭了转录功能，挂起任务。")
                                    break
                        except:
                            pass

                    note_id = note["id"]
                    raw_desc = note["desc"]
                    blogger_name = note["blogger_name"]
                    title = note["title"]
                    
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
                            
                    task_id = f"tx_{note_id}"
                    log_dir = os.path.join(ROOT_DIR, "data", "logs")
                    os.makedirs(log_dir, exist_ok=True)
                    log_path = os.path.join(log_dir, f"{task_id}.log")
                    
                    # 注册/更新内存任务状态为 running
                    with tasks_lock:
                        active_transcribe_tasks[task_id] = {
                            "id": task_id,
                            "note_id": note_id,
                            "title": title,
                            "blogger": f"{blogger_name}",
                            "status": "running",
                            "log_path": log_path,
                            "created_at": datetime.now().isoformat(),
                            "started_at": datetime.now().isoformat(),
                            "finished_at": None,
                            "current_step": f"正在转录视频 [{retry_count + 1}/3 次重试]"
                        }
                    
                    success = False
                    text = ""
                    
                    # 将转录的详细控制台信息写入专属 log 文件中，实现任务日志回显
                    try:
                        with open(log_path, "w", encoding="utf-8") as log_file:
                            log_file.write(f"=== 语音转录后台任务 {task_id} 启动 ===\n")
                            log_file.write(f"博主: {blogger_name}\n")
                            log_file.write(f"视频标题: {title}\n")
                            log_file.write(f"视频 URL: {video_url}\n")
                            log_file.write(f"Whisper 配置: model={whisper_model}, url={whisper_url}\n")
                            log_file.write(f"尝试次数: 第 {retry_count + 1} 次尝试\n\n")
                            log_file.flush()
                            
                            # 捕获 transcribe_with_retry 的标准输出重定向到日志文件中
                            with contextlib.redirect_stdout(log_file):
                                success, text = transcribe_with_retry(video_url, whisper_url, model=whisper_model, retries=2)
                    except Exception as le:
                        print(f"[语音转录后台 Worker] 写入任务日志错误: {le}")
                        
                    # 3. 回写数据库
                    conn = sqlite3.connect(db_path, timeout=30.0)
                    cursor = conn.cursor()
                    
                    final_text = ""
                    if success:
                        final_text = text
                        cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                        print(f"[语音转录后台 Worker] 视频 [{note_id}] 转录成功，内容已回填。")
                        with tasks_lock:
                            active_transcribe_tasks[task_id]["status"] = "success"
                            active_transcribe_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                            active_transcribe_tasks[task_id]["current_step"] = "转录成功，正文回填完成"
                    else:
                        if retry_count < 3:
                            # 允许重试，更新状态标记为下一轮重试
                            final_text = f"[转录失败_第{retry_count + 1}次重试]: {video_url}"
                            cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                            print(f"[语音转录后台 Worker] 视频 [{note_id}] 本轮转录失败，标记以待下轮重试：{text}")
                            with tasks_lock:
                                active_transcribe_tasks[task_id]["status"] = "failed"
                                active_transcribe_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                                active_transcribe_tasks[task_id]["current_step"] = f"本轮转录失败: {text}"
                        else:
                            # 达到上限，放弃重试
                            final_text = f"[转录失败_已达上限]: {video_url}"
                            cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?", (final_text, note_id))
                            print(f"[语音转录后台 Worker] 视频 [{note_id}] 转录失败已达上限，停止重试：{text}")
                            with tasks_lock:
                                active_transcribe_tasks[task_id]["status"] = "failed"
                                active_transcribe_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                                active_transcribe_tasks[task_id]["current_step"] = "转录失败已达上限"
                                
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

                    # 4.5. 当视频转录成功时，立刻触发数据库指标重算，把新解析出的文字/词频/观点句合并进大盘和详情页面中
                    if success:
                        try:
                            from utils.recalculate import recalculate_blogger_stats
                            recalculate_blogger_stats(blogger_name)
                        except Exception as re_err:
                            print(f"[语音转录后台 Worker] 重算指标失败: {re_err}")
                        
                    conn.close()
                    time.sleep(1) # 视频间隔安全休眠
            else:
                # 任务空闲时，等待触发事件或扫描间隔时间（分钟）后自动扫库
                transcribe_trigger_event.wait(timeout=transcribe_interval * 60)
                transcribe_trigger_event.clear()
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
    # 5. 启动自动定时对标更新调度器
    start_auto_crawl_scheduler()


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
def get_blogger_files_status(name: str, mode: str = "A"):
    """检测博主的物理蒸馏文件存在性，并根据当前 mode 返回静态访问路径"""
    import urllib.parse
    
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "output")
    
    # 模式 A: 对标分析
    a_report = f"{name}_蒸馏报告.html"
    a_skill = f"{name}_创作指南.skill/SKILL.md"
    a_soul = f"{name}_创作指南.skill/SOUL.md"
    
    # 模式 B: 自我诊断
    b_report = f"{name}_诊断报告.html"
    b_skill = f"{name}_创作基因.skill/SKILL.md"
    b_soul = f"{name}_创作基因.skill/SOUL.md"
    
    a_report_exists = os.path.exists(os.path.join(output_dir, a_report))
    b_report_exists = os.path.exists(os.path.join(output_dir, b_report))
    
    # 按照当前请求的 mode 决定主要返回哪个
    if mode == "B":
        report_filename = b_report
        skill_filename = b_skill
        soul_filename = b_soul
    else:
        report_filename = a_report
        skill_filename = a_skill
        soul_filename = a_soul
        
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
            },
            "has_mode_a": a_report_exists,
            "has_mode_b": b_report_exists
        }
    }

# ----------------------------------------------------------
# Codex AI 蒸馏集成 API 接口
# ----------------------------------------------------------

class DistillUpload(BaseModel):
    blogger: str
    mode: str = "A"  # "A" 或 "B"
    report_html: str
    skill_md: str
    soul_md: Optional[str] = None


@app.get("/api/distill/pending_tasks")
def list_pending_distill_tasks():
    """获取所有处于待分析状态的博主蒸馏任务底稿列表"""
    raw_material_dir = os.path.join(ROOT_DIR, "output", "_过程文件", "原始素材")
    tasks = []
    if os.path.exists(raw_material_dir):
        for filename in os.listdir(raw_material_dir):
            if filename.endswith("_AI蒸馏任务.md"):
                blogger_name = filename.replace("_AI蒸馏任务.md", "")
                tasks.append({
                    "blogger": blogger_name,
                    "filename": filename,
                    "filepath": f"output/_过程文件/原始素材/{filename}"
                })
    return tasks


@app.get("/api/distill/pending_tasks/{blogger}/content")
def get_pending_distill_task_content(blogger: str):
    """读取指定博主的蒸馏任务底稿原始 Markdown 文本内容"""
    task_file = os.path.join(ROOT_DIR, "output", "_过程文件", "原始素材", f"{blogger}_AI蒸馏任务.md")
    if not os.path.exists(task_file):
        raise HTTPException(status_code=404, detail=f"Blogger '{blogger}' AI distillation task draft file not found.")
    try:
        with open(task_file, "r", encoding="utf-8") as f:
            content = f.read()
        return {"blogger": blogger, "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/distill/upload")
def upload_distill_results(data: DistillUpload):
    """接收 Codex 蒸馏产物文本，保存到 output/ 并自动完成前端渲染准备"""
    name = data.blogger
    mode = data.mode
    output_dir = os.path.join(ROOT_DIR, "output")
    
    if mode == "B":
        report_path = os.path.join(output_dir, f"{name}_诊断报告.html")
        skill_dir = os.path.join(output_dir, f"{name}_创作基因.skill")
    else:
        report_path = os.path.join(output_dir, f"{name}_蒸馏报告.html")
        skill_dir = os.path.join(output_dir, f"{name}_创作指南.skill")
        
    try:
        # 1. 写入 HTML 报告
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(data.report_html)
            
        # 2. 写入 SKILL.md
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(data.skill_md)
            
        # 3. 写入 SOUL.md (可选)
        if data.soul_md:
            with open(os.path.join(skill_dir, "SOUL.md"), "w", encoding="utf-8") as f:
                f.write(data.soul_md)
                
        return {"status": "success", "message": f"Successfully written distillation outputs for blogger '{name}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded files: {str(e)}")


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
active_transcribe_tasks = {}
tasks_lock = threading.Lock()

# 用于立即唤醒后台转录 Worker 的线程同步事件
transcribe_trigger_event = threading.Event()

# 默认设置参数
DEFAULT_SETTINGS = {
    "whisper_url": "http://192.168.110.30:7211/transcribe",
    "whisper_model": "medium",
    "max_videos": 5,
    "headless": True,
    "enable_transcribe": True,
    "transcribe_interval": 5,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model_name": "gpt-4",
    "enable_auto_crawl": True,
    "crawl_time": "03:00",
    "enable_feishu": False,
    "feishu_chat_id": "",
    "feishu_app_id": "",
    "feishu_app_secret": ""
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

# ----------------------------------------------------------
# 飞书报警通知逻辑 (自建应用版，含图片上传与富文本发送)
# ----------------------------------------------------------
def get_feishu_tenant_token(app_id: str, app_secret: str) -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    payload = {"app_id": app_id, "app_secret": app_secret}
    res = requests.post(url, json=payload, headers=headers, timeout=15)
    res_data = res.json()
    if res_data.get("code") == 0:
        return res_data["tenant_access_token"]
    else:
        raise Exception(f"获取 Token 失败: {res_data.get('msg')}")

def upload_image_to_feishu(token: str, filepath: str) -> str:
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    with open(filepath, "rb") as f:
        files = {
            "image_type": (None, "message"),
            "image": (os.path.basename(filepath), f, "image/png")
        }
        res = requests.post(url, headers=headers, files=files, timeout=30)
        res_data = res.json()
        if res_data.get("code") == 0:
            return res_data["data"]["image_key"]
        else:
            raise Exception(f"上传图片失败: {res_data.get('msg')}")

def send_feishu_post_message(token: str, chat_id: str, title: str, content_list: list) -> dict:
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "post",
        "content": json.dumps({
            "zh_cn": {
                "title": title,
                "content": content_list
            }
        })
    }
    res = requests.post(url, json=payload, headers=headers, timeout=15)
    return res.json()

def check_and_notify_feishu_failures(task_id: str, started_at_str: str):
    settings = load_settings()
    if not settings.get("enable_feishu", False):
        return
        
    app_id = settings.get("feishu_app_id", "")
    app_secret = settings.get("feishu_app_secret", "")
    chat_id = settings.get("feishu_chat_id", "")
    
    if not app_id or not app_secret or not chat_id:
        return
        
    log_path = os.path.join(ROOT_DIR, "data", "logs", f"{task_id}.log")
    if not os.path.exists(log_path):
        return
        
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            logs = f.read()
    except Exception as e:
        print(f"[Feishu Alert] 读取日志失败: {e}")
        return

    import re
    failed_bloggers = set()
    
    exception_matches = re.findall(r"博主【(.*?)】在流水线运行期间发生未捕获异常", logs)
    for name in exception_matches:
        failed_bloggers.add(name.strip())
        
    phase_matches = re.findall(r"❌ 阶段失败: \[[^\]]*?\((.*?)\)\]", logs)
    for name in phase_matches:
        failed_bloggers.add(name.strip())
        
    if not failed_bloggers:
        start_matches = re.findall(r"=== 流水线任务 \S+ 启动 \(博主: '(.*?)'\) ===", logs)
        if start_matches and "failed" in logs.lower():
            for name in start_matches:
                failed_bloggers.add(name.strip())

    if not failed_bloggers:
        if "failed" in logs.lower() or "returncode" in logs.lower():
            failed_bloggers.add("全量对标更新任务")

    try:
        started_dt = datetime.fromisoformat(started_at_str)
    except:
        started_dt = datetime.min
        
    screenshot_dir = os.path.join(ROOT_DIR, "screenshots")
    screenshots = []
    if os.path.exists(screenshot_dir):
        for fname in os.listdir(screenshot_dir):
            if fname.endswith(".png"):
                filepath = os.path.join(screenshot_dir, fname)
                try:
                    mtime = os.path.getmtime(filepath)
                    mtime_dt = datetime.fromtimestamp(mtime)
                    if mtime_dt >= started_dt:
                        screenshots.append((mtime, filepath, fname))
                except:
                    pass
        screenshots.sort(key=lambda x: x[0])

    try:
        token = get_feishu_tenant_token(app_id, app_secret)
        
        for blogger in failed_bloggers:
            blogger_screenshot = None
            for mtime, filepath, fname in reversed(screenshots):
                if blogger.lower() in fname.lower() or fname == "login_qr.png":
                    blogger_screenshot = filepath
                    break
            
            if not blogger_screenshot and screenshots:
                blogger_screenshot = screenshots[-1][1]
                
            reason = "抓取流水线执行异常，请查看日志"
            if "扫码登录" in logs or "请使用手机" in logs:
                reason = "需手机抖音 APP 扫码登录 (Cookie 已过期)"
            elif "验证码拦截" in logs or "滑块解锁" in logs or "captcha" in logs.lower():
                reason = "滑动验证码拦截，需要手动解锁或更新风控规约"
            elif "导入 SQLite 库" in logs and "失败" in logs:
                reason = "数据合并导入 SQLite 数据库阶段出错"
            else:
                lines = [l.strip() for l in logs.split("\n") if l.strip()]
                for line in reversed(lines):
                    if "错误" in line or "Exception" in line or "Error" in line or "❌" in line:
                        reason = line
                        break

            content_list = []
            content_list.append([{"tag": "text", "text": f"博主/任务：{blogger}\n"}])
            content_list.append([{"tag": "text", "text": f"失败原因：{reason}\n"}])
            
            if blogger_screenshot and os.path.exists(blogger_screenshot):
                try:
                    img_key = upload_image_to_feishu(token, blogger_screenshot)
                    content_list.append([{"tag": "img", "image_key": img_key}])
                except Exception as img_err:
                    content_list.append([{"tag": "text", "text": f"（异常截图上传飞书失败: {img_err}）\n"}])
            else:
                content_list.append([{"tag": "text", "text": "（未检测到异常截图）\n"}])
                
            content_list.append([{"tag": "text", "text": f"详情日志请登录看板在「任务日志」模块查看。"}])
            
            send_feishu_post_message(token, chat_id, f"🚨 对标抓取任务异常提醒 - {blogger}", content_list)
            
    except Exception as fe:
        print(f"[Feishu Alert] 发送飞书报警失败: {fe}")

# ----------------------------------------------------------
# 自动定时对标抓取调度器逻辑
# ----------------------------------------------------------
def get_scheduler_state_path():
    return os.path.join(ROOT_DIR, "data", "scheduler_state.json")

def load_scheduler_state():
    path = get_scheduler_state_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"last_auto_crawl_time": None, "last_auto_crawl_date": None}

def save_scheduler_state(state):
    path = get_scheduler_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=4)
    except:
        pass

def auto_crawl_scheduler_loop():
    print("[自动定时更新调度器] 启动成功，延迟 5 分钟进行首次检测判别...")
    time.sleep(5 * 60)
    
    try:
        settings = load_settings()
        if settings.get("enable_auto_crawl", True):
            state = load_scheduler_state()
            last_time_str = state.get("last_auto_crawl_time")
            should_run_startup = False
            
            if not last_time_str:
                should_run_startup = True
            else:
                try:
                    last_time = datetime.fromisoformat(last_time_str)
                    elapsed = datetime.now() - last_time
                    if elapsed.total_seconds() >= 24 * 3600:
                        should_run_startup = True
                except:
                    should_run_startup = True
            
            if should_run_startup:
                print("[自动定时更新调度器] 检测到上次抓取超过 24 小时（或无记录），立即启动补跑任务...")
                run_crawler_pipeline(blogger="all")
                state["last_auto_crawl_time"] = datetime.now().isoformat()
                state["last_auto_crawl_date"] = datetime.now().strftime("%Y-%m-%d")
                save_scheduler_state(state)
    except Exception as se:
        print(f"[自动定时更新调度器] 启动判别出错: {se}")

    while True:
        try:
            time.sleep(60)
            settings = load_settings()
            if not settings.get("enable_auto_crawl", True):
                continue
                
            crawl_time_str = settings.get("crawl_time", "03:00")
            try:
                hour_str, min_str = crawl_time_str.split(":")
                sched_hour = int(hour_str)
                sched_min = int(min_str)
            except Exception as pe:
                print(f"[自动定时更新调度器] 无法解析设定时间 '{crawl_time_str}': {pe}")
                continue
                
            now = datetime.now()
            if now.hour == sched_hour and now.minute == sched_min:
                state = load_scheduler_state()
                today_str = now.strftime("%Y-%m-%d")
                if state.get("last_auto_crawl_date") != today_str:
                    print(f"[自动定时更新调度器] 当前时间 {now.strftime('%H:%M')}，到达每日设定更新点，触发自动抓取...")
                    run_crawler_pipeline(blogger="all")
                    state["last_auto_crawl_time"] = now.isoformat()
                    state["last_auto_crawl_date"] = today_str
                    save_scheduler_state(state)
        except Exception as e:
            print(f"[自动定时更新调度器] 轮询异常: {e}")

def start_auto_crawl_scheduler():
    t = threading.Thread(target=auto_crawl_scheduler_loop, daemon=True)
    t.start()

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
            headless = "true" if settings.get("headless", True) else "false"
            
            python_exe = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
                
            cmd = [
                python_exe,
                os.path.join(ROOT_DIR, "scripts", "pachopngjiaoben", "pipeline.py"),
                "--max-videos", str(max_videos),
                "--whisper-url", whisper_url,
                "--headless", headless
            ]
            if blogger != "all":
                cmd.extend(["--blogger", blogger])
                
            # 强制子进程以 UTF-8 编码模式运行以防 Windows GBK 终端乱码，且关闭缓冲以防无法实时读取输出
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
                
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=ROOT_DIR
                )
                
                with open(log_path, "w", encoding="utf-8", buffering=1) as log_file:
                    log_file.write(f"=== 流水线任务 {task_id} 启动 (博主: '{blogger}') ===\n")
                    log_file.write(f"配置参数: 抓取上限={max_videos}, Whisper模型={whisper_model}, Whisper接口={whisper_url}\n\n")
                    log_file.flush()
                    
                    # 实时按行读取并清刷输出，确保控制台能实时看到数据而非等结束后一次性呈现
                    for line in process.stdout:
                        log_file.write(line)
                        log_file.flush()
                        
                process.wait()
                    
                with tasks_lock:
                    if process.returncode == 0:
                        active_crawl_tasks[task_id]["status"] = "success"
                    else:
                        active_crawl_tasks[task_id]["status"] = "failed"
                    active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                    started_at = active_crawl_tasks[task_id]["started_at"]
                    
                try:
                    check_and_notify_feishu_failures(task_id, started_at)
                except Exception as fe_err:
                    print(f"[Queue Worker] 触发飞书通知失败: {fe_err}")
            except Exception as err:
                with tasks_lock:
                    active_crawl_tasks[task_id]["status"] = "failed"
                    active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                    started_at = active_crawl_tasks[task_id].get("started_at") or datetime.now().isoformat()
                try:
                    with open(log_path, "a", encoding="utf-8") as log_file:
                        log_file.write(f"\n[线程错误] 运行 pipeline 异常: {err}\n")
                except:
                    pass
                try:
                    check_and_notify_feishu_failures(task_id, started_at)
                except Exception as fe_err:
                    print(f"[Queue Worker] 触发飞书通知失败: {fe_err}")
            
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
    transcribe_interval: int = 5
    headless: bool = True
    enable_transcribe: bool = True
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_name: str = "gpt-4"
    enable_auto_crawl: bool = True
    crawl_time: str = "03:00"
    enable_feishu: bool = False
    feishu_chat_id: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

@app.post("/api/settings")
def update_settings_endpoint(settings: SettingsUpdate):
    data = {
        "whisper_url": settings.whisper_url,
        "whisper_model": settings.whisper_model,
        "max_videos": settings.max_videos,
        "transcribe_interval": settings.transcribe_interval,
        "headless": settings.headless,
        "enable_transcribe": settings.enable_transcribe,
        "openai_api_key": settings.openai_api_key,
        "openai_base_url": settings.openai_base_url,
        "openai_model_name": settings.openai_model_name,
        "enable_auto_crawl": settings.enable_auto_crawl,
        "crawl_time": settings.crawl_time,
        "enable_feishu": settings.enable_feishu,
        "feishu_chat_id": settings.feishu_chat_id,
        "feishu_app_id": settings.feishu_app_id,
        "feishu_app_secret": settings.feishu_app_secret
    }
    if save_settings(data):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save settings")

class FeishuTestRequest(BaseModel):
    feishu_chat_id: str
    feishu_app_id: str
    feishu_app_secret: str

@app.post("/api/settings/test_feishu")
def test_feishu_connectivity(body: FeishuTestRequest):
    """测试飞书报警通知连接性"""
    try:
        token = get_feishu_tenant_token(body.feishu_app_id, body.feishu_app_secret)
        content_list = [
            [{"tag": "text", "text": "恭喜您！您的信息源监控系统与飞书通知助手已成功联通！\n"}],
            [{"tag": "text", "text": f"测试时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"}],
            [{"tag": "text", "text": "后续如果自动对标更新调度任务发生验证码拦截、需要扫码登录或爬虫报错时，系统会自动发送异常截图与报警卡片到本会话中。"}]
        ]
        res_data = send_feishu_post_message(token, body.feishu_chat_id, "🔔 飞书报警通道联通性测试", content_list)
        if res_data.get("code") == 0:
            return {"status": "success", "message": "Test message sent successfully."}
        else:
            raise HTTPException(status_code=400, detail=f"发送消息失败: {res_data.get('msg')}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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

@app.get("/api/transcribe/tasks")
def get_transcribe_tasks():
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if not os.path.exists(db_path):
        return []
        
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        pending_notes = cursor.execute("""
            SELECT n.id, n.title, b.name as blogger_name
            FROM blogger_notes n
            JOIN bloggers b ON n.blogger_id = b.id
            WHERE n.type = 'video' AND (
                n.desc LIKE 'http://%' OR 
                n.desc LIKE 'https://%' OR 
                n.desc LIKE '[转录失败_第%'
            )
        """).fetchall()
        conn.close()
    except Exception as e:
        print(f"Error querying pending transcribe tasks: {e}")
        pending_notes = []
        
    tasks_list = []
    processed_note_ids = set()
    
    # 1. 内存中已启动过的转录任务
    with tasks_lock:
        for tid, t in list(active_transcribe_tasks.items()):
            processed_note_ids.add(str(t["note_id"]))
            tasks_list.append({
                "id": tid,
                "blogger": t["blogger"],
                "title": t["title"],
                "status": t["status"],
                "created_at": t["created_at"],
                "started_at": t["started_at"],
                "finished_at": t["finished_at"]
            })
            
    # 2. 数据库中仍在排队等待的转录视频项
    for note in pending_notes:
        nid = str(note["id"])
        if nid in processed_note_ids:
            continue
        tid = f"tx_{nid}"
        tasks_list.append({
            "id": tid,
            "blogger": note["blogger_name"],
            "title": note["title"],
            "status": "queued",
            "created_at": None,
            "started_at": None,
            "finished_at": None
        })
        
    # 按状态排序：进行中 (running) > 排队中 (queued) > 成功 (success) > 失败 (failed)
    status_order = {"running": 0, "queued": 1, "success": 2, "failed": 3}
    tasks_list.sort(key=lambda x: (status_order.get(x["status"], 4), x["created_at"] or ""))
    return tasks_list

@app.post("/api/transcribe/trigger")
def trigger_transcription_scan():
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    count = 0
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM blogger_notes
                WHERE type = 'video' AND (
                    desc LIKE 'http://%' OR 
                    desc LIKE 'https://%' OR 
                    desc LIKE '[转录失败_第%'
                )
            """)
            count = cursor.fetchone()[0]
            conn.close()
        except Exception as e:
            print(f"Error checking pending transcription count: {e}")
            
    transcribe_trigger_event.set()
    return {
        "status": "success", 
        "count": count,
        "message": f"Transcription scan triggered immediately. Found {count} pending video(s)."
    }

@app.post("/api/crawl/clear")
def clear_finished_tasks():
    global active_crawl_tasks, active_transcribe_tasks
    with tasks_lock:
        retained_tasks = {}
        for tid, t in active_crawl_tasks.items():
            if t["status"] in ["queued", "running"]:
                retained_tasks[tid] = t
        active_crawl_tasks = retained_tasks
        
        retained_tx = {}
        for tid, t in active_transcribe_tasks.items():
            if t["status"] in ["queued", "running"]:
                retained_tx[tid] = t
        active_transcribe_tasks = retained_tx
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
        
    if "请使用手机" in logs and "扫码登录" in logs:
        return "等待手机扫码登录中..."

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
    is_crawl = False
    
    with tasks_lock:
        if task_id in active_crawl_tasks:
            task_info = active_crawl_tasks[task_id]
            is_crawl = True
        elif task_id in active_transcribe_tasks:
            task_info = active_transcribe_tasks[task_id]
        else:
            task_info = None

    if not task_info:
        return {"status": "error", "message": "Task not found"}
        
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
    if is_crawl:
        current_step = analyze_task_step(logs)
    else:
        current_step = task_info.get("current_step", "正在进行后台语音转录...")
    
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
    # 支持从环境变量读取 HOST 和 PORT，方便 Docker 部署时绑定 0.0.0.0
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8000))
    # 端口绑定，且仅监控 web 目录，避免数据及缓存写入引发服务异常重启与队列丢失
    uvicorn.run("app:app", host=host, port=port, reload=True, reload_dirs=["web"])
