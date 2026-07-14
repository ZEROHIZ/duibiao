"""
scripts/pipeline.py

抖音博主数据采集处理一键式流水线。
核心职责：串联并执行 [爬取] -> [格式转换与Whisper转录] -> [SQLite数据导入] 整个流水线。
支持单博主按需执行或全博主定时轮询执行。
"""

import os
import sys

# 强制标准输出与错误流为 UTF-8 编码，防止 Windows 控制台/管道环境下的 GBK 编码报错
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

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

def trigger_agent_cli(blogger):
    """根据 settings 中的授权状态，通过 subprocess 唤醒相应的智能体 CLI 进行自动蒸馏"""
    config_path = os.path.join(ROOT_DIR, "data", "config.json")
    if not os.path.exists(config_path):
        return True # 无配置，跳过
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception as e:
        print(f"[Agent CLI] 读取配置失败: {e}")
        return True
        
    google_token = settings.get("google_access_token", "")
    openai_token = settings.get("openai_access_token", "")
    proxy_url = settings.get("proxy_url", "")
    
    # 判定使用哪个智能体 CLI
    agent_cmd = None
    env_vars = {}
    
    if google_token:
        agent_cmd = "agy"
        env_vars["GOOGLE_OAUTH_ACCESS_TOKEN"] = google_token
    elif openai_token:
        agent_cmd = "codex"
        env_vars["OPENAI_API_KEY"] = openai_token
    else:
        print("[Agent CLI] 尚未配置任何智能体授权 Token，跳过自动蒸馏步骤。")
        return True
        
    task_file = os.path.join(ROOT_DIR, "output", f"{blogger}_AI蒸馏任务.md")
    if not os.path.exists(task_file):
        # 兼容路径
        task_file = os.path.join(ROOT_DIR, "output", "_过程文件", "原始素材", f"{blogger}_AI蒸馏任务.md")
        
    if not os.path.exists(task_file):
        print(f"[Agent CLI] 未找到蒸馏任务底稿文件: {task_file}，跳过。")
        return True

    google_model = settings.get("google_model", "gemini-2.5-pro")
    openai_model = settings.get("openai_model", "gpt-4o")

    if agent_cmd == "agy":
        cmd = [
            agent_cmd,
            "--dangerously-skip-permissions",
            "--model", google_model,
            "-p",
            f"请加载项目并执行该蒸馏任务中的全部指令，严格按照里面规定的格式和质量红线生成最终报告与文件夹：{task_file}"
        ]
    else:
        cmd = [
            agent_cmd,
            "--dangerously-bypass-approvals-and-sandbox",
            "--model", openai_model,
            "-p",
            f"请加载项目并执行该蒸馏任务中的全部指令，严格按照里面规定的格式和质量红线生成最终报告与文件夹：{task_file}"
        ]
    
    print(f"\n==================================================")
    print(f">>> 唤醒智能体 CLI 自动生成报告与 Skill 目录: {agent_cmd} ({blogger})")
    print(f">>> 命令行: {' '.join(cmd)}")
    if proxy_url:
        print(f">>> 注入代理服务器: {proxy_url}")
    print(f"==================================================")
    
    env = os.environ.copy()
    env.update(env_vars)
    if proxy_url:
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        
    try:
        # 同步运行并实时打印智能体输出，方便在后台队列日志中显示
        is_windows = os.name == "nt"
        process = subprocess.Popen(
            cmd,
            shell=is_windows,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=ROOT_DIR
        )
        
        while True:
            line = process.stdout.readline()
            if not line:
                break
            print(f"[{agent_cmd}] {line}", end="")
            sys.stdout.flush()
            
        process.wait()
        if process.returncode != 0:
            print(f"\n❌ 智能体 {agent_cmd} 运行失败，退出码: {process.returncode}")
            return False
            
        print(f"✅ 智能体 {agent_cmd} 自动蒸馏成功结束。")
        return True
    except Exception as e:
        print(f"❌ 运行智能体 CLI 时发生异常错误: {e}")
        return False

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

    # 检查数据库中该博主的名称是否在爬取过程中被自动更新了
    db_path = os.path.join(ROOT_DIR, "data", "distiller.db")
    if os.path.exists(db_path) and url:
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            r = conn.execute("SELECT name FROM bloggers WHERE home_url = ?", (url,)).fetchone()
            conn.close()
            if r and r[0] != blogger:
                new_blogger_name = r[0]
                print(f"[Pipeline] 检测到博主名称在爬虫运行中被更新: {blogger} -> {new_blogger_name}")
                # 如果被更新了，我们需要将刚才 crawler 保存的原始文件夹重命名为新名字，以便后续步骤正确读取！
                old_raw_dir = os.path.join(ROOT_DIR, "data", "raw", blogger)
                new_raw_dir = os.path.join(ROOT_DIR, "data", "raw", new_blogger_name)
                # 只有当旧文件夹存在，且新文件夹不存在时才重命名（若新文件夹已存在，可能是历史残留，我们做个兼容）
                if os.path.exists(old_raw_dir) and not os.path.exists(new_raw_dir):
                    os.rename(old_raw_dir, new_raw_dir)
                    print(f"[Pipeline] 重命名原始数据目录: {old_raw_dir} -> {new_raw_dir}")
                
                blogger = new_blogger_name
                # 更新 raw_data_path 变量指向重命名后的新 JSON 文件路径
                raw_data_path = os.path.join(ROOT_DIR, "data", "raw", blogger, "douyin_data.json")
        except Exception as e:
            print(f"[Pipeline] 检查博主名称更新失败: {e}")

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

    # 2.5. 数据分析与特征提取（读取 notes_details 生成 analysis.json 供导入器使用）
    analyze_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "scripts", "analyze.py"),
        processed_data_path,
        "-o", os.path.join(ROOT_DIR, "data")
    ]
    if not run_step(analyze_cmd, f"2.5. 数据深度分析特征提取 ({blogger})"):
        return False

    # 2.7. 认知分析与 AI 蒸馏底稿任务生成（生成 AI 蒸馏任务.md 等过程文件）
    deep_analyze_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "scripts", "deep_analyze.py"),
        os.path.join(ROOT_DIR, "data", f"{blogger}_analysis.json"),
        blogger,
        "-o", os.path.join(ROOT_DIR, "output"),
        "--details", processed_data_path
    ]
    if not run_step(deep_analyze_cmd, f"2.7. AI 蒸馏底稿任务生成 ({blogger})"):
        return False

    # 3. 增量导入 SQLite 数据库
    importer_cmd = [
        PYTHON_EXE,
        os.path.join(ROOT_DIR, "web", "backend", "importer.py"),
        "--blogger", blogger
    ]
    if not run_step(importer_cmd, f"3. 数据导入 SQLite 库 ({blogger})"):
        return False

    # 3.5 自动唤醒智能体 CLI 生成物理报告与创作指南
    if not trigger_agent_cli(blogger):
        print(f"⚠️ [Pipeline] 智能体自动拆解失败，但原始数据已入库，您可以手动拉起 Agent。")

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
