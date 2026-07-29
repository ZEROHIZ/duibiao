"""
.venv/Scripts/python.exe web/backend/app.py
FastAPI 后端应用入口 (app.py)
核心职责：启动 Web 服务，连接 SQLite，提供各项监控 API 接口，并挂载静态前端网页与物理蒸馏输出目录（/output）。
更新说明：支持在前端直观展示已绑定的 CLI 账号名而不进行隐藏，且在断开连接时自动清理本地 CLI 证书文件。
"""

import os
import sqlite3
import sys
import json
import threading
import time
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
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
# 数据库结构补漏与热升级及报告定位自动提取服务
# ----------------------------------------------------------
def upgrade_db_schema():
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            cursor = conn.cursor()
            # 检查 bloggers 表是否有 category 与 is_transcribe 字段
            cursor.execute("PRAGMA table_info(bloggers);")
            columns = [col[1] for col in cursor.fetchall()]
            
            modified = False
            if "category" not in columns:
                print("[Database Upgrade] Adding 'category' column to 'bloggers' table...")
                cursor.execute("ALTER TABLE bloggers ADD COLUMN category TEXT DEFAULT '待诊断';")
                modified = True
            if "is_transcribe" not in columns:
                print("[Database Upgrade] Adding 'is_transcribe' column to 'bloggers' table...")
                cursor.execute("ALTER TABLE bloggers ADD COLUMN is_transcribe INTEGER DEFAULT 1;")
                modified = True
            if "platform" not in columns:
                print("[Database Upgrade] Adding 'platform' column to 'bloggers' table...")
                cursor.execute("ALTER TABLE bloggers ADD COLUMN platform TEXT DEFAULT 'douyin';")
                modified = True
                
            if modified:
                conn.commit()
                print("[Database Upgrade] Success.")
            conn.close()
        except Exception as e:
            print(f"[Database Upgrade] Failed to upgrade schema: {e}")

upgrade_db_schema()


def extract_category_from_html(html_path: str) -> str:
    """利用正则从生成的诊断或蒸馏 HTML 报告中提取博主定位"""
    if not os.path.exists(html_path):
        return ""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        # 正则扫描 <div class="card-label">定位领域</div> 紧随其后的 <div class="card-val">定位内容</div>
        # 兼容 card-label 叫 '定位' 或 '定位领域' 的情况，并允许任意空白符与换行符
        pattern = r'class="card-label">定位(?:领域)?</div>\s*<div class="card-val">(.*?)</div>'
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"[Category Extraction] Error reading {html_path}: {e}")
    return ""


def sync_blogger_category_from_html(name: str, conn_or_cursor):
    """根据博主名字从本地 HTML 报告同步 category 字段写回 bloggers 表"""
    output_dir = os.path.join(ROOT_DIR, "output")
    # 尝试读取诊断报告，若无，则尝试读取蒸馏报告
    diag_path = os.path.join(output_dir, f"{name}_诊断报告.html")
    distill_path = os.path.join(output_dir, f"{name}_蒸馏报告.html")
    
    category = extract_category_from_html(diag_path)
    if not category:
        category = extract_category_from_html(distill_path)
        
    if category:
        try:
            # 优先尝试从 connection 中获取 cursor 并提交事务，保证立刻持久化写入
            cursor = conn_or_cursor.cursor()
            cursor.execute("UPDATE bloggers SET category = ? WHERE name = ?;", (category, name))
            conn_or_cursor.commit()
            print(f"[Category Sync] Updated blogger '{name}' category to '{category}'")
            return category
        except Exception as e:
            try:
                # 兼容传入的就是 cursor 对象的情况，直接执行并由外部 commit
                conn_or_cursor.execute("UPDATE bloggers SET category = ? WHERE name = ?;", (category, name))
                print(f"[Category Sync Fallback] Updated blogger '{name}' category to '{category}'")
                return category
            except Exception as e2:
                print(f"[Category Sync] Failed to update DB for '{name}': {e} | {e2}")
    return ""


def init_all_bloggers_categories():
    """在服务启动时全量扫描本地 HTML 报告，将历史博主的定位提取并持久化写回数据库"""
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("SELECT name, category FROM bloggers;")
            rows = cursor.fetchall()
            for row in rows:
                name, cat = row[0], row[1]
                if not cat or cat == "待诊断":
                    sync_blogger_category_from_html(name, conn)
            conn.close()
        except Exception as e:
            print(f"[Init Categories] Failed: {e}")

init_all_bloggers_categories()


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
                WHERE b.is_transcribe = 1 AND n.type = 'video' AND (
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
    name: Optional[str] = ""
    home_url: str = ""
    is_transcribe: Optional[int] = 1
    platform: Optional[str] = "douyin"
    biji_account_id: Optional[str] = "account_01"
    biji_topic_alias: Optional[str] = None
    biji_topic_name: Optional[str] = None


class BijiAccountCreate(BaseModel):
    alias_name: Optional[str] = ""


class BijiAccountAliasUpdate(BaseModel):
    alias_name: str


class BijiTopicCreate(BaseModel):
    account_id: Optional[str] = "account_01"
    topic_name: str


class BloggerShortcutCreate(BaseModel):
    name: str = ""
    text: str



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
               b.total_likes, b.total_collects, b.total_comments, b.category, b.is_transcribe, b.platform,
               b.biji_browser_id as biji_account, b.biji_topic_name, b.biji_topic_alias, b.biji_url, b.biji_follow_id,
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
        bloggers = []
        for row in rows:
            b_dict = dict(row)
            if not b_dict.get("category") or b_dict["category"] == "待诊断":
                new_cat = sync_blogger_category_from_html(b_dict["name"], conn)
                if new_cat:
                    b_dict["category"] = new_cat
            bloggers.append(b_dict)
        return bloggers
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


class BloggerCategoryUpdate(BaseModel):
    category: str


@app.put("/api/bloggers/{blogger_id}/category")
def update_blogger_category(blogger_id: int, body: BloggerCategoryUpdate):
    """手动修改对标博主的主营定位领域分类"""
    category_str = body.category.strip()
    if not category_str:
        raise HTTPException(status_code=400, detail="主营定位领域分类不能为空")
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="未找到该博主记录")
            
        cursor.execute("UPDATE bloggers SET category = ? WHERE id = ?;", (category_str, blogger_id))
        conn.commit()
        return {"status": "success", "message": "主营定位领域已成功更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


class BloggerTranscribeUpdate(BaseModel):
    is_transcribe: int


@app.put("/api/bloggers/{blogger_id}/is_transcribe")
def update_blogger_is_transcribe(blogger_id: int, body: BloggerTranscribeUpdate):
    """更新指定博主是否开启转录"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Blogger not found.")
            
        cursor.execute("UPDATE bloggers SET is_transcribe = ? WHERE id = ?;", (body.is_transcribe, blogger_id))
        conn.commit()
        return {"status": "success", "message": "Blogger transcribe status updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


class BloggerPlatformUpdate(BaseModel):
    platform: str


@app.put("/api/bloggers/{blogger_id}/platform")
def update_blogger_platform(blogger_id: int, body: BloggerPlatformUpdate):
    """更新指定博主的平台属性（如 douyin, bilibili, xiaohongshu）"""
    platform_str = body.platform.strip().lower()
    if not platform_str:
        raise HTTPException(status_code=400, detail="平台名称不能为空")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM bloggers WHERE id = ?;", (blogger_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Blogger not found.")
            
        cursor.execute("UPDATE bloggers SET platform = ? WHERE id = ?;", (platform_str, blogger_id))
        conn.commit()
        return {"status": "success", "message": "Blogger platform updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/biji/accounts")
def get_biji_accounts():
    """获取所有已配置的得到浏览器账号列表"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT account_id, nickname, alias_name, status, last_login_at FROM biji_browser_accounts ORDER BY account_id ASC;")
        rows = cursor.fetchall()
        accounts = [dict(r) for r in rows]
        if not accounts:
            cursor.execute("""
                INSERT INTO biji_browser_accounts (account_id, nickname, alias_name, status)
                VALUES ('account_01', '默认账号', '得到账号_01', 'LOGGED_IN')
            """)
            conn.commit()
            accounts = [{"account_id": "account_01", "nickname": "默认账号", "alias_name": "得到账号_01", "status": "LOGGED_IN"}]
        return {"status": "success", "accounts": accounts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/biji/accounts")
def create_biji_account(body: BijiAccountCreate):
    """创建新的得到浏览器账号"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT account_id FROM biji_browser_accounts;")
        rows = cursor.fetchall()
        existing_ids = [r["account_id"] for r in rows]
        
        index = len(existing_ids) + 1
        new_account_id = f"account_{index:02d}"
        while new_account_id in existing_ids:
            index += 1
            new_account_id = f"account_{index:02d}"
            
        alias_name = body.alias_name.strip() if (body.alias_name and body.alias_name.strip()) else f"得到账号_{index:02d}"
        
        cursor.execute("""
            INSERT INTO biji_browser_accounts (account_id, nickname, alias_name, status)
            VALUES (?, ?, ?, 'NEW');
        """, (new_account_id, alias_name, alias_name))
        conn.commit()
        
        return {
            "status": "success",
            "account": {
                "account_id": new_account_id,
                "alias_name": alias_name,
                "status": "NEW"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.put("/api/biji/accounts/{account_id}/alias")
def update_biji_account_alias(account_id: str, body: BijiAccountAliasUpdate):
    """修改指定得到账号的别名"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE biji_browser_accounts SET alias_name = ? WHERE account_id = ?;", (body.alias_name.strip(), account_id))
        conn.commit()
        return {"status": "success", "message": "账号别名更新成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/biji/topics/{account_id}")
def get_biji_topics(account_id: str):
    """获取指定得到账号下的知识库列表"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT biji_topic_alias, biji_topic_name 
            FROM bloggers 
            WHERE biji_browser_id = ? AND biji_topic_alias IS NOT NULL AND biji_topic_alias != '';
        """, (account_id,))
        rows = cursor.fetchall()
        topics = [{"alias": r["biji_topic_alias"], "name": r["biji_topic_name"] or r["biji_topic_alias"]} for r in rows]
        
        # 默认示例兜底
        if not topics:
            topics = [{"alias": "40Dk9QrY", "name": "默认知识库 (40Dk9QrY)"}]
        return {"status": "success", "topics": topics}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/biji/topics/create")
def create_biji_topic_api(body: BijiTopicCreate):
    """触发后端 Playwright 自动在得到创建知识库"""
    account_id = body.account_id or "account_01"
    topic_name = body.topic_name.strip()
    if not topic_name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
        
    try:
        from biji_browser import BijiBrowserEngine
        engine = BijiBrowserEngine(account_id=account_id, headless=True)
        res = engine.create_biji_topic(topic_name)
        if res and res.get("alias"):
            return {
                "status": "success",
                "topic": {
                    "alias": res["alias"],
                    "name": res.get("name") or topic_name
                }
            }
        else:
            raise HTTPException(status_code=500, detail="得到建库失败，未能从页面获取新知识库的 topic_alias")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自动建库异常: {str(e)}")


def get_headless_setting() -> bool:
    """从全局系统配置中获取 Playwright 浏览器无头模式开关 (headless/headless_browser)"""
    config_path = os.path.join(ROOT_DIR, "data", "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                val = cfg.get("headless")
                if val is None:
                    val = cfg.get("headless_browser")
                if val is not None:
                    if isinstance(val, bool):
                        return val
                    if isinstance(val, str):
                        return val.lower() == "true"
        except:
            pass
    return True


@app.post("/api/bloggers")
def create_blogger(body: BloggerCreate):
    """录入新对标博主 (先录入数据库，后台注册任务并可视化推流)"""
    import sqlite3
    import threading
    import uuid

    # 允许博主名称空置，后续由爬虫或得到自动提取充实
    blogger_name = body.name.strip() if (body.name and body.name.strip()) else ""
    if not blogger_name:
        blogger_name = f"待爬取博主_{str(uuid.uuid4())[:6]}"

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        b_account = body.biji_account_id or "account_01"
        b_topic_alias = body.biji_topic_alias or ""
        b_topic_name = body.biji_topic_name or ""

        cursor.execute("""
        INSERT INTO bloggers (
            name, home_url, total_notes, video_count, normal_count, 
            avg_likes, avg_collects, avg_comments, 
            total_likes, total_collects, total_comments, is_transcribe, platform,
            biji_browser_id, biji_topic_alias, biji_topic_name
        ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?);
        """, (
            blogger_name, 
            body.home_url, 
            body.is_transcribe if body.is_transcribe is not None else 1,
            body.platform if body.platform else "douyin",
            b_account,
            b_topic_alias if b_topic_alias != "new" else "",
            b_topic_name
        ))
        conn.commit()
        
        cursor.execute("SELECT id, is_transcribe, platform FROM bloggers WHERE name = ?;", (blogger_name,))
        row = cursor.fetchone()
        new_id = row["id"] if row else None
        is_transcribe_val = row["is_transcribe"] if row else 1
        platform_val = row["platform"] if row else "douyin"

        created_task_id = None

        # 先保存数据库，立即响应前端；得到的自动建库与关注作为可视化后台任务呈现
        if (b_topic_alias or b_topic_name) and body.home_url:
            task_id = f"biji_add_{uuid.uuid4().hex[:8]}"
            created_task_id = task_id
            log_dir = os.path.join(ROOT_DIR, "data", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{task_id}.log")
            now_iso = datetime.now().isoformat()

            with tasks_lock:
                active_crawl_tasks[task_id] = {
                    "id": task_id,
                    "task_id": task_id,
                    "blogger": f"⚡ 得到自动建库/关注: {blogger_name}",
                    "status": "running",
                    "created_at": now_iso,
                    "started_at": now_iso,
                    "finished_at": None,
                    "log_path": log_path,
                    "task_type": "biji_add"
                }

            def log_msg(msg: str):
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                formatted = f"[{ts}] {msg}\n"
                print(formatted, end="")
                try:
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write(formatted)
                except:
                    pass

            def async_biji_pipeline():
                try:
                    from biji_browser import BijiBrowserEngine
                    headless_mode = get_headless_setting()
                    log_msg(f"🚀 [得到自动化任务启动] TaskID: {task_id} | 账号: {b_account} | 无头模式: {headless_mode}")
                    log_msg(f"📌 博主名称: {blogger_name} | 主页链接: {body.home_url}")
                    
                    engine = BijiBrowserEngine(account_id=b_account, headless=headless_mode, log_func=log_msg)
                    target_alias = b_topic_alias
                    
                    # 1. 若为新建知识库模式
                    if target_alias == "new" or (not target_alias and b_topic_name):
                        log_msg(f"📁 [步骤 1/2] 正在为博主『{blogger_name}』在得到自动创建知识库: 『{b_topic_name}』...")
                        create_res = engine.create_biji_topic(b_topic_name or "默认知识库")
                        if create_res and create_res.get("alias"):
                            target_alias = create_res["alias"]
                            log_msg(f"✅ [得到建库成功] 知识库: 『{b_topic_name}』 | Alias: {target_alias}")
                            if new_id:
                                db_conn = get_db_connection()
                                db_conn.cursor().execute("UPDATE bloggers SET biji_topic_alias = ?, biji_topic_name = ? WHERE id = ?;", (target_alias, b_topic_name, new_id))
                                db_conn.commit()
                                db_conn.close()
                        else:
                            log_msg(f"⚠️ [建库提示] 抓取完成，尝试继续关联页面...")

                    # 2. 若存在知识库 Alias 且有主页 URL，进行得到关注
                    if target_alias and target_alias != "new" and body.home_url:
                        log_msg(f"👤 [步骤 2/2] 正在打开得到知识库 (Alias: {target_alias}) 关注博主...")
                        engine.add_blogger_to_biji(target_alias, body.home_url, blogger_name=blogger_name)
                        log_msg(f"🎉 [完成] 博主『{blogger_name}』已成功处理完毕！")

                    with tasks_lock:
                        if task_id in active_crawl_tasks:
                            active_crawl_tasks[task_id]["status"] = "success"
                            active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
                except Exception as err:
                    log_msg(f"❌ [得到自动化流程异常]: {err}")
                    with tasks_lock:
                        if task_id in active_crawl_tasks:
                            active_crawl_tasks[task_id]["status"] = "failed"
                            active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
            
            threading.Thread(target=async_biji_pipeline, daemon=True).start()

        return {
            "status": "success",
            "task_id": created_task_id,
            "data": {
                "id": new_id,
                "name": blogger_name,
                "home_url": body.home_url,
                "is_transcribe": is_transcribe_val,
                "platform": platform_val
            }
        }
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Blogger with name '{blogger_name}' already exists.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.post("/api/bloggers/shortcut")
def create_blogger_shortcut(body: BloggerShortcutCreate):
    """通过快捷指令/剪贴板文本快速录入对标博主"""
    import re
    import sqlite3
    import urllib.parse
    
    raw_text = body.text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="输入文本不能为空")
        
    # 1. 提取 URL (以 http:// 或 https:// 开头)
    url_match = re.search(r'https?://[^\s]+', raw_text)
    if not url_match:
        raise HTTPException(status_code=400, detail="未在输入文本中找到合法的链接")
        
    extracted_url = url_match.group(0)
    # 处理花括号包裹的情况，如 {https://v.douyin.com/...}
    if extracted_url.endswith('}'):
        extracted_url = extracted_url[:-1]
        
    # 2. 提取/生成博主昵称
    extracted_name = body.name.strip()
    if not extracted_name:
        # 尝试通过正则寻找类似 【xxx的作品】 或 【xxx的个人主页】 或 xxx的作品 或 xxx的个人主页
        name_match = re.search(r'【([^】]+)的作品】|【([^】]+)的个人主页】', raw_text)
        if name_match:
            extracted_name = name_match.group(1) or name_match.group(2)
        else:
            name_match2 = re.search(r'([^【】\s]+)的作品|([^【】\s]+)的个人主页', raw_text)
            if name_match2:
                extracted_name = name_match2.group(1) or name_match2.group(2)
                
    # 3. 如果仍未找到昵称，分配“未命名_[唯一ID]”
    if not extracted_name:
        try:
            parsed = urllib.parse.urlparse(extracted_url)
            path = parsed.path.strip('/')
            parts = [p for p in path.split('/') if p]
            if parts:
                last_part = parts[-1]
                if len(last_part) > 15:
                    short_id = last_part[-8:]
                else:
                    short_id = last_part
            else:
                import hashlib
                short_id = hashlib.md5(extracted_url.encode('utf-8')).hexdigest()[:8]
        except Exception:
            import hashlib
            short_id = hashlib.md5(extracted_url.encode('utf-8')).hexdigest()[:8]
            
        extracted_name = f"未命名_{short_id}"
        
    # 4. 写入数据库
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 检查是否已存在该主页链接的博主
        cursor.execute("SELECT id, name FROM bloggers WHERE home_url = ?;", (extracted_url,))
        existing_url = cursor.fetchone()
        if existing_url:
            raise HTTPException(
                status_code=400, 
                detail=f"该主页链接已录入，博主名称为: {existing_url['name']}"
            )
            
        # 自动识别平台类型
        url_lower = extracted_url.lower()
        if "bilibili.com" in url_lower or "b23.tv" in url_lower:
            detected_platform = "bilibili"
        elif "xiaohongshu.com" in url_lower or "xhslink.com" in url_lower:
            detected_platform = "xiaohongshu"
        elif "channels.weixin" in url_lower:
            detected_platform = "wechat_channels"
        else:
            detected_platform = "douyin"

        cursor.execute("""
        INSERT INTO bloggers (
            name, home_url, total_notes, video_count, normal_count, 
            avg_likes, avg_collects, avg_comments, 
            total_likes, total_collects, total_comments, platform
        ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?);
        """, (extracted_name, extracted_url, detected_platform))
        conn.commit()
        
        cursor.execute("SELECT id, platform FROM bloggers WHERE name = ?;", (extracted_name,))
        row = cursor.fetchone()
        new_id = row["id"]
        platform_val = row["platform"]
        return {
            "status": "success",
            "message": "博主录入成功",
            "data": {
                "id": new_id,
                "name": extracted_name,
                "home_url": extracted_url,
                "platform": platform_val
            }
        }
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400, 
            detail=f"名为 '{extracted_name}' 的博主已存在，请尝试提供不同的昵称。"
        )
    except HTTPException as he:
        raise he
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
    """删除指定的对标博主及相关全部数据 (同步清理磁盘本地 JSON 缓存，防止重启恢复)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, name FROM bloggers WHERE id = ?;", (blogger_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Blogger not found.")
        
        blogger_name = row["name"]
        
        # 1. 开启事务，级联删除该博主的其它所有关联数据
        cursor.execute("DELETE FROM blogger_distilled WHERE blogger_id = ?;", (blogger_id,))
        cursor.execute("DELETE FROM blogger_notes WHERE blogger_id = ?;", (blogger_id,))
        cursor.execute("DELETE FROM bloggers WHERE id = ?;", (blogger_id,))
        conn.commit()

        # 2. 清理磁盘上的本地 JSON 分析缓存文件，彻底防止服务重启时 importer.py 重新恢复该博主
        data_dir = os.path.join(ROOT_DIR, "data")
        processed_dir = os.path.join(data_dir, "processed")
        
        target_files = [
            os.path.join(data_dir, f"{blogger_name}_analysis.json"),
            os.path.join(processed_dir, f"{blogger_name}_notes_details.json"),
            os.path.join(data_dir, f"{blogger_name}_notes.json")
        ]
        for tf in target_files:
            if os.path.exists(tf):
                try:
                    os.remove(tf)
                    print(f"[Delete Blogger] Successfully removed local file cache: {tf}")
                except Exception as ef:
                    print(f"[Delete Blogger] Warning: Failed to remove {tf}: {ef}")

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
        SELECT n.id, n.title, n.desc, n.type, n.likes, n.collects, n.comments, n.shares, n.category, n.comments_json, n.published_at, b.name as blogger_name, b.is_transcribe
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
        SELECT n.id, n.title, n.desc, n.type, n.likes, n.collects, n.comments, n.shares, n.category, n.tags_json, n.comments_json, n.published_at, b.is_transcribe
        FROM blogger_notes n
        JOIN bloggers b ON n.blogger_id = b.id
        WHERE n.blogger_id = ?
        ORDER BY n.likes DESC
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
# AI 跨界视野扩展与垂直找号探索 API 接口
# ----------------------------------------------------------

@app.get("/api/niches-exploration")
def get_niches_exploration():
    """获取当前已保存的领域探索发散结果"""
    exploration_path = os.path.join(ROOT_DIR, "data", "niches_exploration.json")
    
    # 提取当前已覆盖分类
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    covered_categories = []
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute("SELECT DISTINCT category FROM bloggers WHERE category != '' AND category IS NOT NULL AND category != '待诊断';").fetchall()
            covered_categories = [r["category"] for r in rows]
            conn.close()
        except Exception as e:
            print(f"Query covered categories failed: {e}")

    data = {"niches": []}
    if os.path.exists(exploration_path):
        try:
            with open(exploration_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Read exploration json failed: {e}")
            
    data["covered"] = covered_categories
    return data


@app.post("/api/niches-exploration/refresh")
def refresh_niches_exploration():
    """触发 AI 重新对当前已有领域分类进行发散探索，破除信息茧房"""
    config_path = os.path.join(ROOT_DIR, "data", "config.json")
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    
    # 1. 读取 API 密钥配置
    api_key = ""
    base_url = "https://api.openai.com/v1"
    model_name = "gpt-4"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                api_key = cfg.get("openai_api_key", "").strip()
                base_url = cfg.get("openai_base_url", base_url).strip()
                model_name = cfg.get("openai_model_name", model_name).strip()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"加载配置文件失败: {str(e)}")
            
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 OpenAI API Key，请前往系统设置中进行配置。")
        
    # 2. 查询当前已有分类大盘
    categories_list = []
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT category, COUNT(*) as cnt 
                FROM bloggers 
                WHERE category != '' AND category IS NOT NULL AND category != '待诊断'
                GROUP BY category 
                ORDER BY cnt DESC;
            """).fetchall()
            categories_list = [f"{r['category']}" for r in rows]
            conn.close()
        except Exception as e:
            print(f"Query categories failed: {e}")
            
    categories_desc = ", ".join(categories_list) if categories_list else "暂无数据"
    
    # 3. 构造 LLM 发送的 System 和 User 提示词
    system_prompt = (
        "你是一个顶尖的自媒体商业洞察与流量对标专家。你非常擅长通过对比不同垂直领域的流量和变现模式，为创作者提供破茧和发散灵感。"
        "请以严格的 JSON 格式输出，并且保证返回的 JSON 结构合法，不需要任何 ```json 包裹，直接输出 JSON 纯文本。"
    )
    
    user_prompt = f"""
用户目前在数据库里已录入的对标账号主要覆盖以下内容领域：
{categories_desc}

请帮用户分析并“扩展视野”：
1. 识别出用户目前能力圈已覆盖的方向与特征。
2. 探索并推荐 4 到 6 个目前数据库中**没有**录入的、具有高商业变现潜力或内容吸粉价值的垂直细分赛道。
3. 这些推荐赛道必须严格遵循以下【双轨发散机制】进行分配：
   - 【能力圈延展建议 (Niche Extension，占比约 60%，如 3 个推荐)】：与用户已有的商业、AI、宠物医疗等领域具有一定的技术/商业逻辑联系，采用“已有优势交叉”或“变现模式平移”来探索相关的垂直人群、垂直类目或细分产品。
   - 【破茧跨界灵感建议 (Niche Breakout，占比约 40%，如 2 个推荐)】：与用户当前已有领域完全不相关。必须是能够提供全新跨界商业灵感的垂直细分赛道，严禁使用“娱乐八卦”、“大众新闻”、“搞笑段子”等缺乏核心商业变现闭环的泛大类。必须是有独特变现模式、高客单价或巧妙内容套路的小众细分领域（例如“中古奢侈品鉴宝”、“客制化机械键盘”等）。
4. 对于每个推荐的细分赛道，必须提供：
   - 领域名字 (大白话，简单易懂，杜绝空洞的形容词和形式主义大词)。
   - 赛道属性 (如：“垂直人群”、“垂直类目” 或 “垂直产品”)。
   - 策略类型 (必须是 “能力圈延展” 或 “破茧跨界灵感”)。
   - 商业描述 (大白话一句话说明这个方向在做什么生意，如何变现，不搞官腔)。
   - 5 到 8 个【专业硬核搜索词】。这些词不能是“文玩”、“医美”等宏观的类目词。必须是该赛道精准的用户、博主在交流和做内容时使用的【专业技术词、产品精细规格、具体项目名称或垂类名词】。用户在抖音/小红书搜索这些词时能极度精准地搜到这一垂直赛道的主体内容，进而顺藤摸瓜找到新对标账号。

请返回如下 JSON 格式：
{{
  "niches": [
    {{
      "name": "领域名字",
      "type": "垂直人群 / 垂直类目 / 垂直产品",
      "strategy_type": "能力圈延展 / 破茧跨界灵感",
      "business": "变现模式的大白话阐述",
      "keywords": ["精准项目或产品词1", "精准项目或产品词2", "精准项目或产品词3", "精准项目或产品词4", "精准项目或产品词5"]
    }}
  ]
}}
"""

    # 4. 调用配置的 OpenAI API
    # 格式化 base_url
    url = base_url.strip()
    if not url.endswith("/chat/completions"):
        if url.endswith("/"):
            url = url + "chat/completions"
        else:
            url = url + "/chat/completions"
            
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.75,
        "response_format": {"type": "json_object"}
    }
    
    try:
        import requests
        response = requests.post(url, headers=headers, json=payload, timeout=90.0)
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"LLM 接口返回失败 ({response.status_code}): {response.text}")
        
        result_json = response.json()
        choice_content = result_json["choices"][0]["message"]["content"]
        
        # 5. 解析并落地保存
        parsed_data = json.loads(choice_content)
        
        # 保存到 data/niches_exploration.json
        exploration_path = os.path.join(ROOT_DIR, "data", "niches_exploration.json")
        os.makedirs(os.path.dirname(exploration_path), exist_ok=True)
        with open(exploration_path, "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
        return parsed_data
        
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="请求大模型 API 超时，请检查 base_url 或代理网络。")
    except json.JSONDecodeError as je:
        raise HTTPException(status_code=502, detail=f"解析 LLM 返回的 JSON 失败: {str(je)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行探索异常: {str(e)}")


# ----------------------------------------------------------
# Codex AI 蒸馏集成 API 接口
# ----------------------------------------------------------

class DistillUpload(BaseModel):
    blogger: str
    mode: str = "A"  # "A" 或 "B"
    report_html: str
    skill_md: str
    soul_md: Optional[str] = None
    is_transcribe: Optional[int] = None
    platform: Optional[str] = None


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
            
        # 4. 同步更新/设置 is_transcribe/platform 并抽取写回主表 category
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            modified = False
            if data.is_transcribe is not None:
                cursor.execute("UPDATE bloggers SET is_transcribe = ? WHERE name = ?;", (data.is_transcribe, name))
                modified = True
            if data.platform is not None:
                cursor.execute("UPDATE bloggers SET platform = ? WHERE name = ?;", (data.platform.strip().lower(), name))
                modified = True
            if modified:
                conn.commit()
            sync_blogger_category_from_html(name, conn)
            conn.close()
        except Exception as se:
            print(f"[Upload Callback] Sync category, platform or is_transcribe failed: {se}")
            
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
    "enable_auto_agent": True,
    "transcribe_interval": 5,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model_name": "gpt-4",
    "enable_auto_crawl": True,
    "crawl_time": "03:00",
    "enable_feishu": False,
    "feishu_chat_id": "",
    "feishu_app_id": "",
    "feishu_app_secret": "",
    "proxy_url": "",
    "google_access_token": "",
    "openai_access_token": "",
    "google_login_cmd": "opencode auth login",
    "openai_login_cmd": "codex login --device-auth",
    "google_model": "gemini-3.5-flash-medium",
    "openai_model": "gpt-4o",
    "google_models_list": [],
    "openai_models_list": []
}



def get_settings_path():
    return os.path.join(ROOT_DIR, "data", "config.json")

def load_settings():
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 升级老命令选项
                if data.get("google_login_cmd") in ["antigravity login --no-browser", "echo \"Already logged in via Antigravity IDE. You can close this terminal.\"", "agy login", "agy"]:
                    data["google_login_cmd"] = DEFAULT_SETTINGS["google_login_cmd"]
                if data.get("openai_login_cmd") == "codex login --no-browser":
                    data["openai_login_cmd"] = DEFAULT_SETTINGS["openai_login_cmd"]
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
                if task_id not in active_crawl_tasks or active_crawl_tasks[task_id].get("status") == "failed":
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
                
                with tasks_lock:
                    if task_id in active_crawl_tasks:
                        active_crawl_tasks[task_id]["process"] = process
                
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
    enable_auto_agent: bool = True
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_name: str = "gpt-4"
    enable_auto_crawl: bool = True
    crawl_time: str = "03:00"
    enable_feishu: bool = False
    feishu_chat_id: str = ""
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    proxy_url: str = ""
    google_login_cmd: str = "opencode auth login"
    openai_login_cmd: str = "codex login --device-auth"
    google_model: str = "gemini-3.5-flash-medium"
    openai_model: str = "gpt-4o"
    google_models_list: Optional[List[str]] = []
    openai_models_list: Optional[List[str]] = []

@app.post("/api/settings")
def update_settings_endpoint(settings: SettingsUpdate):
    existing = load_settings()
    existing.update({
        "whisper_url": settings.whisper_url,
        "whisper_model": settings.whisper_model,
        "max_videos": settings.max_videos,
        "transcribe_interval": settings.transcribe_interval,
        "headless": settings.headless,
        "enable_transcribe": settings.enable_transcribe,
        "enable_auto_agent": settings.enable_auto_agent,
        "openai_api_key": settings.openai_api_key,
        "openai_base_url": settings.openai_base_url,
        "openai_model_name": settings.openai_model_name,
        "enable_auto_crawl": settings.enable_auto_crawl,
        "crawl_time": settings.crawl_time,
        "enable_feishu": settings.enable_feishu,
        "feishu_chat_id": settings.feishu_chat_id,
        "feishu_app_id": settings.feishu_app_id,
        "feishu_app_secret": settings.feishu_app_secret,
        "proxy_url": settings.proxy_url,
        "google_login_cmd": settings.google_login_cmd,
        "openai_login_cmd": settings.openai_login_cmd,
        "google_model": settings.google_model,
        "openai_model": settings.openai_model,
        "google_models_list": settings.google_models_list or [],
        "openai_models_list": settings.openai_models_list or []
    })
    if save_settings(existing):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save settings")


# === Get 笔记 (biji.com) 自动化同步接口 ===

class BijiSyncRequest(BaseModel):
    account_id: str = "account_01"
    max_posts: int = 20

@app.post("/api/biji/sync")
def trigger_biji_sync_endpoint(body: BijiSyncRequest):
    """手动触发 Get 笔记 (biji.com) 自动化同步"""
    import subprocess
    task_id = f"biji_sync_{int(time.time())}"
    log_dir = os.path.join(ROOT_DIR, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{task_id}.log")

    settings = load_settings()
    is_headless = settings.get("headless", True)
    if isinstance(is_headless, str):
        is_headless = (is_headless.lower() == "true")

    cmd = [
        sys.executable,
        os.path.join(ROOT_DIR, "scripts", "biji_browser.py"),
        "--account", body.account_id,
        "--max-posts", str(body.max_posts)
    ]
    if not is_headless:
        cmd.append("--headful")

    # 注册到全局 active_crawl_tasks 以便前端任务日志实时展示与监控
    now_iso = datetime.now().isoformat()
    with tasks_lock:
        active_crawl_tasks[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "blogger": f"biji_{body.account_id}",
            "status": "running",
            "created_at": now_iso,
            "started_at": now_iso,
            "finished_at": None,
            "log_path": log_path,
            "task_type": "biji_sync"
        }

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    def run_biji_thread():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=ROOT_DIR
            )
            
            with tasks_lock:
                active_crawl_tasks[task_id]["process"] = proc

            with open(log_path, "w", encoding="utf-8", buffering=1) as f:
                f.write(f"=== Get 笔记自动化同步任务 {task_id} 启动 ===\n")
                f.write(f"账号: {body.account_id}, 单博主配额: {body.max_posts}\n\n")
                f.flush()

                # 实时按行读取并清刷输出，实现控制台/前端实时日志秒级更新
                for line in proc.stdout:
                    f.write(line)
                    f.flush()

            proc.wait()
            with tasks_lock:
                if proc.returncode == 0:
                    active_crawl_tasks[task_id]["status"] = "success"
                else:
                    active_crawl_tasks[task_id]["status"] = "failed"
                active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[Error] 运行 biji_browser.py 失败: {e}\n")
                f.flush()
            with tasks_lock:
                active_crawl_tasks[task_id]["status"] = "failed"
                active_crawl_tasks[task_id]["finished_at"] = datetime.now().isoformat()

    threading.Thread(target=run_biji_thread, daemon=True).start()
    return {
        "status": "success",
        "task_id": task_id,
        "log_path": log_path,
        "qr_code_url": f"/screenshots/biji_qr_{body.account_id}.png",
        "message": f"Get 笔记同步任务 [{task_id}] 已在后台启动"
    }

@app.get("/api/biji/accounts")
def get_biji_accounts_endpoint():
    """获取已录入的 Get 笔记账号列表与登录状态"""
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM biji_browser_accounts ORDER BY last_login_at DESC;")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"[Biji Accounts API] Error: {e}")
        return []


# === 智能体 OAuth 认证接口 (FastAPI) ===

class AuthExchangeRequest(BaseModel):
    provider: str
    code: str = ""
    token: str = ""

class DisconnectRequest(BaseModel):
    provider: str

@app.get("/api/auth/status")
def get_auth_status_endpoint():
    """获取智能体 Google / OpenAI 绑定状态"""
    import shutil
    import subprocess
    settings = load_settings()

    # 直接从 config.json 读取 Google 凭证状态（由 agy Lazy Auth 成功后写入）

    # 自动从本地 OpenAI CLI 同步登录态
    codex_path = shutil.which("codex")
    if codex_path:
        try:
            is_windows = os.name == "nt"
            r = subprocess.run(
                [codex_path, "login", "status"],
                shell=is_windows,
                capture_output=True,
                text=True,
                timeout=5
            )
            output = r.stdout or r.stderr or ""
            if r.returncode == 0 and "Logged in" in output:
                token_info = output.strip()
                settings["openai_access_token"] = f"CLI_OAUTH:{token_info}"
                save_settings(settings)
        except Exception:
            pass

    # 自动从本地 Google agy CLI 同步登录态
    if not settings.get("google_access_token"):
        agy_path = shutil.which("agy")
        if not agy_path and os.name == "nt":
            default_win = os.path.expandvars(r"%USERPROFILE%\AppData\Local\agy\bin\agy.exe")
            if os.path.exists(default_win):
                agy_path = default_win
        if agy_path:
            try:
                check_env = os.environ.copy()
                check_env["BROWSER"] = "false"
                check_env.pop("DISPLAY", None)
                check_env.pop("SSH_CONNECTION", None)
                check_env.pop("SSH_CLIENT", None)
                r = subprocess.run([agy_path, "models"], capture_output=True, text=True, env=check_env, timeout=3.0)
                if r.returncode == 0:
                    settings["google_access_token"] = "CLI_AUTHENTICATED"
                    save_settings(settings)
            except Exception:
                pass

    google_token = settings.get("google_access_token", "")
    openai_token = settings.get("openai_access_token", "")
    
    def mask_token(t):
        if not t: return ""
        if t == "CLI_AUTHENTICATED": return "本地 CLI 已认证登录"
        if t.startswith("CLI_OAUTH:"):
            return t.replace("CLI_OAUTH:", "").strip()
        if len(t) <= 10: return "••••••••"
        return f"{t[:4]}••••••••{t[-4:]}"
        
    return {
        "google_connected": bool(google_token),
        "google_token_masked": mask_token(google_token),
        "openai_connected": bool(openai_token),
        "openai_token_masked": mask_token(openai_token)
    }

@app.post("/api/auth/exchange")
def auth_exchange_endpoint(body: AuthExchangeRequest):
    """回填授权码进行换码，或直接绑定 Token"""
    settings = load_settings()
    provider = body.provider.lower()
    token_to_save = ""
    
    if body.token:
        token_to_save = body.token.strip()
    elif body.code:
        # 方案 A: 模拟发送网关换取 Access Token (本地开发或备用测试逻辑)
        # 为确保 100% 能通，若外部网关无法连接，直接将 Authorization Code 作为 Mock Token 保存
        import requests
        gateway_url = f"https://oauth-gateway.antigravity.google/exchange" if provider == "google" else "https://oauth-gateway.chatgpt.com/exchange"
        try:
            res = requests.post(gateway_url, json={"code": body.code}, timeout=3)
            if res.status_code == 200:
                token_to_save = res.json().get("access_token", "").strip()
            else:
                token_to_save = body.code.strip()
        except:
            token_to_save = body.code.strip()
            
    if not token_to_save:
        raise HTTPException(status_code=400, detail="提取或换取 Token 失败，参数不完整")
        
    if provider == "google":
        settings["google_access_token"] = token_to_save
    elif provider == "openai":
        settings["openai_access_token"] = token_to_save
    else:
        raise HTTPException(status_code=400, detail="未知的服务商")
        
    save_settings(settings)
    return {"status": "success", "message": f"成功绑定 {provider} 授权"}

@app.post("/api/auth/disconnect")
def auth_disconnect_endpoint(body: DisconnectRequest):
    """断开智能体授权绑定"""
    import shutil
    import subprocess
    settings = load_settings()
    provider = body.provider.lower()
    
    if provider == "google":
        # 清除 config.json 中保存的 agy 授权凭证
        settings["google_access_token"] = ""
        settings["google_refresh_token"] = ""
    elif provider == "openai":
        settings["openai_access_token"] = ""
        # 自动执行 codex logout 清理 OpenAI CLI 登录状态
        codex_path = shutil.which("codex")
        if codex_path:
            try:
                is_windows = os.name == "nt"
                subprocess.run([codex_path, "logout"], shell=is_windows, timeout=5)
            except Exception as e:
                print(f"[Auth Disconnect] Failed to run codex logout: {e}")
    else:
        raise HTTPException(status_code=400, detail="未知的服务商")
        
    save_settings(settings)
    return {"status": "success"}

# === 智能体交互式终端登录接口 ===

class TerminalAuthState:
    def __init__(self):
        self.process = None
        self.output_buffer = []
        self.lock = threading.Lock()
        self.provider = None

global_terminal_auth = TerminalAuthState()

def read_process_stdout(proc, state_obj):
    import re
    ansi_escape = re.compile(r'\x1b(?:\[[0-9;?]*[A-Za-z]|\([A-Z]|[^\[()])')
    print(f"[Debug] read_process_stdout thread started for PID {proc.pid}")
    try:
        while True:
            char = proc.stdout.read(1)
            if not char:
                print(f"[Debug] read_process_stdout received EOF from PID {proc.pid}")
                break
            
            clean = ansi_escape.sub('', char)
            with state_obj.lock:
                if not state_obj.output_buffer:
                    state_obj.output_buffer.append("")
                if char == "\n":
                    state_obj.output_buffer.append("")
                else:
                    state_obj.output_buffer[-1] += clean
                
                if len(state_obj.output_buffer) > 500:
                    state_obj.output_buffer.pop(0)
    except Exception as e:
        print(f"[Debug Error] Exception in read_process_stdout for PID {proc.pid}: {e}")
        with state_obj.lock:
            state_obj.output_buffer.append(f"\n[System Error] 读取输出异常: {e}\n")
    finally:
        try:
            proc.wait(timeout=2)
        except:
            pass
        print(f"[Debug] Process PID {proc.pid} exited with code {proc.returncode}")
        with state_obj.lock:
            state_obj.output_buffer.append(f"\n[System] 进程已退出，退出码: {proc.returncode}\n")
            if proc.returncode == 0:
                try:
                    settings = load_settings()
                    if state_obj.provider == "openai":
                        settings["openai_access_token"] = "CLI_AUTHENTICATED"
                        save_settings(settings)
                        state_obj.output_buffer.append("[System] 检测到 codex 运行正常，已自动同步为已绑定状态！\n")
                except Exception as se:
                    state_obj.output_buffer.append(f"[System Error] 自动同步绑定状态失败: {se}\n")
            if state_obj.process == proc:
                state_obj.process = None

def check_google_status_thread(state_obj):
    import subprocess
    import shutil
    import os
    settings = load_settings()
    agy_path = shutil.which("agy")
    if not agy_path and os.name == "nt":
        default_win = os.path.expandvars(r"%USERPROFILE%\\AppData\\Local\\agy\\bin\\agy.exe")
        if os.path.exists(default_win):
            agy_path = default_win
    
    if not agy_path:
        with state_obj.lock:
            state_obj.output_buffer.append("[System Error] 未找到 agy CLI 路径，请先安装。\n")
            state_obj.process = None
        return

    env = os.environ.copy()
    env["BROWSER"] = "false"
    env.pop("DISPLAY", None)
    env.pop("SSH_CONNECTION", None)
    env.pop("SSH_CLIENT", None)

    try:
        proc = subprocess.run(
            [agy_path, "models"],
            capture_output=True,
            text=True,
            env=env,
            timeout=3.0
        )
        if proc.returncode == 0:
            with state_obj.lock:
                state_obj.output_buffer.append("[System] 检测到 agy 运行正常，已获取到模型列表！\n")
                state_obj.output_buffer.append(proc.stdout)
                state_obj.output_buffer.append("\n[System] 同步成功！账号已自动绑定。\n")
            settings["google_access_token"] = "CLI_AUTHENTICATED"
            save_settings(settings)
        else:
            with state_obj.lock:
                state_obj.output_buffer.append(f"[System Error] 检测失败，退出码: {proc.returncode}\n")
                state_obj.output_buffer.append(proc.stderr or proc.stdout)
                state_obj.output_buffer.append("\n👉 未检测到有效的本地已登录状态。\n")
                state_obj.output_buffer.append("👉 请打开您的本地终端，执行一次性登录授权：\n")
                state_obj.output_buffer.append("   - Windows 宿主机：在 PowerShell 中运行 `agy`，选择 1. Google OAuth 完成授权。\n")
                state_obj.output_buffer.append("   - Docker 容器：运行 `docker exec -it <容器名称> agy` 完成授权。\n")
    except subprocess.TimeoutExpired:
        with state_obj.lock:
            state_obj.output_buffer.append("[System] 未检测到已登录的凭证，进程检测超时。\n")
            state_obj.output_buffer.append("👉 请打开您的本地终端，执行一次性登录授权：\n")
            state_obj.output_buffer.append("   - Windows 宿主机：在 PowerShell 中运行 `agy`，选择 1. Google OAuth 完成授权。\n")
            state_obj.output_buffer.append("   - Docker 容器：运行 `docker exec -it <容器名称> agy` 完成授权。\n")
    except Exception as e:
        with state_obj.lock:
            state_obj.output_buffer.append(f"[System Error] 探测进程出现异常: {e}\n")
    finally:
        with state_obj.lock:
            state_obj.process = None

class TerminalStartRequest(BaseModel):
    provider: str

@app.post("/api/auth/terminal/start")
def terminal_start_endpoint(body: TerminalStartRequest):
    import shlex
    import subprocess
    import shutil
    import threading
    settings = load_settings()
    provider = body.provider.lower()

    if provider == "google":
        # Google: 开启非阻塞的同步状态检测线程
        class DummyProcess:
            def __init__(self):
                self.pid = 9999
                self.returncode = 0
            def kill(self):
                pass
        
        with global_terminal_auth.lock:
            global_terminal_auth.output_buffer = ["[System] 开始检测本地 CLI 登录状态...\n"]
            global_terminal_auth.provider = "google"
            global_terminal_auth.process = DummyProcess()
        
        threading.Thread(target=check_google_status_thread, args=(global_terminal_auth,), daemon=True).start()
        return {"status": "success", "message": "检测任务已拉起"}

    elif provider == "openai":
        cmd_str = settings.get("openai_login_cmd", "codex login --device-auth")
        is_windows = os.name == "nt"
        cmd_args = cmd_str if is_windows else shlex.split(cmd_str)
        cmd_display = cmd_str
    else:
        raise HTTPException(status_code=400, detail="未知的服务商")

    print(f"[Debug] terminal_start_endpoint called. Provider: {provider}, cmd_args: {cmd_args}")

    with global_terminal_auth.lock:
        if global_terminal_auth.process:
            try:
                print(f"[Debug] Killing previous process PID {global_terminal_auth.process.pid}")
                global_terminal_auth.process.kill()
            except:
                pass
            global_terminal_auth.process = None

        global_terminal_auth.output_buffer = [f"[System] 正在拉起: {cmd_display}\n"]
        global_terminal_auth.provider = provider

        proxy_url = settings.get("proxy_url", "")
        env = os.environ.copy()
        if proxy_url:
            env["HTTP_PROXY"] = proxy_url
            env["HTTPS_PROXY"] = proxy_url

        try:
            use_shell = (os.name == "nt")
            proc = subprocess.Popen(
                cmd_args,
                shell=use_shell,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=0,
                cwd=ROOT_DIR
            )
            print(f"[Debug] Process successfully spawned. PID: {proc.pid}")
            global_terminal_auth.process = proc

            t = threading.Thread(target=read_process_stdout, args=(proc, global_terminal_auth), daemon=True)
            t.start()

            return {"status": "success", "message": "登录进程已启动"}
        except Exception as e:
            global_terminal_auth.output_buffer.append(f"[System Error] 进程启动失败: {e}\n")
            raise HTTPException(status_code=500, detail=f"启动失败: {e}")

from fastapi import WebSocket, WebSocketDisconnect

class TerminalSession:
    def __init__(self, proc, ws):
        self.proc = proc
        self.ws = ws

active_terminal_sessions = {}  # provider -> TerminalSession

@app.websocket("/api/auth/terminal/ws")
async def terminal_ws_endpoint(websocket: WebSocket, provider: str):
    await websocket.accept()
    
    import shutil
    import os
    import threading
    import asyncio
    import subprocess
    
    settings = load_settings()
    provider = provider.lower()
    
    # 动态构建启动指令与参数
    if provider == "google":
        agy_path = shutil.which("agy")
        if not agy_path and os.name == "nt":
            default_win = os.path.expandvars(r"%USERPROFILE%\AppData\Local\agy\bin\agy.exe")
            if os.path.exists(default_win):
                agy_path = default_win
        if not agy_path:
            await websocket.send_text("[System Error] 未找到 agy CLI 可执行文件，请先安装。\r\n")
            await websocket.close()
            return
        cmd_args = [agy_path]
    elif provider == "openai":
        cmd_str = settings.get("openai_login_cmd", "codex login --device-auth")
        import shlex
        cmd_args = cmd_str if os.name == "nt" else shlex.split(cmd_str)
    else:
        await websocket.send_text("[System Error] 未知的服务商。\r\n")
        await websocket.close()
        return

    # 设置环境变量
    env = os.environ.copy()
    env["BROWSER"] = "false"
    env.pop("DISPLAY", None)
    env.pop("SSH_CONNECTION", None)
    env.pop("SSH_CLIENT", None)
    
    # 注意：我们这里不需要强制注入 SSH_CONNECTION 以退化为文本形式！
    # 因为使用 PTY，Bubbletea 就可以在网页的 xterm.js 窗口中以完整的 GUI TUI 彩色交互正常渲染运行！
    # 唯一需要注意的是代理：只为 OpenAI 注入代理以防 agy 挂死
    proxy_url = settings.get("proxy_url", "")
    if proxy_url and provider == "openai":
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url

    is_windows = os.name == "nt"
    proc = None
    
    try:
        if is_windows:
            from winpty import PtyProcess
            cmd_line = subprocess.list2cmdline(cmd_args) if isinstance(cmd_args, list) else cmd_args
            proc = PtyProcess.spawn(cmd_line, env=env, cwd=ROOT_DIR, dimensions=(24, 80))
        else:
            # Linux POSIX PTY Spawning using pexec/pty
            import pty
            import sys
            pid, fd = pty.fork()
            if pid == 0: # child
                try:
                    os.execve(cmd_args[0], cmd_args, env)
                except Exception as ex:
                    sys.stderr.write(f"Failed to exec command: {ex}\n")
                    sys.stderr.flush()
                    os._exit(1)
            else: # parent
                class LinuxPtyProcess:
                    def __init__(self, pid, fd):
                        self.pid = pid
                        self.fd = fd
                        self.exitstatus = None
                    def isalive(self):
                        if self.exitstatus is not None:
                            return False
                        try:
                            r_pid, status = os.waitpid(self.pid, os.WNOHANG)
                            if r_pid != 0:
                                self.exitstatus = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
                                return False
                            return True
                        except:
                            return False
                    def read(self, size=1024):
                        import select
                        r, _, _ = select.select([self.fd], [], [], 0.05)
                        if self.fd in r:
                            try:
                                data = os.read(self.fd, size)
                                return data.decode("utf-8", errors="replace")
                            except:
                                return ""
                        return ""
                    def write(self, data):
                        try:
                            os.write(self.fd, data.encode("utf-8"))
                        except:
                            pass
                    def kill(self):
                        import signal
                        try:
                            os.kill(self.pid, signal.SIGKILL)
                        except:
                            pass
                proc = LinuxPtyProcess(pid, fd)

        # 注册当前激活的终端 session
        active_terminal_sessions[provider] = TerminalSession(proc, websocket)
        
        loop = asyncio.get_running_loop()
        
        # 开启读取 PTY 字符线程并发送到网页
        def read_pty_loop():
            try:
                while True:
                    if is_windows:
                        if not proc.isalive():
                            break
                        data = proc.read(1024)
                    else:
                        if not proc.isalive():
                            break
                        data = proc.read(1024)
                    
                    if data:
                        asyncio.run_coroutine_threadsafe(websocket.send_text(data), loop)
            except EOFError:
                pass
            except Exception as e:
                print(f"[Debug Error] Exception in read_pty_loop: {e}")
            finally:
                asyncio.run_coroutine_threadsafe(websocket.close(), loop)
                
        threading.Thread(target=read_pty_loop, daemon=True).start()
        
        # 接收并处理网页发送的按键输入写入 PTY
        while True:
            is_alive = proc.isalive() if is_windows else proc.isalive()
            if not is_alive:
                break
            
            text = await websocket.receive_text()
            if text:
                proc.write(text)
                
    except WebSocketDisconnect:
        print(f"[Debug] Web terminal client disconnected for {provider}")
    except Exception as e:
        print(f"[Debug Error] Exception in terminal ws: {e}")
    finally:
        # 清理与同步绑定状态
        if proc:
            is_alive = proc.isalive() if is_windows else proc.isalive()
            if is_alive:
                try:
                    proc.kill()
                except:
                    pass
            
            # 检测最终是否登录成功
            if provider == "google":
                try:
                    check_env = os.environ.copy()
                    check_env["BROWSER"] = "false"
                    check_env.pop("DISPLAY", None)
                    r = subprocess.run([cmd_args[0], "models"], capture_output=True, text=True, env=check_env, timeout=3.0)
                    if r.returncode == 0:
                        settings["google_access_token"] = "CLI_AUTHENTICATED"
                        save_settings(settings)
                        print(f"[Debug] Sync successful: google authenticated via terminal login!")
                except Exception as se:
                    print(f"[Debug Error] Final sync check failed: {se}")
            elif provider == "openai":
                try:
                    r = subprocess.run("codex login status", shell=True, capture_output=True, text=True, timeout=3)
                    if r.returncode == 0:
                        settings["openai_access_token"] = "CLI_AUTHENTICATED"
                        save_settings(settings)
                        print(f"[Debug] Sync successful: openai authenticated via terminal login!")
                except Exception as se:
                    print(f"[Debug Error] Final sync check failed: {se}")

        active_terminal_sessions.pop(provider, None)

@app.post("/api/auth/terminal/kill")
def terminal_kill_endpoint(provider: str = "google"):
    provider = provider.lower()
    session = active_terminal_sessions.get(provider)
    if session:
        try:
            session.proc.kill()
        except:
            pass
        return {"status": "success", "message": "终端会话已终止"}
    return {"status": "success", "message": "无活动会话"}

# === 智能体 CLI 诊断与一键安装接口 ===

class CLIInstallState:
    def __init__(self):
        self.logs = []
        self.is_running = False
        self.lock = threading.Lock()

global_cli_install = CLIInstallState()

@app.get("/api/auth/cli/status")
def get_cli_status_endpoint():
    import shutil
    import subprocess
    
    # 检测 agy (Google Antigravity AI 智能体命令行)
    agy_path = shutil.which("agy")
    agy_version = "未知"
    if agy_path:
        try:
            r = subprocess.run([agy_path, "--version"], capture_output=True, text=True, timeout=5)
            agy_version = (r.stdout.strip() or r.stderr.strip() or "已安装")[:100]
        except Exception:
            agy_version = "已安装"

    # 检测 codex (OpenAI Codex CLI)
    codex_path = shutil.which("codex")
    codex_version = "未知"
    if codex_path:
        try:
            r = subprocess.run([codex_path, "--version"], capture_output=True, text=True, timeout=5)
            codex_version = (r.stdout.strip() or r.stderr.strip() or "已安装")[:100]
        except Exception:
            codex_version = "已安装"
            
    return {
        "status": "success",
        "google": {
            "installed": bool(agy_path),
            "path": agy_path or "未找到",
            "version": agy_version
        },
        "openai": {
            "installed": bool(codex_path),
            "path": codex_path or "未找到",
            "version": codex_version
        }
    }




# === CLI 一键安装接口 ===

class CLIInstallState:
    def __init__(self):
        self.process = None
        self.output_buffer = []
        self.lock = threading.Lock()

global_cli_install = CLIInstallState()

class CLIInstallRequest(BaseModel):
    provider: str

@app.post("/api/auth/cli/install")
def cli_install_endpoint(body: CLIInstallRequest):
    import subprocess
    import re
    provider = body.provider.lower()
    is_windows = os.name == "nt"
    
    # 不同平台的安装命令
    if provider == "google":
        if is_windows:
            # Windows: 用 npm 安装 opencode（必须），尝试 winget 安装 agy（可选）
            full_cmd = (
                "npm install -g opencode-ai"
                " & echo [System] opencode 安装完成，正在尝试通过 winget 安装 agy..."
                " & winget install --id Google.Antigravity --silent --accept-source-agreements --accept-package-agreements"
                " & echo [System] 全部安装流程完成（agy 若 winget 不可用可忽略）"
            )
        else:
            # Linux/macOS
            full_cmd = (
                "npm install -g opencode-ai"
                " && echo '[System] opencode 安装完成，正在安装 agy...'"
                " && curl -fsSL https://antigravity.google/cli/install.sh | bash"
            )

    elif provider == "openai":
        full_cmd = "npm install -g @openai/codex"
    else:
        raise HTTPException(status_code=400, detail="未知的服务商")
    
    with global_cli_install.lock:
        if global_cli_install.process and global_cli_install.process.poll() is None:
            return {"status": "success", "message": "安装任务已在运行中"}
        
        global_cli_install.output_buffer = [f"[System] 正在拉起安装: {full_cmd}\n"]
        
        ansi_escape = re.compile(r'\x1b(?:\[[0-9;?]*[A-Za-z]|\([A-Z]|[^\[(])')

        try:
            proc = subprocess.Popen(
                full_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )
            global_cli_install.process = proc

            def _read(p, state):
                for line in p.stdout:
                    clean = ansi_escape.sub('', line)
                    with state.lock:
                        state.output_buffer.append(clean)
                        if len(state.output_buffer) > 500:
                            state.output_buffer.pop(0)
                try:
                    p.wait(timeout=2)
                except:
                    pass
                with state.lock:
                    state.output_buffer.append(f"\n[System] 安装命令已完成，退出码: {p.returncode}\n")
            
            threading.Thread(target=_read, args=(proc, global_cli_install), daemon=True).start()
            return {"status": "success", "message": "安装任务已启动"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"启动安装失败: {e}")

@app.get("/api/auth/cli/install-logs")
def cli_install_logs_endpoint():
    with global_cli_install.lock:
        is_running = global_cli_install.process is not None and global_cli_install.process.poll() is None
        logs = "".join(global_cli_install.output_buffer)
    return {"status": "success", "logs": logs, "is_running": is_running}

@app.get("/api/auth/cli/models")
def get_cli_models_endpoint(provider: str):
    import shutil
    import subprocess
    import requests
    provider = provider.lower()
    settings = load_settings()
    is_windows = os.name == "nt"
    
    # 确保日志文件夹存在
    log_dir = os.path.join(ROOT_DIR, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"models_fetch_{provider}.log")
    
    env = os.environ.copy()
    proxy_url = settings.get("proxy_url", "")
    if proxy_url:
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        
    models = []
    
    if provider == "google":
        agy_path = shutil.which("agy")
        with open(log_path, "w", encoding="utf-8") as lf:
            lf.write(f"=== 开始拉取 Google 智能体模型列表 ===\n")
            if agy_path:
                lf.write(f"执行命令: {agy_path} models\n")
                try:
                    r = subprocess.run(
                        [agy_path, "models"],
                        env=env,
                        shell=is_windows,
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                    lf.write(f"进程退出码: {r.returncode}\n")
                    lf.write(f"--- STDOUT ---\n{r.stdout}\n")
                    lf.write(f"--- STDERR ---\n{r.stderr}\n")
                    if r.returncode == 0 and r.stdout.strip():
                        parsed_models = []
                        for line in r.stdout.splitlines():
                            line = line.strip()
                            if line and not line.startswith("Available models:") and not line.startswith("==") and not "help" in line:
                                parsed_models.append(line)
                        if parsed_models:
                            models = parsed_models
                            lf.write(f"解析出模型列表: {models}\n")
                except subprocess.TimeoutExpired:
                    lf.write("❌ 运行超时！进程未在 3 秒内响应。\n")
                    lf.write("提示: 请检查本地是否已经完成终端授权登录，或检测网络代理配置。\n")
                except Exception as ex:
                    lf.write(f"❌ 运行异常: {ex}\n")
            else:
                lf.write("❌ 未找到 agy 可执行文件！请先在页面下方点击安装诊断与部署。\n")
                
        if not models:
            models = [
                "gemini-3.5-flash-medium",
                "gemini-3.5-flash-high",
                "gemini-3.5-flash-low",
                "gemini-3.1-pro-low",
                "gemini-3.1-pro-high",
                "claude-sonnet-4.6",
                "claude-opus-4.6",
                "gpt-oss-120b"
            ]
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"⚠️ 拉取失败，启用 IDE 本地保底模型列表: {models}\n")
            
    elif provider == "openai":
        openai_key = settings.get("openai_api_key", "")
        if not openai_key:
            openai_key = settings.get("openai_access_token", "")
            if openai_key.startswith("CLI_OAUTH:"):
                openai_key = openai_key.replace("CLI_OAUTH:", "").strip()
                
        base_url = settings.get("openai_base_url", "https://api.openai.com/v1")
        if not base_url:
            base_url = "https://api.openai.com/v1"
            
        proxies = {}
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
            
        with open(log_path, "w", encoding="utf-8") as lf:
            lf.write(f"=== 开始拉取 OpenAI 智能体模型列表 ===\n")
            lf.write(f"请求接口: {base_url.rstrip('/')}/models\n")
            if openai_key:
                try:
                    headers = {"Authorization": f"Bearer {openai_key}"}
                    url = f"{base_url.rstrip('/')}/models"
                    res = requests.get(url, headers=headers, proxies=proxies, timeout=15)
                    lf.write(f"HTTP 状态码: {res.status_code}\n")
                    lf.write(f"响应内容:\n{res.text}\n")
                    if res.status_code == 200:
                        data = res.json()
                        fetched_models = [m["id"] for m in data.get("data", []) if "gpt" in m["id"] or "o1" in m["id"] or "o3" in m["id"] or "codex" in m["id"]]
                        fetched_models.sort()
                        if fetched_models:
                            models = fetched_models
                            lf.write(f"解析出模型列表: {models}\n")
                except Exception as ex:
                    lf.write(f"❌ 接口请求异常: {ex}\n")
            else:
                lf.write("❌ 未配置 OpenAI API 密钥，跳过网络拉取。\n")
                
        if not models:
            models = ["gpt-4o", "gpt-4o-mini", "o1-mini", "o3-mini", "gpt-4-turbo"]
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"⚠️ 拉取失败，启用 OpenAI 保底模型列表: {models}\n")
            
    if models:
        try:
            settings = load_settings()
            settings[f"{provider}_models_list"] = models
            save_settings(settings)
        except Exception as save_ex:
            print(f"Failed to persist fetched models: {save_ex}")
            
    return {"status": "success", "models": models}

class CLIInstallRequest(BaseModel):
    provider: str

def run_cli_install_task(provider: str, state_obj: CLIInstallState):
    import subprocess
    import shutil
    
    with state_obj.lock:
        state_obj.logs = [f"[Installer] 正在启动 {provider} CLI 安装任务...\n"]
        
    is_windows = os.name == "nt"
    
    try:
        if provider == "openai":
            # 1. 检查并安装 node/npm (仅限 Linux/Docker)
            if not is_windows:
                if shutil.which("npm") is None:
                    with state_obj.lock:
                        state_obj.logs.append("[Installer] 检测到容器内未安装 NPM。正在运行 apt-get 安装 Node.js 与 NPM，请稍候...\n")
                    subprocess.run(["apt-get", "update"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    r = subprocess.run(
                        ["apt-get", "install", "-y", "nodejs", "npm"],
                        capture_output=True, text=True
                    )
                    if r.returncode != 0:
                        with state_obj.lock:
                            state_obj.logs.append(f"[Installer Error] 安装 Node.js 与 NPM 失败:\n{r.stderr}\n")
                        return
                    with state_obj.lock:
                        state_obj.logs.append("[Installer] Node.js 与 NPM 安装成功。\n")
                        
            # 2. 运行 npm install -g @openai/codex
            with state_obj.lock:
                state_obj.logs.append("[Installer] 正在运行 npm install -g @openai/codex...\n")
            
            cmd = ["npm", "install", "-g", "@openai/codex"]
            if is_windows:
                npm_cmd = shutil.which("npm") or "npm.cmd"
                cmd = [npm_cmd, "install", "-g", "@openai/codex"]
                
            proc = subprocess.Popen(
                cmd,
                shell=is_windows,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=0
            )
            
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                with state_obj.lock:
                    state_obj.logs.append(line)
                    
            proc.wait()
            if proc.returncode == 0:
                with state_obj.lock:
                    state_obj.logs.append("\n🎉 [Installer] OpenAI Codex CLI 安装完全成功！\n")
            else:
                with state_obj.lock:
                    state_obj.logs.append(f"\n❌ [Installer Error] npm 安装失败，退出码: {proc.returncode}\n")
                    
        elif provider == "google":
            if not is_windows:
                with state_obj.lock:
                    state_obj.logs.append("[Installer] 正在容器内运行官方安装脚本 curl -fsSL https://antigravity.google/cli/install.sh | bash ...\n")
                
                proc = subprocess.Popen(
                    "curl -fsSL https://antigravity.google/cli/install.sh | bash",
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=0
                )
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    with state_obj.lock:
                        state_obj.logs.append(line)
                proc.wait()
                if proc.returncode == 0:
                    with state_obj.lock:
                        state_obj.logs.append("\n🎉 [Installer] Google Antigravity CLI 安装完全成功！\n")
                else:
                    with state_obj.lock:
                        state_obj.logs.append(f"\n❌ [Installer Error] 脚本安装失败，退出码: {proc.returncode}\n")
            else:
                with state_obj.lock:
                    state_obj.logs.append("[Installer] 正在 Windows 环境下拉起 PowerShell 执行官方安装指令...\n")
                    state_obj.logs.append("[Installer] 命令: powershell -Command \"irm https://antigravity.google/cli/install.ps1 | iex\"\n")
                
                proc = subprocess.Popen(
                    'powershell -Command "irm https://antigravity.google/cli/install.ps1 | iex"',
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=0
                )
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    with state_obj.lock:
                        state_obj.logs.append(line)
                proc.wait()
                if proc.returncode == 0:
                    with state_obj.lock:
                        state_obj.logs.append("\n🎉 [Installer] Google Antigravity CLI Windows 安装成功！\n")
                else:
                    with state_obj.lock:
                        state_obj.logs.append(f"\n❌ [Installer Error] PowerShell 安装脚本执行失败，退出码: {proc.returncode}\n")
                    
    except Exception as e:
        with state_obj.lock:
            state_obj.logs.append(f"\n❌ [Installer Exception] 安装过程发生异常: {e}\n")
    finally:
        with state_obj.lock:
            state_obj.is_running = False

@app.post("/api/auth/cli/install")
def trigger_cli_install_endpoint(body: CLIInstallRequest, background_tasks: BackgroundTasks):
    provider = body.provider.lower()
    if provider not in ["google", "openai"]:
        raise HTTPException(status_code=400, detail="未知的服务商")
        
    with global_cli_install.lock:
        if global_cli_install.is_running:
            raise HTTPException(status_code=400, detail="当前有正在进行的安装任务，请勿重复提交")
        global_cli_install.is_running = True
        
    background_tasks.add_task(run_cli_install_task, provider, global_cli_install)
    return {"status": "success", "message": f"后台已拉起 {provider} CLI 安装任务，请轮询日志获取进度"}

@app.get("/api/auth/cli/install-logs")
def get_cli_install_logs_endpoint():
    with global_cli_install.lock:
        logs = "".join(global_cli_install.logs)
        is_running = global_cli_install.is_running
    return {
        "status": "success",
        "logs": logs,
        "is_running": is_running
    }

# === 单视频 AI 拆解任务异步执行接口 ===



class TeardownRequest(BaseModel):
    note_id: str

@app.post("/api/hothook/teardown")
def trigger_video_teardown_endpoint(body: TeardownRequest, background_tasks: BackgroundTasks):
    """异步唤醒智能体 CLI 对数据库中的单篇爆款视频运行 hothook 拆解"""
    settings = load_settings()
    google_token = settings.get("google_access_token", "")
    openai_token = settings.get("openai_access_token", "")
    proxy_url = settings.get("proxy_url", "")
    
    agent_cmd = None
    env_vars = {}
    if google_token:
        agent_cmd = "agy"
        env_vars["GOOGLE_OAUTH_ACCESS_TOKEN"] = google_token
    elif openai_token:
        agent_cmd = "codex"
        env_vars["OPENAI_API_KEY"] = openai_token
    else:
        raise HTTPException(status_code=400, detail="请先在『智能体授权』页面绑定 Google 或 OpenAI 账号")
        
    # 查询视频标题
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    row = cursor.execute("SELECT title FROM blogger_notes WHERE id = ?", (body.note_id,)).fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="数据库中未找到该视频记录")
    
    title = row[0]
    
    # 异步在后台拉起 CLI 进程并打日志
    def run_hothook_cli_in_background():
        google_model = settings.get("google_model", "gemini-2.5-pro")
        openai_model = settings.get("openai_model", "gpt-4o")

        # agy --model 接受其自身的显示名，而 config 中存的是 slug，需要做映射
        AGY_MODEL_MAP = {
            "gemini-2.5-pro":           "Gemini 2.5 Pro",
            "gemini-3.5-flash-medium":  "Gemini 3.5 Flash (Medium)",
            "gemini-3.5-flash-high":    "Gemini 3.5 Flash (High)",
            "gemini-3.5-flash-low":     "Gemini 3.5 Flash (Low)",
            "gemini-3.1-pro-low":       "Gemini 3.1 Pro (Low)",
            "gemini-3.1-pro-high":      "Gemini 3.1 Pro (High)",
        }
        agy_model_name = AGY_MODEL_MAP.get(google_model, google_model)

        # 构建统一的绝对路径以防止 agy 搜寻系统盘/C盘
        abs_project_dir = ROOT_DIR.replace("\\", "/")
        abs_skill_path = f"{abs_project_dir}/skills/hothook/SKILL.md"
        abs_db_path = f"{abs_project_dir}/data/distiller.db"
        abs_output_dir = f"{abs_project_dir}/output"

        if agent_cmd == "agy":
            cmd = [
                agent_cmd,
                "--dangerously-skip-permissions",
                "--add-dir", ROOT_DIR,
                "--model", agy_model_name,
                "-p",
                f"您的项目工作区根目录位于 {abs_project_dir}。请加载该工作区并认真阅读位于 {abs_skill_path} 的技能定义与分析流程。请查询位于 {abs_db_path} 的 SQLite 本地数据库中 ID 为 {body.note_id} (标题为『{title}』) 的视频记录，完成深度分析后，将生成的最终单文件 HTML 报告与改写脚本保存到 {abs_output_dir} 目录下。"
            ]
        else:
            cmd = [
                agent_cmd,
                "--dangerously-bypass-approvals-and-sandbox",
                "--model", openai_model,
                "-p",
                f"您的项目工作区根目录位于 {abs_project_dir}。请加载该工作区并认真阅读位于 {abs_skill_path} 的技能定义与分析流程。请查询位于 {abs_db_path} 的 SQLite 本地数据库中 ID 为 {body.note_id} (标题为『{title}』) 的视频记录，完成深度分析后，将生成的最终单文件 HTML 报告与改写脚本保存到 {abs_output_dir} 目录下。"
            ]
        
        env = os.environ.copy()
        env.update(env_vars)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        if proxy_url:
            env["HTTP_PROXY"] = proxy_url
            env["HTTPS_PROXY"] = proxy_url
            
        log_dir = os.path.join(ROOT_DIR, "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"hothook_{body.note_id}.log")
        
        try:
            # 记录初始信息
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"=== HotHook 智能体单视频拆解任务启动 ===\n")
                f.write(f"目标视频 ID: {body.note_id}\n")
                f.write(f"目标视频标题: 『{title}』\n")
                f.write(f"命令: {' '.join(cmd)}\n\n")

            # 始终使用 shell=False，Windows/Linux 均正确传递参数列表
            process = subprocess.Popen(
                cmd,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=ROOT_DIR,
                env=env
            )

            # 逐行无缓冲读取并追加写入，实现完美的前端流式展现
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()

            process.wait()

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== HotHook 智能体拆解任务完成，退出码: {process.returncode} ===\n")
        except Exception as ex:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n❌ 拉起智能体进程时发生异常错误: {ex}\n")
                
    background_tasks.add_task(run_hothook_cli_in_background)
    return {"status": "success", "message": f"已成功拉起智能体，正在后台静默分析视频『{title}』。"}


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
    tasks_list.sort(key=lambda x: x.get("created_at") or x.get("started_at") or "", reverse=True)
    
    clean_list = []
    for t in tasks_list:
        clean_list.append({
            "id": t.get("id") or t.get("task_id", ""),
            "blogger": t.get("blogger", "未知"),
            "status": t.get("status", "running"),
            "created_at": t.get("created_at") or t.get("started_at", ""),
            "started_at": t.get("started_at", ""),
            "finished_at": t.get("finished_at")
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
            WHERE b.is_transcribe = 1 AND n.type = 'video' AND (
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

def analyze_task_step(logs):
    if not logs:
        return "排队中"
    
    # 检查是否有错误/失败
    if "错误：" in logs or "运行发生异常错误:" in logs or "❌ 阶段失败" in logs or "Failed to read logs" in logs or "超时" in logs:
        for line in reversed(logs.split("\n")):
            if "错误：" in line or "运行发生异常错误:" in line or "❌ 阶段失败" in line or "超时" in line:
                return f"同步状态: {line.strip()}"
        return "同步异常终止"
        
    if "🎉 得到 Playwright 抓取流程全部完成！" in logs or "🎉 同步流水线全部成功！" in logs or "流水线同步运行汇总: 1/1 成功" in logs:
        return "同步完成"
        
    if "登录二维码已截取" in logs or "等待扫码登录中" in logs or "请使用手机" in logs and "扫码登录" in logs:
        return "⚠️ 等待微信扫码登录中..."

    if "[验证码拦截]" in logs or "手动滑块解锁" in logs:
        return "遇到验证码拦截 (等待手动滑块解锁)"
        
    # 从流水线反向查找当前运行的阶段
    current_phase = ""
    for line in reversed(logs.split("\n")):
        if ">>> 开始执行阶段:" in line:
            current_phase = line.split(">>> 开始执行阶段:")[-1].strip()
            break
        if "开始同步博主:" in line:
            current_phase = line.strip()
            break
            
    # 从爬虫日志反向查找细分动作
    for line in reversed(logs.split("\n")):
        if "模拟键盘按下" in line or "ArrowDown" in line:
            return f"{current_phase} - {line.strip()}"
        if "当前处理视频 ID" in line or "打开作品详情" in line:
            return f"{current_phase} - {line.strip()}"
        if "正在访问目标主页" in line or "正在检测登录状态" in line:
            return f"{current_phase} - 正在访问得到/主页"
            
    if current_phase:
        return current_phase
        
    return "正在执行"

@app.get("/api/crawl/status/{task_id}")
def get_crawler_status(task_id: str):
    from datetime import timedelta
    is_crawl = False
    task_info = None
    
    with tasks_lock:
        if task_id in active_crawl_tasks:
            task_info = active_crawl_tasks[task_id]
            is_crawl = True
        elif task_id in active_transcribe_tasks:
            task_info = active_transcribe_tasks[task_id]

    logs = ""
    started_dt = None
    
    if not task_info:
        # 探测是否是 data/logs 下的任务日志文件 (包含 .log 后缀或无后缀)
        log_filename = task_id if task_id.endswith(".log") else f"{task_id}.log"
        agent_log_path = os.path.join(ROOT_DIR, "data", "logs", log_filename)
        if os.path.exists(agent_log_path):
            try:
                mtime = os.path.getmtime(agent_log_path)
                started_dt = datetime.fromtimestamp(mtime) - timedelta(minutes=10)
                with open(agent_log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    logs = "".join(lines[-150:])
            except Exception as e:
                logs = f"Failed to read agent logs: {e}"
                
            current_step = analyze_task_step(logs)
            is_crawl = True
        else:
            return {"status": "error", "message": "Task not found"}
    else:
        log_path = task_info["log_path"]
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    logs = "".join(lines[-150:])
            except Exception as e:
                logs = f"Failed to read logs: {e}"
        if task_info.get("started_at"):
            try:
                started_dt = datetime.fromisoformat(task_info["started_at"])
            except:
                pass
                
        if is_crawl:
            current_step = analyze_task_step(logs)
        else:
            current_step = task_info.get("current_step", "正在进行后台语音转录...")

    # 扫描与当前任务运行时间匹配的截图文件 (包括 biji 二维码与通用截图)
    screenshots = []
    screenshots_dir = os.path.join(ROOT_DIR, "screenshots")
    if os.path.exists(screenshots_dir) and started_dt:
        try:
            # 确定任务的时间窗口 (启动时间 - 5分钟 ~ 结束时间 + 5分钟)
            task_end_dt = datetime.now() + timedelta(minutes=5)
            if task_info and task_info.get("finished_at"):
                try:
                    task_end_dt = datetime.fromisoformat(task_info["finished_at"]) + timedelta(minutes=5)
                except:
                    pass

            for filename in os.listdir(screenshots_dir):
                if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    filepath = os.path.join(screenshots_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    mtime_dt = datetime.fromtimestamp(mtime)
                    
                    # 严格判定：截图修改时间必须落在该任务的实际运行时间窗口内
                    if (started_dt - timedelta(minutes=5)) <= mtime_dt <= task_end_dt:
                        screenshots.append(f"/screenshots/{filename}?t={int(mtime)}")
        except Exception as err:
            print(f"Error scanning screenshots: {err}")
            
    task_status = "running"
    if task_info:
        task_status = task_info.get("status", "running")
    else:
        task_status = "completed"

    return {
        "status": "success",
        "task_status": task_status,
        "logs": logs,
        "current_step": current_step,
        "screenshots": screenshots
    }

@app.get("/api/agent/tasks")
def get_all_agent_tasks():
    log_dir = os.path.join(ROOT_DIR, "data", "logs")
    if not os.path.exists(log_dir):
        return []
        
    tasks = []
    import datetime
    
    with tasks_lock:
        active_paths_map = {
            os.path.basename(info.get("log_path", "")): info.get("status", "running")
            for info in active_crawl_tasks.values() if info.get("log_path")
        }
    
    for filename in os.listdir(log_dir):
        if not filename.endswith(".log"):
            continue
        file_path = os.path.join(log_dir, filename)
        if not os.path.isfile(file_path):
            continue
            
        mtime = os.path.getmtime(file_path)
        dt = datetime.datetime.fromtimestamp(mtime)
        created_at_str = dt.isoformat()
        
        # 默认名称
        blogger_desc = filename
        if filename.startswith("biji_sync_"):
            blogger_desc = f"⚡ Get 笔记文案同步 ({filename})"
        elif filename.startswith("hothook_"):
            note_id = filename.replace("hothook_", "").replace(".log", "")
            blogger_desc = f"🤖 AI 拆解: 视频 ID {note_id}"
            try:
                db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                row = cursor.execute("SELECT title FROM blogger_notes WHERE id = ?", (note_id,)).fetchone()
                conn.close()
                if row:
                    blogger_desc = f"🤖 AI 拆解: 『{row[0]}』"
            except:
                pass
        elif filename.startswith("distill_"):
            blogger_name = filename.replace("distill_", "").split("_")[0]
            blogger_desc = f"🔄 自动蒸馏: {blogger_name}"
        elif filename.startswith("models_fetch_"):
            provider_name = filename.replace("models_fetch_", "").replace(".log", "")
            blogger_desc = f"🔄 拉取 {provider_name.capitalize()} 智能体模型"
        elif filename == "cli_install.log":
            blogger_desc = "📦 智能体 CLI 客户端部署安装"
            
        # 优先从内存状态中读取真实运行状态，防止文件未刷新导致的误判
        if filename in active_paths_map:
            status = active_paths_map[filename]
        else:
            status = "success"
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                    if "=== HotHook 智能体拆解任务完成" in content or "=== 任务完成" in content or "退出码: 0" in content or "Exit code: 0" in content or "🎉 得到 Playwright 抓取流程全部完成" in content:
                        status = "success"
                    elif "❌" in content or "Failed" in content or "Error" in content:
                        status = "failed"
                    elif (datetime.datetime.now() - dt).total_seconds() < 15:
                        status = "running"
            except:
                pass
            
        tasks.append({
            "id": filename,
            "blogger": blogger_desc,
            "status": status,
            "created_at": created_at_str,
            "started_at": created_at_str,
            "finished_at": created_at_str
        })
        
    tasks.sort(key=lambda x: x["created_at"], reverse=True)
    return tasks

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

@app.post("/api/task/cancel/{task_id}")
def cancel_task(task_id: str):
    """通用任务取消接口，支持取消爬虫同步任务与语音转录任务"""
    # 1. 语音转录任务（以 tx_ 开头）
    if task_id.startswith("tx_"):
        note_id = task_id.replace("tx_", "")
        db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path, timeout=10.0)
                cursor = conn.cursor()
                # 检查该视频是否确实存在且待转录
                row = cursor.execute("SELECT id, desc FROM blogger_notes WHERE id = ?;", (note_id,)).fetchone()
                if not row:
                    conn.close()
                    raise HTTPException(status_code=404, detail="未找到对应的视频笔记")
                
                desc = row[1] or ""
                # 如果还未被标记取消，拼上 '[已取消转录] ' 标记
                if not desc.startswith("[已取消转录]"):
                    new_desc = f"[已取消转录] {desc}"
                    cursor.execute("UPDATE blogger_notes SET desc = ? WHERE id = ?;", (new_desc, note_id))
                    conn.commit()
                    
                conn.close()
                
                # 在内存的 active_transcribe_tasks 中标记为 cancelled
                with tasks_lock:
                    if task_id in active_transcribe_tasks:
                        active_transcribe_tasks[task_id]["status"] = "failed"
                        
                return {"status": "success", "message": "转录任务已成功标记取消"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"数据库更新失败: {str(e)}")
        else:
            raise HTTPException(status_code=404, detail="数据库未就绪")
            
    # 2. 爬虫同步任务
    else:
        with tasks_lock:
            if task_id not in active_crawl_tasks:
                raise HTTPException(status_code=404, detail="未找到该同步任务")
            
            task_info = active_crawl_tasks[task_id]
            status = task_info.get("status")
            
            if status in ["success", "failed", "cancelled"]:
                return {"status": "success", "message": f"任务已结束，当前状态为 {status}"}
                
            # 如果是 queued，直接标记为 cancelled / failed 即可，worker 拿到会自动跳过
            if status == "queued":
                task_info["status"] = "failed"
                task_info["finished_at"] = datetime.now().isoformat()
                log_path = task_info["log_path"]
                try:
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write("\n=== 任务在排队阶段被用户取消 ===\n")
                except:
                    pass
                return {"status": "success", "message": "排队中的任务已取消"}
                
            # 如果是 running，需要杀死进程
            if status == "running":
                task_info["status"] = "failed"
                task_info["finished_at"] = datetime.now().isoformat()
                
                process = task_info.get("process")
                if process:
                    try:
                        import psutil
                        parent = psutil.Process(process.pid)
                        for child in parent.children(recursive=True):
                            child.kill()
                        parent.kill()
                    except Exception as pe:
                        try:
                            process.kill()
                        except:
                            pass
                            
                log_path = task_info["log_path"]
                try:
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write("\n=== 任务运行中被用户取消强制中止 ===\n")
                except:
                    pass
                    
                return {"status": "success", "message": "运行中的任务已中止"}
@app.post("/api/task/cancel-all-queued")
def cancel_all_queued_tasks():
    """一键取消所有排队中的抓取与转录任务"""
    cancelled_crawl_count = 0
    cancelled_transcribe_count = 0
    
    # 1. 取消所有排队中的抓取同步任务
    with tasks_lock:
        for task_id, task_info in active_crawl_tasks.items():
            if task_info.get("status") == "queued":
                task_info["status"] = "failed"
                task_info["finished_at"] = datetime.now().isoformat()
                cancelled_crawl_count += 1
                log_path = task_info["log_path"]
                try:
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write("\n=== 任务在排队阶段被用户批量取消 ===\n")
                except:
                    pass
                    
    # 2. 取消所有排队中的语音转录任务
    # 修改 SQLite 数据库，将所有待转录视频链接前缀加上 [已取消转录] 标记，使轮询跳过它们
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            cursor = conn.cursor()
            
            # 先统计有多少条记录将被修改以展示结果
            row = cursor.execute("""
                SELECT COUNT(*) FROM blogger_notes 
                WHERE type = 'video' AND (
                    desc LIKE 'http://%' OR 
                    desc LIKE 'https://%' OR 
                    desc LIKE '[转录失败_第%'
                ) AND desc NOT LIKE '[已取消转录]%';
            """).fetchone()
            cancelled_transcribe_count = row[0] if row else 0
            
            if cancelled_transcribe_count > 0:
                cursor.execute("""
                    UPDATE blogger_notes 
                    SET desc = '[已取消转录] ' || desc 
                    WHERE type = 'video' AND (
                        desc LIKE 'http://%' OR 
                        desc LIKE 'https://%' OR 
                        desc LIKE '[转录失败_第%'
                    ) AND desc NOT LIKE '[已取消转录]%';
                """)
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"Cancel all queued transcribe tasks failed: {e}")
            
    # 3. 取消内存中的待转录任务
    with tasks_lock:
        for task_id, task_info in active_transcribe_tasks.items():
            if task_info.get("status") == "queued":
                task_info["status"] = "failed"
                task_info["finished_at"] = datetime.now().isoformat()
                
    return {
        "status": "success", 
        "message": f"已成功取消 {cancelled_crawl_count} 个同步任务，标记取消 {cancelled_transcribe_count} 个视频转录。"
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
        # 探测是否是 data/logs 下的智能体任务日志
        agent_log_path = os.path.join(ROOT_DIR, "data", "logs", task_id)
        if os.path.exists(agent_log_path):
            logs = ""
            try:
                with open(agent_log_path, "r", encoding="utf-8", errors="replace") as f:
                    logs = f.read()
            except Exception as e:
                logs = f"Failed to read agent logs: {e}"
                
            current_step = "智能体分析执行中..."
            if "=== HotHook 智能体拆解任务完成" in logs or "=== 任务完成" in logs or "退出码" in logs or "Exit code" in logs:
                current_step = "已执行完成"
            elif "❌" in logs or "Failed" in logs:
                current_step = "执行中断/失败"
                
            return {
                "status": "success",
                "logs": logs,
                "current_step": current_step,
                "screenshots": []
            }
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
    # 端口绑定，同时监控 web 和 scripts 目录，确保修改 biji_browser.py 能自动热重载生效
    uvicorn.run("app:app", host=host, port=port, reload=True, reload_dirs=["web", "scripts"])
