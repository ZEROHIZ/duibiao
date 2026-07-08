"""
scripts/pipeline.py

抖音博主数据采集处理一键式流水线。
核心职责：串联并执行 [爬取] -> [格式转换与Whisper转录] -> [SQLite数据导入] 整个流水线。
支持单博主按需执行或全博主定时轮询执行。
"""

import os
import sys
import argparse
import subprocess
import json

# 确定项目根目录与 Python 执行器路径 (三层目录结构)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON_EXE = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")

# 如果没有 venv，则回退到系统 python
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable

def load_all_bloggers():
    """读取 saved_links.json 获得所有配置的博主姓名列表"""
    filepath = os.path.join(ROOT_DIR, "saved_links.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            bloggers = []
            for category, items in data.items():
                for item in items:
                    name = item.get("name")
                    if name and "示例" not in name:
                        bloggers.append(name)
            return bloggers
    except Exception as e:
        print(f"读取 saved_links.json 失败: {e}")
        return []

def run_step(cmd, description):
    """运行子脚本进程，并实时输出流"""
    print(f"\n==================================================")
    print(f">>> 开始执行阶段: {description}")
    print(f">>> 命令行: {' '.join(cmd)}")
    print(f"==================================================")
    
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    # 开启子进程，并把输出重定向到主进程的标准输出
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

    # 实时打印子进程输出
    while True:
        line = process.stdout.readline()
        if not line:
            break
        print(line, end="")
        sys.stdout.flush()

    process.wait()
    if process.returncode != 0:
        print(f"\n❌ 阶段失败: [{description}] 异常退出，退出码: {process.returncode}")
        return False
    print(f"✅ 阶段成功: [{description}] 已顺利结束。")
    return True

def load_bloggers_from_db():
    """从 SQLite 数据库获取所有配置的博主和主页链接"""
    import sqlite3
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT name, home_url FROM bloggers").fetchall()
        conn.close()
        bloggers = []
        for r in rows:
            name = r["name"]
            url = r["home_url"]
            if name and "示例" not in name:
                bloggers.append({"name": name, "url": url})
        return bloggers
    except Exception as e:
        print(f"读取 SQLite 数据库博主列表失败: {e}")
        return []

def get_blogger_url_from_db(blogger_name):
    """根据博主名称从数据库中查询其个人主页链接"""
    import sqlite3
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        r = conn.execute("SELECT home_url FROM bloggers WHERE name = ?", (blogger_name,)).fetchone()
        conn.close()
        if r:
            return r[0]
    except Exception as e:
        print(f"查询博主 [{blogger_name}] 主页链接失败: {e}")
    return None

def process_single_blogger(blogger, max_videos, whisper_url, url=None, headless="true"):
    """串联执行单个博主的全部处理流程"""
    print(f"\n##################################################")
    print(f" 正在启动博主【{blogger}】的流水线任务 ")
    if url:
        print(f" 监控链接: {url}")
    print(f"##################################################")

    # 1. 爬虫抓取原始数据
    raw_data_path = os.path.join(ROOT_DIR, "data", "raw", blogger, "douyin_data.json")
    crawler_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "scripts", "pachopngjiaoben", "douyin_crawler.py"),
        "--blogger", blogger,
        "--max-videos", str(max_videos)
    ]
    if url:
        crawler_cmd.extend(["--url", url])
    if headless:
        crawler_cmd.extend(["--headless", headless])
        
    if not run_step(crawler_cmd, f"1. 抖音网页数据爬取 ({blogger})"):
        return False

    # 2. 转换及视频数据整理（跳过 Whisper 语音转录，以便快速入库）
    processed_data_path = os.path.join(ROOT_DIR, "data", "processed", f"{blogger}_notes_details.json")
    converter_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "scripts", "pachopngjiaoben", "convert_douyin_notes.py"),
        "-i", raw_data_path,
        "-o", processed_data_path,
        "-b", blogger,
        "--whisper-url", whisper_url,
        "--skip-transcribe"
    ]
    if not run_step(converter_cmd, f"2. 格式转换与快速导入准备 ({blogger})"):
        return False

    # 3. 增量导入 SQLite 数据库
    importer_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "web", "backend", "importer.py"),
        "--blogger", blogger
    ]
    if not run_step(importer_cmd, f"3. 数据导入 SQLite 库 ({blogger})"):
        return False

    print(f"\n🎉 博主【{blogger}】的流水线数据同步完全成功！")
    return True

def main():
    # 优先加载 config.json 的设置作为 CLI 默认值
    config_path = os.path.join(ROOT_DIR, "data", "config.json")
    default_max_videos = 5
    default_whisper_url = "http://192.168.110.30:7211/transcribe"
    default_headless = "true"
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                default_max_videos = config_data.get("max_videos", 5)
                default_whisper_url = config_data.get("whisper_url", "http://192.168.110.30:7211/transcribe")
                default_headless = "true" if config_data.get("headless", True) else "false"
        except Exception as e:
            pass

    parser = argparse.ArgumentParser(description="抖音博主数据更新流水线一键脚本")
    parser.add_argument("--blogger", default=None, help="指定需要更新的单个博主姓名。如果不指定，则更新全部博主")
    parser.add_argument("--max-videos", type=int, default=default_max_videos, help="每次更新抓取的最大视频条数")
    parser.add_argument("--whisper-url", default=default_whisper_url, help="Whisper API地址")
    parser.add_argument("--url", default=None, help="手动指定该博主的个人主页监控链接 (仅当指定单个博主时有效)")
    parser.add_argument("--all", action="store_true", help="强制更新所有博主")
    parser.add_argument("--headless", default=default_headless, help="是否无头模式 ('true' 或 'false')")
    args = parser.parse_args()

    # 决定运行的博主列表与对应的 URL
    bloggers_to_run = []
    
    if args.blogger:
        # 如果是单博主模式
        url = args.url or get_blogger_url_from_db(args.blogger)
        if not url:
            # 兼容回退读取 saved_links.json
            links = load_all_bloggers()
            # 这里 load_all_bloggers 会返回博主名字列表，但如果是 saved_links.json，我们需要去取对应的 url
            # 为了兼容性，如果没有从数据库找到，我们回退读取 saved_links.json 来寻找
            filepath = os.path.join(ROOT_DIR, "saved_links.json")
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for category, items in data.items():
                            for item in items:
                                if item.get("name") == args.blogger:
                                    url = item.get("url")
                                    break
                except:
                    pass
        bloggers_to_run.append({"name": args.blogger, "url": url})
    else:
        # 如果是全博主模式，优先从 SQLite 加载
        bloggers_to_run = load_bloggers_from_db()
        if not bloggers_to_run:
            # 回退读取 saved_links.json
            filepath = os.path.join(ROOT_DIR, "saved_links.json")
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for category, items in data.items():
                            for item in items:
                                name = item.get("name")
                                url = item.get("url")
                                if name and "示例" not in name:
                                    bloggers_to_run.append({"name": name, "url": url})
                except:
                    pass
                    
        if not bloggers_to_run:
            print("未在数据库或 saved_links.json 中找到任何有效的博主配置。")
            sys.exit(1)

    print(f"流水线准备就绪。计划更新以下博主: {', '.join(b['name'] for b in bloggers_to_run)}")
    print(f"每个博主上限抓取 {args.max_videos} 条视频，无头模式={args.headless}，Whisper API: {args.whisper_url}")

    success_count = 0
    for b in bloggers_to_run:
        blogger_name = b["name"]
        blogger_url = b["url"]
        try:
            if process_single_blogger(blogger_name, args.max_videos, args.whisper_url, url=blogger_url, headless=args.headless):
                success_count += 1
        except Exception as e:
            print(f"博主【{blogger_name}】在流水线运行期间发生未捕获异常: {e}")

    print(f"\n==================================================")
    print(f"流水线同步运行汇总: {success_count}/{len(bloggers_to_run)} 成功")
    print(f"==================================================")
    if success_count < len(bloggers_to_run):
        sys.exit(1)

if __name__ == "__main__":
    main()
