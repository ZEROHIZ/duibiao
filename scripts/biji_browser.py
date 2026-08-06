"""
Get 笔记 (biji.com) Playwright 无头浏览器与网络拦截引擎
核心职责：
1. 维护多账号 Playwright 隔离上下文 (data/browser_context/{account_id}/)，完美适配 Docker 运行。
2. 自动化登录处理：自动补勾协议按钮 (#login-agree)，截取二维码 (screenshots/biji_qr_{account_id}.png) 并监听 .nickname-row。
3. 网络请求拦截：自动捕获 1.json (知识库)、2.json (博主列表)、3.json (作品列表)、4.json (转录全文)。
4. 增量去重与触底断链：对比 post_id_str 与 post_update_time，自动跳过无更新作品。
5. 自动调用 biji_backfill 将转录稿回传至主作品表 (blogger_notes)。
"""

import os
import sys
import json
import time
import re
import argparse
import sqlite3
import threading
from pathlib import Path

# 尝试导入 playwright
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    print("❌ 缺少 playwright 依赖，请运行 pip install playwright")
    sys.exit(1)

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from biji_migrator import migrate_database
from biji_backfill import backfill_transcripts, extract_video_id_from_url

# 常量定义
BASE_URL = "https://www.biji.com/subject"
SCREENSHOT_DIR = os.path.join(SKILL_ROOT, "screenshots")
DATA_DIR = os.path.join(SKILL_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "distiller.db")


class BijiBrowserEngine:
    """得到 Playwright 浏览器自动化与接口拦截管理类"""

    def __init__(self, account_id="account_01", headless=None, log_func=None):
        self.account_id = account_id
        self.log_func = log_func
        self.context_dir = os.path.join(DATA_DIR, "browser_context", account_id)
        os.makedirs(self.context_dir, exist_ok=True)
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        # 自动强力擦除可能存在的 Chromium 单例锁文件，防止浏览器上下文死锁报错
        for root, dirs, files in os.walk(self.context_dir):
            for name in files + dirs:
                if name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
                    full_path = os.path.join(root, name)
                    try:
                        if os.path.islink(full_path) or os.path.exists(full_path):
                            os.remove(full_path)
                    except:
                        pass

        # 若未指定 headless，优先从 config.json 读取配置（默认无头）
        if headless is None:
            headless = True
            config_path = os.path.join(DATA_DIR, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        val = cfg.get("headless")
                        if val is None:
                            val = cfg.get("headless_browser")
                        if val is not None:
                            if isinstance(val, bool):
                                headless = val
                            elif isinstance(val, str):
                                headless = (val.lower() == "true")
                except:
                    pass
        self.headless = headless

        # 缓存拦截到的数据
        self.topics_data = []
        self.follows_data = []
        self.captured_posts = {}
        self.captured_details = {}

    def log(self, msg):
        """格式化打印并同时推送给后台日志文件"""
        print(msg)
        if self.log_func:
            try:
                self.log_func(msg)
            except:
                pass

    def capture_error_screenshot(self, page, step_name):
        """保存关键步骤点击失败/异常截图，并打印显式错误信息供 Web 看板展现"""
        timestamp = int(time.time())
        screenshot_name = f"biji_error_{self.account_id}_{timestamp}.png"
        screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_name)
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            self.log(f"\n==================================================")
            self.log(f"❌ [关键步骤异常终止] 在步骤『{step_name}』中操作失败！")
            self.log(f"📸 故障现场截图已保存至：")
            self.log(f"   {screenshot_path}")
            self.log(f"==================================================\n")
        except Exception as e:
            self.log(f"❌ 保存异常截图失败: {e}")
        return screenshot_path

    def update_account_status(self, nickname="", user_id="", status="LOGGED_IN"):
        """更新数据库中的账号状态记录"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO biji_browser_accounts (account_id, nickname, user_id, status, last_login_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                nickname = excluded.nickname,
                user_id = excluded.user_id,
                status = excluded.status,
                last_login_at = CURRENT_TIMESTAMP
        """, (self.account_id, nickname, str(user_id), status))
        conn.commit()
        conn.close()

    def is_post_already_updated(self, post_id_str, post_update_time):
        """检查作品是否在本地已存在且无需更新"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT post_update_time FROM biji_transcripts WHERE post_id_str = ?",
            (str(post_id_str),)
        ).fetchone()
        conn.close()

        if row:
            local_update_time = row[0] or 0
            if local_update_time >= (post_update_time or 0):
                return True  # 已是最新
        return False

    def save_transcript_to_db(self, detail_data, blogger_name=""):
        """保存详情 JSON (4.json) 到 biji_transcripts 独立表"""
        c = detail_data.get("c", {})
        post_id_str = str(c.get("post_id_str") or c.get("post_id") or "")
        if not post_id_str:
            return

        post_name = c.get("post_name") or c.get("post_title") or ""
        post_media_text = c.get("post_media_text") or ""
        post_summary = c.get("post_summary") or ""
        post_cleaned_summary = c.get("post_cleaned_summary") or ""
        original_video_url = c.get("post_url") or ""
        original_video_id = extract_video_id_from_url(original_video_url)
        post_update_time = c.get("post_update_time") or 0

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO biji_transcripts (
                post_id_str, account_id, blogger_name, post_name, post_media_text,
                post_summary, post_cleaned_summary, original_video_url,
                original_video_id, post_update_time, synced_to_main
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(post_id_str) DO UPDATE SET
                post_media_text = excluded.post_media_text,
                post_summary = excluded.post_summary,
                post_cleaned_summary = excluded.post_cleaned_summary,
                original_video_url = excluded.original_video_url,
                original_video_id = excluded.original_video_id,
                post_update_time = excluded.post_update_time,
                synced_to_main = 0
        """, (
            post_id_str, self.account_id, blogger_name, post_name, post_media_text,
            post_summary, post_cleaned_summary, original_video_url,
            original_video_id, post_update_time
        ))
        conn.commit()

    def get_pending_target_bloggers(self):
        """获取本地主库中需要在得到同步文案的对标博主列表"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 优先选择包含待转录视频链接的博主
        rows = cursor.execute("""
            SELECT DISTINCT b.id, b.name, b.home_url, b.biji_url, b.biji_follow_id, b.biji_topic_alias
            FROM bloggers b
            JOIN blogger_notes n ON n.blogger_id = b.id
            WHERE n.type = 'video' AND (
                n.desc LIKE 'http://%' OR 
                n.desc LIKE 'https://%' OR 
                n.desc LIKE '[转录失败%'
            )
        """).fetchall()

        # 若无具体待转录视频，则获取主表中录入的所有对标博主
        if not rows:
            rows = cursor.execute("""
                SELECT id, name, home_url, biji_url, biji_follow_id, biji_topic_alias
                FROM bloggers
            """).fetchall()

        conn.close()
        return [dict(r) for r in rows]

    def get_pending_note_ids(self, blogger_id):
        """获取指定博主在主库中等待转录的视频数字 ID (aweme_id) 列表"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        rows = cursor.execute("""
            SELECT id, title FROM blogger_notes
            WHERE blogger_id = ? AND type = 'video' AND (
                desc LIKE 'http://%' OR 
                desc LIKE 'https://%' OR 
                desc LIKE '[转录失败%'
            )
        """, (blogger_id,)).fetchall()
        conn.close()
        return {str(r["id"]): r["title"] for r in rows}

    def create_biji_topic(self, topic_name):
        """自动在得到创建知识库并返回新生成的 topic_alias"""
        self.log(f"\n🚀 [得到建库] 正在为账号 '{self.account_id}' 创建知识库: 『{topic_name}』...")
        created_topic_info = {}

        with sync_playwright() as p:
            self.log(f"🌐 [引擎启动] 正在为账号 '{self.account_id}' 启动 Chromium (Headless={self.headless})...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.context_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = context.new_page()

            def handle_response(response):
                try:
                    if "v1/web/topic" in response.url and response.status == 200:
                        data = response.json()
                        c = data.get("c") or {}
                        if isinstance(c, dict) and (c.get("id_alias") or c.get("topic_id")):
                            created_topic_info["alias"] = c.get("id_alias") or c.get("alias")
                            created_topic_info["id"] = c.get("topic_id") or c.get("id")
                            created_topic_info["name"] = c.get("topic_name") or c.get("name") or topic_name
                            self.log(f"📡 [网络拦截] 成功拦截 1.json 创建建库响应! TopicAlias: {created_topic_info['alias']}")
                except:
                    pass

            page.on("response", handle_response)

            try:
                self.log("🌐 正在打开得到知识库总览主页: https://www.biji.com/subject...")
                page.goto("https://www.biji.com/subject", wait_until="domcontentloaded")
                time.sleep(2)

                self.log("🔍 正在查找『创建知识库』新建卡片按钮...")
                create_btn = page.locator("div[class*='create-item'], .create-item").first
                try:
                    create_btn.wait_for(state="visible", timeout=8000)
                    self.log("👆 找到创建卡片按钮，正在点击...")
                    create_btn.click()
                except Exception as ex:
                    self.log(f"  [提示] 使用 文本选择器 尝试点击创建按钮 ({ex})...")
                    page.locator("text=创建知识库").first.click()

                self.log("📱 等待新建知识库弹窗对话框 (.modal-content) 展现...")
                page.wait_for_selector(".modal-content", timeout=8000)
                
                self.log(f"✍️ 在弹窗输入框中填入知识库名称: 『{topic_name}』...")
                name_input = page.locator(".modal-content input.n-input__input-el, .modal-content input").first
                name_input.click()
                name_input.fill(topic_name)
                try:
                    page.evaluate("(el) => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }", name_input.element_handle())
                except:
                    pass
                time.sleep(0.8)

                self.log("🔍 正在定位弹窗『确定』主按钮 (.action-buttons .n-button--primary-type)...")
                confirm_btn = page.locator(".modal-content .action-buttons button.n-button--primary-type, .modal-content button.n-button--primary-type, .modal-content button:has-text('确定')").first
                confirm_btn.wait_for(state="visible", timeout=6000)
                self.log("👆 点击『确定』主按钮...")
                confirm_btn.click()

                self.log("⏳ 等待 3 秒进行建库 API (1.json) 响应...")
                time.sleep(3)
            except Exception as err:
                self.log(f"❌ [得到建库过程抛出异常]: {err}")
            finally:
                context.close()

        new_topic_alias = created_topic_info.get("alias")
        print(f"✅ [得到建库成功] 知识库名称: 『{topic_name}』 | Alias: {new_topic_alias}")
        return created_topic_info

    def add_blogger_to_biji(self, topic_alias, home_url, blogger_name=""):
        """自动在得到知识库添加/订阅博主，并带有二次刷页重寻自愈"""
        self.log(f"\n🚀 [得到关注] 正在知识库 '{topic_alias}' 添加博主: 『{blogger_name or home_url}』...")
        topic_url = f"https://www.biji.com/subject/{topic_alias}/DEFAULT"
        biji_url = None
        follow_id = None

        with sync_playwright() as p:
            self.log(f"🌐 [引擎启动] 正在为账号 '{self.account_id}' 启动 Chromium (Headless={self.headless})...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.context_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = context.new_page()
            
            captured_follow_info = {}
            captured_follow_map = {}  # key: home_url or fname, value: (fname, fid, item_url)

            def is_valid_name(name_str):
                if not name_str:
                    return False
                s = str(name_str).strip()
                if "GET笔记" in s or "正在帮你订阅" in s or s.startswith("待爬取") or s == "待订阅" or s == "订阅中":
                    return False
                return True

            def normalize_url(u):
                if not u:
                    return ""
                return u.split("?")[0].rstrip("/").lower()

            def handle_response(response):
                try:
                    if "v1/web/follow" in response.url and response.status == 200:
                        data = response.json()
                        c = data.get("c") or {}
                        
                        items_to_process = []
                        if isinstance(c, list):
                            items_to_process = c
                        elif isinstance(c, dict):
                            if "list" in c and isinstance(c["list"], list):
                                items_to_process = c["list"]
                            else:
                                items_to_process = [c]

                        for item in items_to_process:
                            fid = item.get("follow_id") or item.get("id")
                            fname = item.get("follow_name") or item.get("name") or ""
                            item_url = item.get("url") or item.get("home_url") or ""
                            if fid and is_valid_name(fname):
                                captured_follow_info[fname] = fid
                                captured_follow_map[fname] = (fname, fid, item_url)
                                norm_u = normalize_url(item_url)
                                if norm_u:
                                    captured_follow_map[norm_u] = (fname, fid, item_url)
                                self.log(f"📡 [网络拦截] 拦截到 2.json 有效博主: '{fname}' (ID: {fid}, URL: {item_url})")
                            elif fid and fname:
                                self.log(f"  [提示] 忽略过渡态/占位名称: '{fname}' (ID: {fid})")
                except:
                    pass

            page.on("response", handle_response)

            try:
                self.log(f"🌐 打开目标知识库主页: {topic_url}...")
                page.goto(topic_url, wait_until="domcontentloaded")
                time.sleep(2)

                # 步骤 1: 点击 "添加"
                self.log("🔍 正在查找页面『添加』按钮...")
                try:
                    add_btn = page.locator("xpath=//*[contains(text(), '添加')]").first
                    add_btn.wait_for(state="visible", timeout=8000)
                    self.log("👆 点击『添加』按钮...")
                    add_btn.click()
                    time.sleep(1)
                except Exception as ex:
                    self.capture_error_screenshot(page, "点击『添加』按钮")
                    raise RuntimeError(f"无法定位或点击页面『添加』按钮: {ex}")

                # 步骤 2: 点击 "订阅直播/博主"
                self.log("🔍 正在查找『订阅直播/博主』菜单选项...")
                try:
                    sub_btn = page.locator("div[role='menuitem']:has-text('订阅直播/博主'), [role='menuitem']:has-text('订阅直播/博主')").first
                    sub_btn.wait_for(state="visible", timeout=8000)
                    self.log("👆 点击『订阅直播/博主』...")
                    sub_btn.click()
                    time.sleep(1)
                except Exception as ex:
                    self.capture_error_screenshot(page, "点击『订阅直播/博主』菜单")
                    raise RuntimeError(f"无法定位或点击『订阅直播/博主』菜单: {ex}")

                # 步骤 3: 点击 "抖音博主"
                self.log("🔍 正在定位『抖音博主』Tab 页签 (data-name='douyin')...")
                try:
                    tab_btn = page.locator(".n-tabs-tab[data-name='douyin'], div[data-name='douyin'], .n-tabs-tab:has-text('抖音博主')").first
                    tab_btn.wait_for(state="visible", timeout=8000)
                    self.log("👆 点击『抖音博主』Tab...")
                    tab_btn.click()
                except Exception as ex:
                    self.capture_error_screenshot(page, "点击『抖音博主』Tab")
                    raise RuntimeError(f"无法定位或点击『抖音博主』Tab: {ex}")
                
                # 显式等待 Tab 激活动画完成
                self.log("⏳ 等待 Tab 激活状态变更 (data-name='douyin')...")
                try:
                    page.wait_for_selector(".n-tabs-tab--active[data-name='douyin'], .n-tabs-tab--active:has-text('抖音博主')", timeout=5000)
                except:
                    pass
                time.sleep(1)

                # 步骤 4: 填入博主 URL
                self.log(f"✍️ 在输入框填入抖音博主主页 URL: {home_url}...")
                try:
                    url_input = page.locator(".n-tab-pane:not([style*='display: none']) input.n-input__input-el, .n-input__input-el").first
                    url_input.click()
                    url_input.fill(home_url)
                    try:
                        page.evaluate("(el) => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }", url_input.element_handle())
                    except:
                        pass
                    time.sleep(0.5)
                except Exception as ex:
                    self.capture_error_screenshot(page, "填入博主 URL 输入框")
                    raise RuntimeError(f"无法填入博主 URL 输入框: {ex}")

                # 步骤 5: 点击 "确定"
                self.log("👆 点击『确定』提交按钮...")
                try:
                    confirm_btn = page.locator(".n-tab-pane:not([style*='display: none']) button.n-button--primary-type, button.n-button--primary-type, button:has-text('确定')").first
                    confirm_btn.click()
                except Exception as ex:
                    self.capture_error_screenshot(page, "点击『确定』提交按钮")
                    raise RuntimeError(f"无法定位或点击『确定』提交按钮: {ex}")

                # 显式轮询等待 2.json 网络响应拦截回调 (最长 10 秒)
                self.log("⏳ 正在等待 2.json 关注响应回调 (最长 10 秒)...")
                for _ in range(20):
                    if captured_follow_map or captured_follow_info:
                        break
                    time.sleep(0.5)

                # 提取拿到的有效 follow_id 与真实博主名称 (优先通过 home_url 精准比对)
                # 显式轮询等待 2.json 网络响应拦截回调 (最长 10 秒)
                self.log("⏳ 正在等待 2.json 关注响应回调 (最长 10 秒)...")
                for _ in range(20):
                    if captured_follow_map or captured_follow_info:
                        break
                    time.sleep(0.5)

                def match_captured_blogger(target_u, target_n):
                    norm_h = normalize_url(target_u)
                    clean_h_key = norm_h.split("/")[-1] if norm_h else ""
                    
                    for k, val in captured_follow_map.items():
                        rname, fid, item_u = val
                        if not is_valid_name(rname):
                            continue
                        norm_item = normalize_url(item_u)
                        clean_item_key = norm_item.split("/")[-1] if norm_item else ""
                        
                        # 1. URL 精确匹配
                        if norm_h and norm_h == norm_item:
                            return rname, fid, "URL 精确匹配"
                        # 2. Key 包含匹配 (防止长短链或 query 参数差异)
                        if clean_h_key and len(clean_h_key) > 5 and clean_h_key in norm_item:
                            return rname, fid, "URL Key 匹配"
                        if clean_item_key and len(clean_item_key) > 5 and clean_item_key in norm_h:
                            return rname, fid, "URL Key 匹配"
                        # 3. 博主昵称匹配 (非待爬取占位符)
                        if target_n and not target_n.startswith("待爬取") and rname:
                            if target_n == rname:
                                return rname, fid, "昵称精确匹配"
                            elif (target_n in rname or rname in target_n) and len(target_n) >= 2:
                                return rname, fid, "昵称包含匹配"
                    return None, None, ""

                real_blogger_name, target_fid, match_reason = match_captured_blogger(home_url, blogger_name)
                
                if target_fid and real_blogger_name:
                    import urllib.parse
                    encoded_name = urllib.parse.quote(real_blogger_name)
                    biji_url = f"https://www.biji.com/subject/{topic_alias}/DEFAULT?followId={target_fid}&followName={encoded_name}"
                    follow_id = target_fid
                    self.log(f"✅ [得到关注成功] ({match_reason}) 匹配到 URL: {biji_url} | 博主名: {real_blogger_name}")
                else:
                    self.log(f"⚠️ [尝试自愈重寻] 未能在关注响应中精准匹配目标博主，刷新知识库列表...")
                    page.goto(topic_url, wait_until="domcontentloaded")
                    time.sleep(1.5)

                    # 自愈刷新后主动点击"博主" Tab，触发 2.json (v1/web/follow) 响应
                    self.log("🔍 [自愈] 正在查找并点击『博主』Tab 以触发 2.json...")
                    try:
                        with page.expect_response(
                            lambda r: "v1/web/follow" in r.url and r.status == 200,
                            timeout=8000
                        ):
                            blogger_tab = page.locator(
                                "xpath=//*[contains(text(),'博主') and not(contains(text(),'订阅'))]"
                            ).first
                            if blogger_tab.is_visible(timeout=4000):
                                self.log("👆 [自愈] 点击『博主』Tab...")
                                blogger_tab.click()
                            else:
                                self.log("  [自愈] 未找到『博主』Tab，等待网络自动触发...")
                        time.sleep(1.5)
                    except Exception as tab_ex:
                        self.log(f"  [自愈] 点击博主 Tab 提示: {tab_ex}...")
                        time.sleep(2)

                    # 再次精准比对
                    real_blogger_name, target_fid, match_reason = match_captured_blogger(home_url, blogger_name)
                    
                    if target_fid and real_blogger_name:
                        import urllib.parse
                        encoded_name = urllib.parse.quote(real_blogger_name)
                        biji_url = f"https://www.biji.com/subject/{topic_alias}/DEFAULT?followId={target_fid}&followName={encoded_name}"
                        follow_id = target_fid
                        self.log(f"✅ [刷新自愈成功] ({match_reason}) 提取 biji_url: {biji_url}")
                    else:
                        self.log("ℹ️ 得到的关注节点可能尚处于后台异步处理中，本次跳过回写（防止误绑定其他博主）。")
            except Exception as err:
                self.log(f"⚠️ [关注流程提示/异常]: {err}")
            finally:
                context.close()

        if biji_url:
            self.save_biji_url_to_db(blogger_name, topic_alias, follow_id, biji_url, home_url, real_name=real_blogger_name)

        return biji_url

    def save_biji_url_to_db(self, blogger_name, topic_alias, follow_id, biji_url, home_url="", real_name=""):
        """将自动关注或自愈抓取到的 biji_url, biji_follow_id, biji_topic_alias 写入 SQLite，精准按 home_url 或 name 比对更新"""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            target_id = None
            if home_url:
                cursor.execute("SELECT id FROM bloggers WHERE home_url = ?;", (home_url,))
                r = cursor.fetchone()
                if r:
                    target_id = r[0]
            if not target_id and blogger_name and not blogger_name.startswith("待爬取"):
                cursor.execute("SELECT id FROM bloggers WHERE name = ?;", (blogger_name,))
                r = cursor.fetchone()
                if r:
                    target_id = r[0]
            if not target_id:
                cursor.execute("SELECT id FROM bloggers WHERE biji_topic_alias = ? AND biji_url IS NULL LIMIT 1;", (topic_alias,))
                r = cursor.fetchone()
                if r:
                    target_id = r[0]

            if target_id:
                cursor.execute("""
                    UPDATE bloggers 
                    SET biji_url = ?, biji_follow_id = ?, biji_topic_alias = ?
                    WHERE id = ?;
                """, (biji_url, str(follow_id or ""), topic_alias, target_id))
                
                # 仅当名称为“待爬取”时才更新为 real_name
                if real_name and not real_name.startswith("待爬取"):
                    cursor.execute("SELECT name FROM bloggers WHERE id = ?;", (target_id,))
                    curr_r = cursor.fetchone()
                    if curr_r and (not curr_r[0] or curr_r[0].startswith("待爬取")):
                        cursor.execute("UPDATE bloggers SET name = ? WHERE id = ?;", (real_name, target_id))
                        
                conn.commit()
                conn.close()
                self.log(f"💾 [数据库回写成功] 已精准将 biji_url ({biji_url}) 绑定至博主 (ID: {target_id})！")
            else:
                conn.close()
                self.log(f"⚠️ [数据库回写跳过] 未在数据库中定位到唯一的博主记录 (home_url={home_url}, name={blogger_name})")
        except Exception as e:
            self.log(f"❌ [数据库回写失败]: {e}")

    def run_sync(self, max_posts_per_blogger=20):
        """执行完整得到数据同步闭环"""
        migrate_database(DB_PATH)

        print(f"\n🚀 启动 Get 笔记 (biji.com) 自动化引擎...")
        print(f"   账号 ID: {self.account_id}")
        print(f"   无头模式: {self.headless}")
        print(f"   存储路径: {self.context_dir}\n")

        with sync_playwright() as p:
            # 使用持久化 BrowserContext 维持 Session
            context = p.chromium.launch_persistent_context(
                user_data_dir=self.context_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = context.new_page()

            # ----------------------------------------------------
            # 监听网络 API 响应
            # ----------------------------------------------------
            def handle_response(response):
                try:
                    url = response.url
                    # 1. 知识库列表 (1.json)
                    if "v1/web/topic/mine/list" in url and response.status == 200:
                        data = response.json()
                        if data.get("h", {}).get("c") == 0:
                            self.topics_data = data.get("c", [])
                            print(f"  [biji-api] 📚 捕获到 {len(self.topics_data)} 个知识库")

                    # 2. 关注博主列表 (2.json)
                    elif "v1/web/follow/list" in url and response.status == 200:
                        data = response.json()
                        if data.get("h", {}).get("c") == 0:
                            self.follows_data = data.get("c", {}).get("list", [])
                            print(f"\n  [biji-api] 👤 成功捕获到 {len(self.follows_data)} 个关注博主明细:")
                            for idx, f in enumerate(self.follows_data, start=1):
                                f_name = f.get("name") or "未命名"
                                f_id = f.get("id") or ""
                                f_url = f.get("url") or "(无原主页链接)"
                                print(f"     └─ {idx}. 博主姓名: 『{f_name}』 | 得到 ID: {f_id} | 原平台主页: {f_url}")

                    # 3. 博主作品列表 (3.json)
                    elif "v1/web/follow/account/posts" in url and response.status == 200:
                        data = response.json()
                        if data.get("h", {}).get("c") == 0:
                            posts = data.get("c", {}).get("posts", [])
                            self.captured_posts[url] = posts
                            print(f"  [biji-api] 📝 捕获作品列表 ({len(posts)} 条)")

                    # 4. 作品转录详情 (4.json)
                    elif "v1/web/topic/post/detail" in url and response.status == 200:
                        data = response.json()
                        if data.get("h", {}).get("c") == 0:
                            c = data.get("c", {})
                            p_id = str(c.get("post_id_str") or c.get("post_id") or "")
                            if p_id:
                                self.captured_details[p_id] = data
                                print(f"  [biji-api] 📄 捕获作品转录详情 (ID: {p_id})")
                except Exception:
                    pass

            page.on("response", handle_response)

            # ----------------------------------------------------
            # Step 1: 登录校验与重定向检查
            # ----------------------------------------------------
            print(f"[biji-auth] 正在检测登录状态 ({BASE_URL})...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            current_url = page.url
            if current_url.rstrip("/") == "https://www.biji.com":
                print("\n⚠️  检测到未登录状态 (已被重定向至 biji.com)")
                # 多重高兼容选择器定位 "注册/登录" 按钮并触发点击
                login_selectors = [
                    "xpath=//*[contains(text(),'注册/登录') or contains(text(),'登录/注册') or contains(text(),'登录') or contains(text(),'注册')]",
                    "button:has-text('登录')",
                    "button:has-text('注册')",
                    "a:has-text('登录')",
                    ".login-btn"
                ]
                clicked_login = False
                for sel in login_selectors:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=1500):
                            btn.click()
                            print(f"  [biji-auth] ✅ 成功点击登录按钮 (选择器: {sel})")
                            clicked_login = True
                            time.sleep(1.5)
                            break
                    except Exception:
                        continue

                if not clicked_login:
                    print("  [biji-auth] ⚠️ 未能精确触发登录按钮，尝试直接检测页面登录弹窗/协议勾选框...")

                # 勾选同意协议按钮 (.agreement-row button 或 #login-agree)
                try:
                    agree_btn = page.locator(".agreement-row button, #login-agree, [data-v-2b3c8e7c].peer, .agreement-row [data-state]").first
                    if agree_btn.is_visible(timeout=2000):
                        data_state = agree_btn.get_attribute("data-state")
                        if data_state == "unchecked":
                            agree_btn.click()
                            print("  [biji-auth] ✅ 自动勾选得到大脑《用户协议》和《隐私政策》")
                        else:
                            print("  [biji-auth] ℹ️ 得到大脑用户协议已被勾选")
                except Exception as e:
                    print(f"  [biji-auth] 勾选协议提示: {e}")

                time.sleep(2)
                # 截图保存二维码文件
                qr_path = os.path.join(SCREENSHOT_DIR, f"biji_qr_{self.account_id}.png")
                page.screenshot(path=qr_path)
                print()
                print("=" * 60)
                print(f"📸 登录二维码已截取并保存至：")
                print(f"   {qr_path}")
                print("🖥️ 远程桌面操作提示：")
                print("   若需手动在浏览器中交互、输入手机号或处理验证码，请在浏览器中打开：")
                print("   👉 http://localhost:6080 (或在 Web 看板页面右上角点击『🖥️ 远程桌面』)")
                print("💡 提示：若微信没有绑定得到账号，请先在手机/网页端自行登录绑定后再扫码。")
                print("=" * 60)
                print()

                # 等待用户扫码 (最长等待 120 秒)
                print("⏳ 正在等待扫码登录中 (监听 .nickname-row)...")
                try:
                    page.wait_for_selector(".nickname-row", timeout=120000)
                    print("🎉 扫码登录成功！")
                except PlaywrightTimeoutError:
                    print("❌ 等待扫码超时 (120秒)，请重新触发同步。")
                    self.update_account_status(status="NEED_LOGIN")
                    context.close()
                    return

            # 确认在 logged_in 状态，提取昵称
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded")
                time.sleep(2)
                nickname_elem = page.locator(".nickname-row").first
                nickname = nickname_elem.inner_text().strip() if nickname_elem.is_visible() else "已登录用户"
                print(f"✅ 得到账号登录正常，昵称: {nickname}")
                self.update_account_status(nickname=nickname, status="LOGGED_IN")
            except Exception:
                pass

            # ----------------------------------------------------
            # Step 2: 目标驱动：提取本地主表中有【待转录视频】的目标对标博主
            # ----------------------------------------------------
            target_bloggers = self.get_pending_target_bloggers()
            print(f"\n🎯 查找到本地主库共有 {len(target_bloggers)} 个需要同步/待转录的对标博主")

            if not target_bloggers:
                print("ℹ️ 本地数据库中没有需要转录或监控的博主，跳过同步。")
                context.close()
                return

            for b_idx, target_b in enumerate(target_bloggers, start=1):
                b_id = target_b["id"]
                b_name = target_b["name"]
                b_home_url = target_b["home_url"] or ""
                biji_url = target_b["biji_url"] or ""
                topic_alias = target_b["biji_topic_alias"] or "DEFAULT"

                print(f"\n==================================================")
                print(f"👤 [{b_idx}/{len(target_bloggers)}] 正在同步目标博主: 『{b_name}』 (ID: {b_id})")

                # 检查该博主待转录的视频 ID 集合
                pending_notes_map = self.get_pending_note_ids(b_id)
                print(f"  📋 该博主在主库中共有 {len(pending_notes_map)} 条【待转录视频】")

                # 校验 biji_url 自身的有效性（防止历史错误数据/污染数据导致的张冠李戴）
                if biji_url:
                    try:
                        import urllib.parse
                        parsed_url = urllib.parse.urlparse(biji_url)
                        qs = urllib.parse.parse_qs(parsed_url.query)
                        url_follow_name = qs.get("followName", [""])[0]
                        if url_follow_name and url_follow_name != b_name and b_name not in url_follow_name and url_follow_name not in b_name:
                            print(f"  ⚠️ [链接污染自愈] 博主『{b_name}』库中记录的 biji_url 包含了不匹配的账号 (『{url_follow_name}』)，自动清空旧链接，重转寻路模式！")
                            biji_url = ""
                            conn = sqlite3.connect(DB_PATH)
                            conn.cursor().execute("UPDATE bloggers SET biji_url = NULL, biji_follow_id = NULL WHERE id = ?", (b_id,))
                            conn.commit()
                            conn.close()
                    except Exception:
                        pass

                # ----------------------------------------------------
                # 分支 A: 若该博主已绑定 biji_url，直接访问直连！
                # ----------------------------------------------------
                if biji_url:
                    print(f"  ⚡ [直连模式] 博主『{b_name}』已有 biji_url，直接跳转作品页...")
                    print(f"  🌐 目标链接: {biji_url}")
                else:
                    # ----------------------------------------------------
                    # 分支 B: 若未绑定 biji_url，通过知识库寻路检索 2.json 并自动绑定
                    # ----------------------------------------------------
                    print(f"  🔍 [寻路模式] 博主『{b_name}』尚未绑定 biji_url，进入得到知识库寻路...")

                    # 触发/重试加载 1.json 知识库列表
                    if not self.topics_data:
                        print("  📚 [寻路] 知识库缓存为空，正在加载主页获取知识库列表 (1.json)...")
                        try:
                            with page.expect_response(lambda r: "v1/web/topic/mine/list" in r.url and r.status == 200, timeout=6000):
                                page.goto(BASE_URL, wait_until="domcontentloaded")
                        except Exception:
                            page.goto(BASE_URL, wait_until="domcontentloaded")
                        time.sleep(2)

                    print(f"  📚 当前账号共捕获到 {len(self.topics_data)} 个知识库")

                    found_url = None
                    for t_idx, t in enumerate(self.topics_data, start=1):
                        t_alias = t.get("id_alias")
                        t_name = t.get("name") or "知识库"
                        topic_page_url = f"https://www.biji.com/subject/{t_alias}/DEFAULT"

                        print(f"  🔍 [{t_idx}/{len(self.topics_data)}] 正在检索知识库: 『{t_name}』 (Alias: {t_alias})...")

                        self.follows_data = None
                        print(f"     🌐 打开知识库主页: {topic_page_url}...")
                        try:
                            page.goto(topic_page_url, wait_until="domcontentloaded")
                            time.sleep(1.5)
                        except Exception as goto_ex:
                            print(f"     ⚠️ 打开知识库主页提示: {goto_ex}")

                        # 多选择器寻找“博主” Tab
                        tab_selectors = [
                            ".n-tabs-tab[data-name='blogger']",
                            ".n-tabs-tab:has-text('博主')",
                            "div[role='tab']:has-text('博主')",
                            "xpath=//*[contains(@class,'n-tabs-tab') and contains(.,'博主')]",
                            "xpath=//*[contains(text(),'博主') and not(contains(text(),'订阅'))]",
                            "text=博主"
                        ]

                        blogger_tab = None
                        for sel in tab_selectors:
                            try:
                                elem = page.locator(sel).first
                                if elem.is_visible(timeout=1500):
                                    blogger_tab = elem
                                    print(f"     🔍 定位到『博主』Tab (使用选择器: {sel})")
                                    break
                            except Exception:
                                continue

                        if blogger_tab:
                            print(f"     👆 点击『博主』Tab 触发关注列表 (2.json)...")
                            try:
                                with page.expect_response(
                                    lambda r: "v1/web/follow/list" in r.url and r.status == 200,
                                    timeout=8000
                                ):
                                    blogger_tab.click()
                                time.sleep(1.5)
                            except Exception as click_ex:
                                self.capture_error_screenshot(page, f"点击『博主』Tab - 知识库『{t_name}』")
                                print(f"     ❌ [关键步骤异常终止] 点击『博主』Tab 或监听 2.json 失败: {click_ex}")
                        else:
                            self.capture_error_screenshot(page, f"定位『博主』Tab - 知识库『{t_name}』")
                            print(f"     ❌ [关键步骤异常终止] 知识库『{t_name}』中尝试所有选择器均未定位到『博主』Tab，终止该知识库寻路！")

                        if self.follows_data:
                            print(f"     📋 成功捕获到 {len(self.follows_data)} 个关注卡片，对比目标博主『{b_name}』 (URL: {b_home_url or '未配置'})...")
                            # 比对 2.json 里的原主页链接 url 与博主名
                            for follow in self.follows_data:
                                f_id = str(follow.get("id") or "")
                                f_name = follow.get("name") or ""
                                f_url = (follow.get("url") or "").strip()

                                is_match = False
                                match_reason = ""

                                # 1. 优先 URL 精确/包含比对
                                if b_home_url and f_url:
                                    norm_b = b_home_url.rstrip("/").split("?")[0].lower()
                                    norm_f = f_url.rstrip("/").split("?")[0].lower()
                                    clean_key_b = norm_b.split("/")[-1]
                                    clean_key_f = norm_f.split("/")[-1]

                                    if norm_b == norm_f or (clean_key_b and clean_key_b in norm_f) or (clean_key_f and clean_key_f in norm_b):
                                        is_match = True
                                        match_reason = f"原主页链接匹配 ({f_url})"

                                # 2. 降级：博主名完全匹配或包含匹配 (如 '小A' 与 '小A学财经')
                                if not is_match and (f_name and b_name):
                                    if f_name == b_name:
                                        is_match = True
                                        match_reason = f"博主昵称完全匹配 ({f_name})"
                                    elif (b_name in f_name or f_name in b_name) and len(b_name) >= 2:
                                        is_match = True
                                        match_reason = f"博主昵称模糊包含匹配 ({f_name} ↔ {b_name})"

                                print(f"        ├─ 卡片: 『{f_name}』 (ID: {f_id}, URL: {f_url or '无'}) -> 匹配结果: {'✅ 成功 (' + match_reason + ')' if is_match else '❌ 不匹配'}")

                                if is_match:
                                    topic_alias = t_alias
                                    found_url = f"https://www.biji.com/subject/{t_alias}/DEFAULT?followId={f_id}&followName={f_name}"
                                    
                                    # 回写绑定到数据库
                                    conn = sqlite3.connect(DB_PATH)
                                    cursor = conn.cursor()
                                    cursor.execute("""
                                        UPDATE bloggers SET 
                                            data_source = 'biji',
                                            biji_browser_id = ?,
                                            biji_topic_alias = ?,
                                            biji_topic_name = ?,
                                            biji_follow_id = ?,
                                            biji_url = ?
                                        WHERE id = ?
                                    """, (self.account_id, t_alias, t_name, f_id, found_url, b_id))
                                    conn.commit()
                                    conn.close()

                                    print(f"  ✅ [博主绑定成功] ({match_reason}) 已为博主『{b_name}』保存 biji_url: {found_url}")
                                    break
                        else:
                            print(f"     └─ ⚠️ 知识库『{t_name}』未获取到 2.json 关注列表数据 (follows_data 为空)")

                        if found_url:
                            biji_url = found_url
                            break
                        else:
                            print(f"     └─ ℹ️ 知识库『{t_name}』未成功匹配目标博主『{b_name}』")

                if not biji_url:
                    print(f"  ⚠️ 未在得到中找到博主『{b_name}』的对应关注卡片，跳过。")
                    continue

                # ----------------------------------------------------
                # Step 3: 打开博主作品页链接，监听 3.json
                # ----------------------------------------------------
                self.captured_posts.clear()
                print(f"  🌐 [打开作品页] 正在访问: {biji_url}")
                try:
                    with page.expect_response(lambda r: "v1/web/follow/account/posts" in r.url and r.status == 200, timeout=8000):
                        page.goto(biji_url, wait_until="networkidle")
                except Exception as e:
                    print(f"  [biji-posts] 打开博主作品页等待 3.json 响应提示: {e}")

                time.sleep(2)

                # 获取 3.json 拦截到的作品列表
                latest_posts = []
                for url, posts_list in self.captured_posts.items():
                    if posts_list:
                        latest_posts = posts_list

                print(f"  [biji-posts] 博主『{b_name}』在得到共有 {len(latest_posts)} 条作品")

                # 过滤出只匹配主库【待转录视频】的新作品
                target_posts_to_fetch = []
                for post in latest_posts:
                    p_id_str = str(post.get("post_id_str") or post.get("post_id") or "")
                    p_url = post.get("post_url") or ""
                    p_title = post.get("post_name") or post.get("post_title") or ""

                    # 正则提取原视频 aweme_id
                    extracted_aweme_id = None
                    if p_url:
                        match = re.search(r'(?:video/|modal_id=|group_id=|aweme_id=)(\d+)', p_url)
                        if match:
                            extracted_aweme_id = match.group(1)

                    # 100% 绝对精确比对 aweme_id 是否属于待转录列表
                    if (extracted_aweme_id and extracted_aweme_id in pending_notes_map) or (p_id_str in pending_notes_map):
                        print(f"  🎯 [匹配成功] 对应待转录视频 ID: {extracted_aweme_id or p_id_str} | 标题: 『{p_title[:20]}』")
                        target_posts_to_fetch.append(post)
                    else:
                        # 降级尝试前缀匹配标题
                        for p_nid, p_ntitle in pending_notes_map.items():
                            clean_title = p_ntitle.rstrip(".").rstrip("…").strip()
                            if clean_title and (clean_title in p_title or p_title in clean_title):
                                print(f"  🎯 [标题前缀匹配成功] 对应待转录视频 ID: {p_nid} | 标题: 『{p_title[:20]}』")
                                target_posts_to_fetch.append(post)
                                break

                print(f"  [精准筛选] 博主『{b_name}』需打开详情提取文案的作品数: {len(target_posts_to_fetch)}")

                # ----------------------------------------------------
                # Step 4: 打开待转录作品详情页，监听 4.json
                # ----------------------------------------------------
                for post in target_posts_to_fetch[:max_posts_per_blogger]:
                    p_id_str = str(post.get("post_id_str") or post.get("post_id") or "")
                    post_detail_url = f"https://www.biji.com/post/{topic_alias}/{p_id_str}/web"

                    print(f"  🌐 [打开作品详情页] 正在访问: {post_detail_url}")
                    try:
                        with page.expect_response(lambda r: "v1/web/topic/post/detail" in r.url and r.status == 200, timeout=8000):
                            page.goto(post_detail_url, wait_until="networkidle")
                    except Exception as e:
                        print(f"  [biji-detail] 打开作品详情页等待 4.json 响应提示: {e}")

                    time.sleep(2)

                    if p_id_str in self.captured_details:
                        detail_json = self.captured_details[p_id_str]
                        self.save_transcript_to_db(detail_json, blogger_name=b_name)
                    else:
                        print("  [提示] 使用 3.json 现有摘要兜底存储")
                        self.save_transcript_to_db({"c": post}, blogger_name=b_name)

            context.close()
            print("\n🎉 得到 Playwright 抓取流程全部完成！")

        # ----------------------------------------------------
        # 抓取结束后，自动调用 backfill 回写至主作品表 (blogger_notes)
        # ----------------------------------------------------
        print("\n🔄 正在触发得到文案向主数据库的回写匹配...")
        backfill_transcripts(DB_PATH)


class ManualBrowserManager:
    """管理用户从 Web 界面手动拉起的 Playwright Chromium 实例 (支持多平台: 得到、抖音、小红书、B站等)"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ManualBrowserManager, cls).__new__(cls)
                cls._instance.active_sessions = {}
            return cls._instance

    def launch_session(self, account_id="account_01", target_url="https://www.biji.com/subject"):
        """手动拉起指定账号环境的 Playwright Chromium 页面"""
        import threading
        with self._lock:
            # 若已有在运行的同账号线程，发送停止信号
            if account_id in self.active_sessions:
                old_info = self.active_sessions[account_id]
                old_evt = old_info.get("stop_event")
                if old_evt:
                    old_evt.set()
                time.sleep(0.3)

            stop_evt = threading.Event()
            session_info = {
                "account_id": account_id,
                "target_url": target_url,
                "stop_event": stop_evt,
                "started_at": time.time(),
                "status": "running"
            }

            def run_browser():
                context_dir = os.path.join(DATA_DIR, "browser_context", account_id)
                os.makedirs(context_dir, exist_ok=True)
                try:
                    with sync_playwright() as p:
                        print(f"🌐 [手动浏览器] 正在为账号 '{account_id}' 启动 Chromium 有头窗口 (URL: {target_url})...")
                        context = p.chromium.launch_persistent_context(
                            user_data_dir=context_dir,
                            headless=False,
                            viewport={"width": 1280, "height": 800},
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            args=["--no-sandbox", "--disable-setuid-sandbox"]
                        )
                        page = context.new_page()
                        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

                        start_t = time.time()
                        while not stop_evt.is_set():
                            if time.time() - start_t > 900:  # 15 分钟超时自动关闭
                                print(f"⏳ [手动浏览器] 账号 '{account_id}' 会话已达到 15 分钟无操作超时，自动安全关闭。")
                                break

                            # 尝试自动监控得到登录标志
                            try:
                                if "biji.com" in page.url:
                                    nickname_elem = page.locator(".nickname-row").first
                                    if nickname_elem.is_visible(timeout=300):
                                        nickname = nickname_elem.inner_text().strip()
                                        engine = BijiBrowserEngine(account_id=account_id)
                                        engine.update_account_status(nickname=nickname, status="LOGGED_IN")
                            except:
                                pass

                            time.sleep(1)

                        print(f"🛑 [手动浏览器] 关闭账号 '{account_id}' 的 Chromium 会话...")
                        context.close()
                except Exception as ex:
                    print(f"⚠️ [手动浏览器提示/退出] 账号 '{account_id}': {ex}")
                finally:
                    with self._lock:
                        if account_id in self.active_sessions and self.active_sessions[account_id].get("stop_event") == stop_evt:
                            del self.active_sessions[account_id]

            t = threading.Thread(target=run_browser, daemon=True)
            session_info["thread"] = t
            self.active_sessions[account_id] = session_info
            t.start()
            return session_info

    def close_session(self, account_id="account_01"):
        """安全关闭指定账号的手动浏览器会话"""
        with self._lock:
            if account_id in self.active_sessions:
                session_info = self.active_sessions[account_id]
                evt = session_info.get("stop_event")
                if evt:
                    evt.set()
                return True
            return False

    def get_active_sessions(self):
        """获取当前正在运行的手动浏览器会话字典"""
        with self._lock:
            res = {}
            for acc_id, info in self.active_sessions.items():
                res[acc_id] = {
                    "account_id": acc_id,
                    "target_url": info.get("target_url"),
                    "started_at": info.get("started_at"),
                    "running_seconds": int(time.time() - info.get("started_at", time.time()))
                }
            return res


manual_browser_mgr = ManualBrowserManager()


def main():
    parser = argparse.ArgumentParser(description="Get 笔记 Playwright 自动化引擎")
    parser.add_argument("--account", default="account_01", help="得到账号标识 ID")
    parser.add_argument("--headful", action="store_true", help="开启有头模式 (默认无头)")
    parser.add_argument("--max-posts", type=int, default=20, help="每个博主最大抓取数量")
    args = parser.parse_args()

    engine = BijiBrowserEngine(
        account_id=args.account,
        headless=not args.headful
    )
    engine.run_sync(max_posts_per_blogger=args.max_posts)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    main()
