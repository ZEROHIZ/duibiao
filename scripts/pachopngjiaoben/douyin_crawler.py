"""
文件名：douyin_crawler.py
核心职责：抖音对标账号视频与评论自动化抓取脚本（键鼠拟人化画廊模式 + 弹窗自愈与网络接口状态校验）。
主要功能：
1. 从 saved_links.json 读取保存的抖音账号链接，支持主页链接与分享短链接。
2. 启动 Playwright 有头浏览器，并在后台运行轻量级并发循环监控 `douyin-login-new-id` 登录弹窗，实现自动点击 SVG 关闭。
3. 进入博主主页，点击第一个作品。
4. 网络接口驱动校验（Event-Driven Verification）：
   - 点击第一个视频后，校验 `comment/list` 响应是否被截获。若未截获，重试寻找并点击评论按钮。
   - 对每个视频，悬停在分享按钮上，校验 `web_shorten` 响应是否被截获。若未截获，重试悬停。
   - 翻页操作时，通过 `ArrowDown` 切换。校验 `page.url` 的视频 ID 发生变更且拦截到新视频的 `comment/list` 响应才判定翻页成功。若超时，自动重试发送 `ArrowDown` 键。
5. 1:1 对齐 adapters/douyin.js 的结构转换，增量去重写入 douyin_data.json。
"""

import sys
import asyncio
import random
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, Response

# 设置控制台输出编码为 UTF-8，防止 emoji 字符引起编码报错
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# ------------------------------------------------------------------
# 1. 基础数据工具函数
# ------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load_links(filepath="saved_links.json"):
    """
    加载待爬取的抖音账号链接。如果文件不存在，则自动初始化一个模板。
    """
    abs_path = os.path.join(ROOT_DIR, filepath)
    if not os.path.exists(abs_path):
        default_data = {
            "美食博主": [
                {
                    "name": "示例博主",
                    "id": "123456",
                    "url": "https://www.douyin.com/user/MS4wLjABAAAA示例链接"
                }
            ]
        }
        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, 'w', encoding='utf-8') as f:
                json.dump(default_data, f, indent=4, ensure_ascii=False)
            print(f"已自动初始化模板文件: {abs_path}，请在该文件中配置正确的抖音账号主页链接。")
        except Exception as e:
            print(f"创建默认链接文件出错: {e}")
        return []
    
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            links = []
            for category, items in data.items():
                for item in items:
                    links.append({
                        "category": category,
                        "name": item.get("name", "未知"),
                        "id": item.get("id", "未知"),
                        "url": item.get("url", "")
                    })
            return links
    except Exception as e:
        print(f"读取链接文件出错: {e}")
        return []


def extract_title(desc):
    """
    提取标题，取描述的第一行并截取前25个字（与 JS 保持 1:1 一致）
    """
    if not desc:
        return "无标题视频"
    first_line = desc.split('\n')[0].strip()
    if len(first_line) > 25:
        return first_line[:25] + "..."
    return first_line or "无标题视频"


# ------------------------------------------------------------------
# 2. 递归数据处理器（1:1 复刻 JS 中 UnifiedDataHub 的清洗逻辑）
# ------------------------------------------------------------------

def recursive_extract_videos(obj, video_list=None):
    """
    递归查找视频对象 payload (带 aweme_id 且含有 desc 或 statistics)
    """
    if video_list is None:
        video_list = []
    if isinstance(obj, dict):
        if "aweme_id" in obj and ("desc" in obj or "statistics" in obj):
            video_list.append(obj)
        else:
            for val in obj.values():
                recursive_extract_videos(val, video_list)
    elif isinstance(obj, list):
        for item in obj:
            recursive_extract_videos(item, video_list)
    return video_list


def recursive_extract_comments(obj, comment_list=None):
    """
    递归查找评论对象 payload (带 cid, text 且含有 aweme_id)
    """
    if comment_list is None:
        comment_list = []
    if isinstance(obj, dict):
        if "cid" in obj and "text" in obj and "aweme_id" in obj:
            comment_list.append(obj)
        else:
            for val in obj.values():
                recursive_extract_comments(val, comment_list)
    elif isinstance(obj, list):
        for item in obj:
            recursive_extract_comments(item, comment_list)
    return comment_list


def normalize_douyin_video(v):
    """
    将捕获的单个抖音原始 aweme 视频，归一化映射为通用标准规范数据结构
    """
    if not v or "aweme_id" not in v:
        return None
    
    vid = str(v["aweme_id"])
    statistics = v.get("statistics", {})
    desc_text = v.get("desc", "")
    title = extract_title(desc_text)
    time_val = v.get("create_time", 0)
    if not time_val:
        time_val = int(datetime.now().timestamp())
        
    # 提取直网可直接下载的播放直链接 (优先匹配含有 www.douyin.com 属性的节点)
    play_url = ""
    video_node = v.get("video", {})
    if video_node:
        play_addr = video_node.get("play_addr", {})
        if play_addr:
            url_list = play_addr.get("url_list", [])
            if isinstance(url_list, list):
                douyin_url = next((url for url in url_list if url and "www.douyin.com" in url), None)
                if douyin_url:
                    play_url = douyin_url
                elif len(url_list) > 0:
                    play_url = url_list[0]
                    
    desc = play_url if play_url else desc_text
    
    interact_info = {
        "likedCount": str(statistics.get("digg_count", 0)),
        "collectedCount": str(statistics.get("collect_count", 0)),
        "commentCount": str(statistics.get("comment_count", 0)),
        "sharedCount": str(statistics.get("share_count", 0))
    }
    
    return {
        "_feed_id": vid,
        "captureSource": "profile",
        "note": {
            "noteId": vid,
            "title": title,
            "desc": desc,
            "type": "video",
            "time": time_val,
            "interactInfo": interact_info
        },
        "comments": {
            "list": []
        }
    }


# ------------------------------------------------------------------
# 3. 增量去重存储接口
# ------------------------------------------------------------------

def save_to_json(new_items, filename="douyin_data.json"):
    """
    将抓取结果增量增改并保存至本地 JSON 文件
    """
    dir_name = os.path.dirname(os.path.abspath(filename))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        
    existing_items = []
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing_items = json.load(f)
                if not isinstance(existing_items, list):
                    existing_items = []
        except Exception as e:
            print(f"加载已有数据文件出错，将重新创建: {e}")
            existing_items = []

    # 建立主键映射，方便增量快速合并与去重
    db_map = {item["_feed_id"]: item for item in existing_items if "_feed_id" in item}

    for new_item in new_items:
        vid = new_item["_feed_id"]
        if vid not in db_map:
            db_map[vid] = new_item
        else:
            # 增量式合并更新属性
            existing = db_map[vid]
            existing["note"]["title"] = new_item["note"]["title"]
            existing["note"]["desc"] = new_item["note"]["desc"]
            existing["note"]["time"] = new_item["note"]["time"]
            existing["note"]["interactInfo"] = new_item["note"]["interactInfo"]
            
            if "shareUrl" in new_item["note"]:
                existing["note"]["shareUrl"] = new_item["note"]["shareUrl"]
                
            # 合并评论列表，使用评论 cid 去重
            existing_comments = existing["comments"]["list"]
            new_comments = new_item["comments"]["list"]
            
            comment_id_set = {str(c["cid"]) for c in existing_comments if "cid" in c}
            for nc in new_comments:
                nc_id = str(nc["cid"])
                if nc_id not in comment_id_set:
                    existing_comments.append(nc)
                    comment_id_set.add(nc_id)

    # 存盘回写
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(list(db_map.values()), f, indent=4, ensure_ascii=False)
        print(f"数据已成功增量合并保存至: {filename}，当前总记录数: {len(db_map)}")
    except Exception as e:
        print(f"写入数据文件出错: {e}")


# ------------------------------------------------------------------
# 4. 后台并发监测与交互辅助函数
# ------------------------------------------------------------------

ENABLE_POPUP_MONITOR = False

async def close_login_popup_monitor(page):
    """
    后台轻量级协程：实时监控 `douyin-login-new-id` 并点击其下的 SVG 进行关闭。
    """
    global ENABLE_POPUP_MONITOR
    popup_selectors = [
        '#douyin-login-new-id',
        '.douyin-login-new-id',
        '[id*="douyin-login-new-id"]',
        '[class*="douyin-login-new-id"]'
    ]
    while True:
        if not ENABLE_POPUP_MONITOR:
            await asyncio.sleep(1.0)
            continue
        try:
            for selector in popup_selectors:
                popup = page.locator(selector).first
                if await popup.count() > 0 and await popup.is_visible():
                    svg = popup.locator('svg').first
                    if await svg.count() > 0 and await svg.is_visible():
                        await svg.click()
                        print(f"[弹窗检测] 成功关闭出现的登录弹窗: {selector} 下的 SVG")
                        await asyncio.sleep(2.0)
                        break
        except Exception:
            pass
        await asyncio.sleep(1.0)  # 每秒检测一次


async def close_tutorial_popup_monitor(page):
    """
    后台轻量级协程：实时监控并自动点击“我知道了”关闭教程遮罩。
    """
    while True:
        try:
            know_btn = page.locator('text="我知道了"').first
            if await know_btn.count() > 0 and await know_btn.is_visible():
                await know_btn.click()
                print("[教程弹窗] 成功点击 '我知道了' 关闭教程遮罩")
                await asyncio.sleep(2.0)
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def click_comment_button(page):
    """
    寻找并点击视频画廊中的评论展开图标
    """
    comment_btn_selectors = [
        '[data-e2e*="comment" i]',
        '[data-e2e*="Comment"]',
        '[data-e2e="comment-icon"]',
        '[data-e2e="video-comment-icon"]',
        '.comment-icon',
        '[class*="comment-icon"]',
        'svg[class*="comment" i]',
        'div[class*="comment" i]'
    ]
    for btn_sel in comment_btn_selectors:
        try:
            btn = page.locator(btn_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                print(f"  [交互] 已点击评论按钮: {btn_sel}")
                return True
        except Exception:
            pass
            
    # 如果全部失败，打印调试信息
    try:
        e2e_attrs = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('[data-e2e]')).map(el => el.getAttribute('data-e2e'));
        }''')
        print(f"  [调试] 未能点击评论，当前页面上的所有 data-e2e 属性: {e2e_attrs}")
    except Exception:
        pass
    return False


async def hover_share_button(page):
    """
    寻找当前激活视频的分享按钮并进行鼠标悬停 (Hover)
    """
    active_container_selectors = [
        '[data-e2e="feed-active-video"]',
        '.slider-video',
        '.xgplayer-playing',
        'div[class*="active"]'
    ]
    share_btn_selectors = [
        '[data-e2e*="share" i]',
        '[data-e2e*="Share"]',
        '[data-e2e="share-icon"]',
        '[data-e2e="video-share-container"]',
        '.share-info',
        '.video-share',
        '[class*="share" i]'
    ]
    
    # 优先在当前激活的视频容器中定位分享
    for container_sel in active_container_selectors:
        try:
            container = page.locator(container_sel).first
            if await container.count() > 0 and await container.is_visible():
                for share_sel in share_btn_selectors:
                    btn = container.locator(share_sel).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.hover()
                        print(f"  [交互] 已悬停当前视频容器内的分享按钮: {share_sel}")
                        return True
        except Exception:
            pass
            
    # 全局兜底
    for share_sel in share_btn_selectors:
        try:
            btn = page.locator(share_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.hover()
                print(f"  [交互] 已悬停全局可见的分享按钮: {share_sel}")
                return True
        except Exception:
            pass
            
    return False


# ------------------------------------------------------------------
# 5. 核心自动化操纵逻辑
# ------------------------------------------------------------------

async def check_page_login_state(page, context):
    """
    检查当前页面和 Cookie 状态，返回 (is_logged_in, target_login_btn, is_popup_visible)
    - is_logged_in: True 表示确认为已登录状态
    - target_login_btn: 如果未登录，返回可能的可点击登录按钮 Locator，若没有则为 None
    - is_popup_visible: True 表示当前已经弹出了登录窗口
    """
    # 1. 检测是否有登录弹窗出现
    is_popup_visible = await page.locator('#douyin-login-new-id, .douyin-login-new-id').first.is_visible()
    
    # 2. 检测是否有已登录特征
    # 包括：[data-e2e="live-avatar"] 头像元素、其它头像或特定 Cookie (sessionid)
    is_logged_in = False
    
    avatar_selectors = [
        '[data-e2e="live-avatar"]',
        'img[class*="avatar"]',
        '[data-e2e="avatar"]',
        '.user-avatar',
        'a[href*="/user/self"]'
    ]
    for sel in avatar_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                is_logged_in = True
                break
        except Exception:
            pass
            
    # Cookie 检测
    try:
        cookies = await context.cookies()
        if any(c['name'] == 'sessionid' for c in cookies):
            is_logged_in = True
    except Exception:
        pass
        
    # 如果已经有弹窗，即使有其他登录标志，也应视为未登录扫码状态（或登录弹窗打断状态）
    if is_popup_visible:
        is_logged_in = False
        
    # 3. 寻找潜在的“登录”按钮元素
    target_login_btn = None
    login_btn_selectors = [
        '.semi-button-content',
        'button:has-text("登录")',
        'div:has-text("登录")',
        '[data-e2e="login-button"]'
    ]
    
    # 在所有可能选择器中，寻找第一个可见且文字包含“登录”的按钮
    for sel in login_btn_selectors:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            found = False
            for i in range(count):
                btn = locs.nth(i)
                if await btn.is_visible():
                    text = await btn.text_content()
                    if text and "登录" in text:
                        target_login_btn = btn
                        found = True
                        break
            if found:
                break
        except Exception:
            pass
            
    return is_logged_in, target_login_btn, is_popup_visible


async def ensure_login(page, context):
    print("[登录检测] 开始检查当前页面登录状态...")
    
    # 首先稍微等待 3 秒以使页面组件稳定渲染
    await asyncio.sleep(3.0)
    
    # 循环轮询检查页面状态，最多轮询 5 次以防加载缓慢
    is_logged_in = False
    target_login_btn = None
    is_popup_visible = False
    
    for attempt in range(5):
        is_logged_in, target_login_btn, is_popup_visible = await check_page_login_state(page, context)
        if is_logged_in or is_popup_visible or target_login_btn:
            break
        await asyncio.sleep(1.0)
        
    if is_logged_in:
        print("[登录检测] 检测到页面头像或 sessionid Cookie，判定为已登录状态，继续执行...")
        return True
        
    # 如果未登录且没有展示弹窗，但有登录按钮，尝试点击按钮调起弹窗
    if not is_popup_visible and target_login_btn:
        print("[登录检测] 检测到页面处于未登录状态（存在登录按钮），尝试点击按钮触发登录弹窗...")
        try:
            await target_login_btn.click()
            # 循环等待弹窗渲染，最多等待 8 秒 (16 * 0.5s)
            for _ in range(16):
                await asyncio.sleep(0.5)
                is_popup_visible = await page.locator('#douyin-login-new-id, .douyin-login-new-id').first.is_visible()
                if is_popup_visible:
                    break
        except Exception as e:
            print(f"[登录检测] 点击登录按钮出错: {e}")
            
    # 如果此时依然没有登录弹窗，进行 Cookie 的兜底检测
    if not is_popup_visible:
        try:
            cookies = await context.cookies()
            if any(c['name'] == 'sessionid' for c in cookies):
                print("[登录检测] 虽然未检测到登录弹窗，但 Cookie 中存在 sessionid，判定为已登录。")
                return True
        except Exception:
            pass
        print("[登录检测] ❌ 无法调起登录弹窗，且未检测到有效登录 Session！在无头环境下无法继续任务。")
        return False
        
    # 如果弹窗成功显示，进入扫码登录等待流程
    print("[验证码拦截] ⚠️ 检测到未登录状态且已弹出登录框！")
    print("  >> 等待 5 秒确保二维码加载完成...")
    await asyncio.sleep(5.0)
    
    max_wait = 180  # 最多等待 180 秒
    qr_path = os.path.join(ROOT_DIR, "screenshots", "login_qr.png")
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    
    for sec in range(0, max_wait, 3):
        # 截图整页供用户扫码
        try:
            await page.screenshot(path=qr_path)
        except Exception as se:
            print(f"[登录检测] 截图失败: {se}")
            
        print(f"[验证码拦截] ⚠️ 请使用手机扫码登录！登录二维码截图已更新，已等待 {sec} 秒...")
        
        # 检测登录弹窗是否关闭（用户成功扫码后，弹窗会自动消失）
        is_popup_visible = await page.locator('#douyin-login-new-id, .douyin-login-new-id').first.is_visible()
        if not is_popup_visible:
            print("[登录检测] 检测到登录弹窗已关闭，正在刷新页面验证登录状态...")
            await page.reload()
            # 刷新后等待页面渲染稳定
            await asyncio.sleep(5.0)
            
            # 重新检测页面登录状态
            new_is_logged, _, _ = await check_page_login_state(page, context)
            if new_is_logged:
                print("[登录检测] 🎉 页面刷新后判定为已登录，登录成功！")
                if os.path.exists(qr_path):
                    try:
                        os.remove(qr_path)
                    except Exception:
                        pass
                return True
            else:
                print("[登录检测] ⚠️ 页面刷新后未检测到已登录特征，重新尝试调起登录弹窗并继续等待...")
                # 再次获取页面上的登录按钮并点击
                _, target_login_btn, is_popup_visible = await check_page_login_state(page, context)
                if not is_popup_visible and target_login_btn:
                    try:
                        await target_login_btn.click()
                        await asyncio.sleep(3.0)
                    except Exception as e:
                        print(f"[登录检测] 重新点击登录按钮出错: {e}")
                        
        await asyncio.sleep(3.0)
        
    print("[登录检测] ❌ 等待扫码登录超时 (3 分钟)，脚本终止。")
    return False


async def scroll_comments_container(page, scroll_times=4):
    print("  [交互] 正在下滚评论区以加载更多评论...")
    
    # 优先指定稳定且精确的 data-e2e 属性和类名
    container_selectors = [
        '[data-e2e="comment-list"]',
        '.comment-mainContent',
        '.comment-list'
    ]
    
    container = None
    for sel in container_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                container = loc
                print(f"  [交互] 成功定位评论容器: {sel}")
                break
        except:
            pass
            
    if container:
        for t in range(scroll_times):
            try:
                await container.evaluate("el => el.scrollTop = el.scrollHeight")
                print(f"    下滚第 {t + 1}/{scroll_times} 次...")
                await asyncio.sleep(random.uniform(1.2, 1.8))
            except Exception as se:
                print(f"    下滚失败: {se}")
    else:
        print("  [交互] 未能定位到滚动评论容器，尝试 PageDown 键下滚评论...")
        try:
            await page.keyboard.press("PageDown")
            await asyncio.sleep(1.5)
            await page.keyboard.press("PageDown")
            await asyncio.sleep(1.5)
        except Exception as se:
            print(f"  [交互] 按键下滚失败: {se}")


async def collect_douyin_data(url, name, max_videos=5, filename="douyin_data.json", headless=True):
    print(f"\n正在启动浏览器，目标博主 [{name}]，链接: {url}")
    print(f"本次抓取上限为 {max_videos} 个视频，正在初始化中...")

    # 加载已有数据以确定哪些视频已经成功抓取过
    existing_vids = set()
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                if isinstance(existing_data, list):
                    for item in existing_data:
                        vid = item.get("_feed_id")
                        if vid:
                            existing_vids.add(str(vid))
        except Exception as err:
            print(f"读取已有数据进行去重分析失败: {err}")
    if existing_vids:
        print(f"已爬取的视频 ID 集合 (共 {len(existing_vids)} 个): {existing_vids}")

    # 本次任务拦截的中间数据存储库
    post_videos_raw = []        # 存放博主主页加载的原始视频数据
    captured_comments = {}      # aweme_id -> list of raw comments
    captured_short_links = {}   # aweme_id -> shortened url string

    def get_query_param(url_str, param_name):
        try:
            parsed = urlparse(url_str)
            qs = parse_qs(parsed.query)
            if param_name in qs:
                return qs[param_name][0]
        except Exception:
            pass
        return None

    # 获取当前活动视频 ID (解析当前 Playwright 的 URL)
    def parse_active_video_id(current_url):
        match = re.search(r'(?:video/|modal_id=|group_id=|reflow_video_id=)(\d+)', current_url)
        if match:
            return match.group(1)
        return None

    # 注册接口拦截函数
    async def handle_response(response: Response):
        response_url = response.url
        
        # 1. 拦截博主作品列表
        if "aweme/v1/web/aweme/post" in response_url:
            try:
                data = await response.json()
                videos = recursive_extract_videos(data)
                if videos:
                    # 去重添加，防止刷新或重复请求导致数据堆积
                    existing_ids = {str(v["aweme_id"]) for v in post_videos_raw if "aweme_id" in v}
                    new_count = 0
                    for v in videos:
                        vid = str(v.get("aweme_id", ""))
                        if vid and vid not in existing_ids:
                            post_videos_raw.append(v)
                            existing_ids.add(vid)
                            new_count += 1
                    print(f"  [拦截] 成功拦截到作品列表，本页共 {len(videos)} 个视频，去重新增 {new_count} 个")
            except Exception:
                pass
                
        # 2. 拦截评论列表
        elif "aweme/v1/web/comment/list" in response_url:
            try:
                data = await response.json()
                comments = recursive_extract_comments(data)
                if comments:
                    # 确定归宿视频 ID：优先读取请求 URL 参数，兜底读取评论体参数
                    vid = get_query_param(response_url, "aweme_id")
                    if not vid and comments[0] and "aweme_id" in comments[0]:
                        vid = str(comments[0]["aweme_id"])
                    
                    if vid:
                        if vid not in captured_comments:
                            captured_comments[vid] = []
                        
                        existing_cids = {str(c["cid"]) for c in captured_comments[vid]}
                        for c in comments:
                            cid = str(c["cid"])
                            if cid not in existing_cids:
                                captured_comments[vid].append(c)
                                existing_cids.add(cid)
                        print(f"  [拦截] 成功抓取视频 [{vid}] 的评论，合并后共 {len(captured_comments[vid])} 条")
            except Exception:
                pass

        # 3. 拦截分享短链接
        elif "aweme/v1/web/web_shorten" in response_url:
            try:
                data = await response.json()
                short_url = data.get("data")
                if short_url:
                    # 从 query 中的 target 参数里解析出视频 ID (支持 URL 编码的 video%2F<id> 或 video/<id>)
                    vid = None
                    match = re.search(r'video(?:%2F|/)(\d+)', response_url)
                    if match:
                        vid = match.group(1)
                    else:
                        vid = get_query_param(response_url, "group_id") or get_query_param(response_url, "aweme_id")
                        
                    if vid:
                        captured_short_links[vid] = short_url
                        print(f"  [拦截] 成功抓取视频 [{vid}] 的分享短链接: {short_url}")
            except Exception:
                pass

    user_data_dir = os.path.join(ROOT_DIR, "data", "browser_context")
    os.makedirs(user_data_dir, exist_ok=True)
    
    async with async_playwright() as p:
        print(f"[浏览器启动] 使用持久化会话数据目录: {user_data_dir}, 无头模式: {headless}")
        
        # 启动持久化上下文
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("response", handle_response)
        
        popup_monitor_task = None
        tutorial_monitor_task = None

        try:
            # 1. 直接访问目标主页
            print(f"正在访问目标主页: {url} ...")
            await page.goto(url, timeout=60000)
            
            # 关闭弹窗监控（登录期间不自动关闭登录弹窗）
            global ENABLE_POPUP_MONITOR
            ENABLE_POPUP_MONITOR = False
            
            # 校验并执行扫码登录流程
            login_success = await ensure_login(page, context)
            if not login_success:
                print("登录状态校验失败，终止任务。")
                await context.close()
                return False
                
            # 登录成功，启用自动清理弹窗协程
            ENABLE_POPUP_MONITOR = True
            
            # 激活后台登录弹窗与教程关闭实时检测任务
            popup_monitor_task = asyncio.create_task(close_login_popup_monitor(page))
            tutorial_monitor_task = asyncio.create_task(close_tutorial_popup_monitor(page))
            
            # 检测验证码与列表加载
            captcha_selectors = [
                ".captcha_verify_container",
                "#captcha-trigger",
                ".secsdk-captcha-drag-slider",
                "#secsdk-captcha-drag-wrapper",
                "[class*='captcha']",
                "iframe[src*='captcha']"
            ]

            start_time = asyncio.get_event_loop().time()
            page_loaded = False
            
            while not page_loaded:
                first_video_locator = page.locator('a[href*="/video/"], [id^="waterfall_item_"]').first
                if await first_video_locator.count() > 0 and await first_video_locator.is_visible():
                    page_loaded = True
                    break
                
                # 检测验证码
                captcha_detected = False
                for selector in captcha_selectors:
                    try:
                        el = page.locator(selector)
                        if await el.count() > 0 and await el.first.is_visible():
                            captcha_detected = True
                            print(f"[验证码拦截] 检测到滑动验证码: {selector}，已自动为您保存截图至 screenshots/")
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            os.makedirs("screenshots", exist_ok=True)
                            await page.screenshot(path=f"screenshots/captcha_{name}_{timestamp}.png")
                            print("  >> 请在打开的浏览器窗口中手动滑块解锁！脚本将在此处静默等待...")
                            break
                    except Exception:
                        pass
                
                if asyncio.get_event_loop().time() - start_time > 45.0:
                    print("  >> 页面加载超时，未检测到作品列表。")
                    break
                    
                await asyncio.sleep(2.0)

            if not page_loaded:
                print("未进入博主主页或数据加载失败，终止本次抓取。")
                return False

            # 等待一小会儿确保 `aweme/v1/web/aweme/post` 响应已下载
            await asyncio.sleep(3.0)

            if not post_videos_raw:
                print("未截获作品接口，尝试下滚刷新...")
                await page.evaluate("window.scrollBy(0, 400)")
                await asyncio.sleep(3.0)

            if not post_videos_raw:
                raise Exception("错误：未截获作品接口数据（aweme/v1/web/aweme/post），无法获取视频 ID 列表，抓取中断。")

            # 第一重保险：如果计划抓取的数量多于当前拦截到的数量，通过下滚主页来加载更多视频
            last_count = len(post_videos_raw)
            no_new_video_count = 0
            while len(post_videos_raw) < max_videos and no_new_video_count < 5:
                print(f"当前已去重拦截到 {len(post_videos_raw)} 个视频，少于计划抓取的 {max_videos} 个，正在下滚主页加载更多...")
                await page.evaluate("window.scrollBy(0, 1500)")
                await asyncio.sleep(2.5)
                current_count = len(post_videos_raw)
                if current_count == last_count:
                    no_new_video_count += 1
                else:
                    no_new_video_count = 0
                    last_count = current_count

            # 限制抓取数量不超过已拦截到的视频总数
            max_videos = min(max_videos, len(post_videos_raw))
            print(f"检测到博主共有 {len(post_videos_raw)} 个视频，本次计划抓取上限为 {max_videos} 个。")

            # 查找第一个未爬取新视频的索引 (早期更新判定与定位点击)
            start_idx = None
            for i in range(max_videos):
                v_id = str(post_videos_raw[i]['aweme_id'])
                if v_id not in existing_vids:
                    start_idx = i
                    break
                    
            if start_idx is None:
                print("\n[早期更新检测] 🎉 检测到该博主的最前部作品全部已存在于数据库中，判定无新视频发布，跳过抓取流程直接安全退出！")
                return True
                
            target_vid = str(post_videos_raw[start_idx]['aweme_id'])
            print(f"\n[早期更新检测] 发现新视频或需要更新的置顶视频，索引: {start_idx + 1}/{max_videos}，视频 ID: {target_vid}")

            # 给后台弹窗任务一点时间，以清理在加载完毕后可能瞬时触发的登录对话框
            await asyncio.sleep(1.5)

            # 2. 点击指定新视频卡片，进入画廊详情模态框
            video_card_selectors = [
                f'[data-e2e="user-post-list"] a[href*="/video/{target_vid}"]',
                f'a[href*="/video/{target_vid}"]',
                f'[id*="{target_vid}"]',
                '[data-e2e="user-post-list"] a[href*="/video/"]',
                'a[href*="/video/"]'
            ]
            
            clicked_successfully = False
            
            # 画廊播放器常见标识元素
            player_selectors = [
                '[data-e2e="feed-active-video"]',
                '.slider-video',
                '.xgplayer',
                'div[class*="active"]'
            ]

            for selector in video_card_selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0 and await locator.is_visible():
                        print(f"正在精准点击目标新视频以进入画廊播放器 (使用选择器: {selector})...")
                        await locator.click()
                        
                        # 检测画廊播放器是否打开 (通过检测播放器元素渲染，或检测该视频的评论API是否被拦截)
                        for _ in range(16):
                            await asyncio.sleep(0.5)
                            player_opened = False
                            for p_sel in player_selectors:
                                if await page.locator(p_sel).first.count() > 0 and await page.locator(p_sel).first.is_visible():
                                    player_opened = True
                                    break
                            if player_opened or target_vid in captured_comments:
                                clicked_successfully = True
                                break
                        if clicked_successfully:
                            break
                except Exception as click_err:
                    print(f"点击 {selector} 出错: {click_err}")
            
            if not clicked_successfully:
                raise Exception(f"错误：未能成功精准点击新卡片进入画廊播放器，视频 [{target_vid}] 未能加载！")

            # 3. 校验评论加载 (API URL 响应驱动)
            comments_loaded = False
            for attempt in range(5):
                if target_vid in captured_comments:
                    comments_loaded = True
                    print(f"  [校验] 成功检测到目标视频 [{target_vid}] 的评论数据已加载！")
                    break
                print(f"  [校验] 未检测到评论 API 响应 (尝试 {attempt + 1}/5)，尝试点击评论按钮...")
                await click_comment_button(page)
                await asyncio.sleep(2.0)

            if not comments_loaded:
                raise Exception(f"错误：多次尝试后仍未捕获到目标视频 [{target_vid}] 的评论接口响应 (comment/list)！抓取中断。")
            else:
                await scroll_comments_container(page, scroll_times=4)

            # 4. 拟人操纵循环：Hover分享 -> 校验短链 -> 按方向下键 -> 校验下一页评论
            print(f"\n开始循环模拟键盘切换视频，计划从索引 {start_idx + 1} 循环至 {max_videos}：")
            
            should_hover_share = True
            crawled_vids_in_this_run = []
            
            for idx in range(start_idx, max_videos):
                raw_v = post_videos_raw[idx]
                active_vid = str(raw_v['aweme_id'])
                is_already_crawled = active_vid in existing_vids
                is_pinned = (raw_v.get("is_top") == 1) or (raw_v.get("tag", {}).get("is_top") == 1) or (raw_v.get("tag", {}).get("top") == 1)
                
                if is_already_crawled and not is_pinned:
                    print(f"\n[历史界限] 检测到已爬取的非置顶历史视频 [{active_vid}]，判定后续视频均已同步。")
                    print(">>> 触发自动提前终止同步，结束任务。")
                    break
                    
                crawled_vids_in_this_run.append(active_vid)
                
                # 随机等待，防反爬 (已爬取过的可以用更小的随机等待，例如 0.2 ~ 0.5s)
                if is_already_crawled:
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                else:
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                
                print(f"\n[{idx + 1}/{max_videos}] 当前处理视频 ID: {active_vid}")

                if is_already_crawled:
                    print(f"  [跳过] 视频 [{active_vid}] 已经爬取过，直接跳过 Hover 分享和评论抓取。")
                else:
                    # A. 校验分享短链 (API URL 响应驱动)
                    if should_hover_share:
                        short_link_loaded = False
                        for attempt in range(3):
                            if active_vid in captured_short_links:
                                short_link_loaded = True
                                print(f"  [校验] 成功截获分享短链: {captured_short_links[active_vid]}")
                                break
                            print(f"  [校验] 未获取到分享短链 (尝试 {attempt + 1}/3)，触发分享按钮 Hover...")
                            await hover_share_button(page)
                            await asyncio.sleep(2.0)
                            
                        if not short_link_loaded:
                            print("  [提示] 检测到当前处于未登录状态或被分享弹窗阻截，后续视频将自动跳过 Hover 分享动作并直接使用直链代替。")
                            should_hover_share = False
                            # 点击视频播放器主体区域以清除弹出的分享面板/遮罩，防碍下键翻页
                            try:
                                player_container = page.locator('.xgplayer-video-container, .slider-video, [data-e2e="feed-active-video"]').first
                                if await player_container.count() > 0 and await player_container.is_visible():
                                    await player_container.click()
                                    print("  [弹窗检测] 已点击视频主体区域以关闭分享浮层")
                                    await asyncio.sleep(1.0)
                            except Exception:
                                pass
                    else:
                        print("  [校验] 跳过 Hover 分享按钮 (免登录直链模式)")
                        
                    # B. 下滚评论区以获取更多评论
                    await scroll_comments_container(page, scroll_times=4)

                # 如果是最后一个，不需要按向下键了
                if idx == max_videos - 1:
                    print("已到达计划抓取上限，结束视频切换。")
                    break

                # B. 模拟向下按键切换并进行下一页评论接口拦截校验
                has_pressed_down = False

                # 第二重保险（自愈）：如果当前列表里已经没有下一个视频项了，触发按向下键让播放器自动加载下一页
                if idx + 1 >= len(post_videos_raw):
                    print("  [提示] 已经到达当前拦截作品列表的末尾，尝试按下 `ArrowDown` 触发视频切换与自动加载...")
                    await page.keyboard.press("ArrowDown")
                    has_pressed_down = True
                    
                    # 等待作品列表接口被拦截并更新 post_videos_raw，最多等待 8 秒
                    post_loaded = False
                    for _ in range(16): # 16 * 0.5s = 8s
                        await asyncio.sleep(0.5)
                        if idx + 1 < len(post_videos_raw):
                            post_loaded = True
                            break
                            
                    if not post_loaded:
                        print("  [提示] 自动加载未响应，尝试下滚背景主页以强制加载更多作品...")
                        try:
                            await page.evaluate("window.scrollBy(0, 1500)")
                        except Exception:
                            pass
                        for _ in range(8):
                            await asyncio.sleep(0.5)
                            if idx + 1 < len(post_videos_raw):
                                post_loaded = True
                                break
                                
                    if not post_loaded:
                        print("  [提示] 无法加载到更多新视频，抓取安全结束。")
                        break

                next_vid = str(post_videos_raw[idx + 1]['aweme_id'])
                next_already_crawled = next_vid in existing_vids
                
                if not has_pressed_down:
                    print(f"  [操作] 模拟键盘按下 `ArrowDown` 切换至下一个视频 [{next_vid}]...")
                    await page.keyboard.press("ArrowDown")

                transition_success = False
                
                if next_already_crawled:
                    # 下一个也是已爬取的，只需极速检查 URL 是否切换成功
                    for attempt in range(5):
                        await asyncio.sleep(0.3)
                        current_active_id = parse_active_video_id(page.url)
                        if current_active_id == next_vid:
                            transition_success = True
                            print(f"  [校验] 成功切换至已爬取视频 [{next_vid}] (基于 URL 校验)")
                            break
                        print(f"  [校验] 等待 URL 切换中... (尝试 {attempt + 1}/5)")
                        if attempt == 2:
                            print("  [操作] 重试按下 `ArrowDown` 键...")
                            await page.keyboard.press("ArrowDown")
                else:
                    # 进行翻页状态自愈校验（直到新视频 ID 加载并截获评论 API 响应）
                    for attempt in range(4):
                        await asyncio.sleep(1.5)
                        
                        # 校验条件：下一个视频的 comment/list 接口数据被拦截到
                        if next_vid in captured_comments:
                            transition_success = True
                            print(f"  [校验] 成功翻页至新视频 [{next_vid}]，且已成功拦截其评论数据！")
                            break
                        
                        print(f"  [校验] 翻页等待中... (第 {attempt + 1}/4 次尝试)")
                        # 如果没拉到新评论，可能是键盘按键丢失，执行按键重试
                        if next_vid not in captured_comments:
                            print("  [操作] 重试按下 `ArrowDown` 键...")
                            await page.keyboard.press("ArrowDown")

                if not transition_success:
                    raise Exception(f"错误：未能成功翻页至下一个视频 (当前视频: {active_vid}，下一视频: {next_vid})！抓取中断。")

            # 5. 数据拼装整合
            print("\n开始规整拦截到的所有数据...")
            processed_videos = []
            video_entities = {}
            
            # 优先填充主页 API 拦截的视频中被处理过的部分
            crawled_vids_set = set(crawled_vids_in_this_run)
            for raw_v in post_videos_raw:
                vid = str(raw_v.get("aweme_id"))
                if vid in crawled_vids_set and vid not in video_entities:
                    normalized = normalize_douyin_video(raw_v)
                    if normalized:
                        video_entities[vid] = normalized

            # 补齐未拦截但激活了详情的视频骨架 (限本次运行列表)
            active_detail_vids = (set(captured_comments.keys()) | set(captured_short_links.keys())) & crawled_vids_set
            for vid in active_detail_vids:
                if vid not in video_entities:
                    video_entities[vid] = {
                        "_feed_id": vid,
                        "captureSource": "profile",
                        "note": {
                            "noteId": vid,
                            "title": f"抖音视频 ({vid})",
                            "desc": f"https://www.douyin.com/video/{vid}",
                            "type": "video",
                            "time": int(datetime.now().timestamp()),
                            "interactInfo": {
                                "likedCount": "0",
                                "collectedCount": "0",
                                "commentCount": "0",
                                "sharedCount": "0"
                            }
                        },
                        "comments": {
                            "list": []
                        }
                    }

            # 注入评论与短链
            for vid, entity in video_entities.items():
                if vid in captured_comments:
                    raw_comments = captured_comments[vid]
                    # 按照 digg_count 点赞数从高到低排序评论
                    raw_comments = sorted(raw_comments, key=lambda x: x.get("digg_count", 0), reverse=True)
                    comment_list = []
                    for rc in raw_comments:
                        user_info = rc.get("user", {})
                        avatar_node = user_info.get("avatar_thumb", {}) or user_info.get("avatar_168x168", {})
                        avatar_urls = avatar_node.get("url_list", []) if avatar_node else []
                        
                        comment_list.append({
                            "cid": str(rc.get("cid")),
                            "text": rc.get("text", ""),
                            "create_time": rc.get("create_time", 0),
                            "digg_count": rc.get("digg_count", 0),
                            "user": {
                                "nickname": user_info.get("nickname", "匿名用户"),
                                "unique_id": user_info.get("unique_id") or user_info.get("short_id") or "未知",
                                "avatar_thumb": {
                                    "url_list": avatar_urls
                                }
                            },
                            "aweme_id": str(rc.get("aweme_id") or vid)
                        })
                    entity["comments"]["list"] = comment_list

                if vid in captured_short_links:
                    entity["note"]["shareUrl"] = captured_short_links[vid]

                processed_videos.append(entity)

            # 存盘写入
            if processed_videos:
                save_to_json(processed_videos, filename=filename)
                return True
            else:
                print("未匹配或提取到有效的视频或评论数据。")
                return False

        except Exception as e:
            print(f"运行发生异常错误: {e}")
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                os.makedirs("screenshots", exist_ok=True)
                screenshot_path = f"screenshots/error_{name}_{timestamp}.png"
                await page.screenshot(path=screenshot_path)
                print(f"出错时已自动保存异常截图: {screenshot_path}")
            except Exception as se:
                print(f"保存异常截图失败: {se}")
            return False
        finally:
            if popup_monitor_task:
                popup_monitor_task.cancel()
            if tutorial_monitor_task:
                tutorial_monitor_task.cancel()
            await asyncio.sleep(2.0)
            if 'context' in locals() and context:
                await context.close()


# ------------------------------------------------------------------
# 6. 入口主函数
# ------------------------------------------------------------------

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="抖音博主数据爬取脚本")
    parser.add_argument("--blogger", default=None, help="指定爬取的博主姓名 (如果未指定，默认爬取所有配置的博主)")
    parser.add_argument("--max-videos", type=int, default=5, help="每个博主爬取的最大视频数")
    parser.add_argument("--url", default=None, help="指定爬取的博主主页链接")
    parser.add_argument("--headless", default="true", help="是否无头模式 ('true' 或 'false')")
    args = parser.parse_args()

    # 如果传入了 url 和 blogger 名字，直接组装使用，无需读取 saved_links.json
    if args.url and args.blogger:
        links = [{
            "category": "自定义",
            "name": args.blogger,
            "id": args.blogger,
            "url": args.url
        }]
    else:
        links = load_links()
        if not links:
            print("未在 saved_links.json 中找到任何可用链接，或刚才已为您自动初始化空模板。请检查并配置后重试。")
            sys.exit(1)
            
        if args.blogger:
            links = [link for link in links if link['name'] == args.blogger]
            if not links:
                print(f"未在 saved_links.json 中找到名字为 [{args.blogger}] 的博主，请检查配置。")
                sys.exit(1)

    print(f"共读取到 {len(links)} 个目标博主账号：")
    for idx, link in enumerate(links):
        print(f"[{idx + 1}] 分类: {link['category']} | 姓名: {link['name']} | ID/链接: {link['id']}")
        
    os.makedirs("screenshots", exist_ok=True)
    
    results = []
    
    # 确定无头模式
    headless_val = (args.headless.lower() == "true")
    
    for idx, link in enumerate(links):
        if "示例链接" in link['url'] or not link['url']:
            print(f"\n跳过未正确配置的占位模板博主: [{link['name']}]")
            continue
            
        print(f"\n==================================================")
        print(f"正在启动爬取任务 [{idx + 1}/{len(links)}]：分类 [{link['category']}] | 博主 [{link['name']}]")
        print(f"==================================================")
        
        filename = os.path.join(ROOT_DIR, "data", "raw", link['name'], "douyin_data.json")
        success = await collect_douyin_data(link['url'], link['name'], max_videos=args.max_videos, filename=filename, headless=headless_val)
        results.append({
            "name": link['name'],
            "url": link['url'],
            "success": success,
            "filename": filename
        })
        
        if idx < len(links) - 1:
            wait_time = random.uniform(5.0, 10.0)
            print(f"\n博主任务切换，随机等待 {wait_time:.1f} 秒后开始下一个博主...")
            await asyncio.sleep(wait_time)
        
    print("\n================ 自动化爬取任务结束 ================")
    print("各博主抓取结果汇总：")
    for r in results:
        status = f"成功 (数据已合并写入 {r['filename']})" if r['success'] else "失败 (请检查控制台或 screenshots/)"
        print(f"- {r['name']}: {status}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已被用户强制中断。")
        sys.exit(0)
