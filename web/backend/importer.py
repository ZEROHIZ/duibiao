"""
数据导入模块 (importer.py)
核心职责：扫描 data/ 目录下的 [博主名]_analysis.json，提取分析数据并存入 SQLite 数据库。
支持从 [博主名]_notes_details.json 读取正文及评论；如果详情不存在，则从 analysis.json 的 notes/top10 中降级恢复。
"""

import os
import sys
import json
import re
from datetime import datetime
from collections import Counter

# 引入本级 database 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_db_connection, init_db

# 确定路径
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "data")


# ----------------------------------------------------------
# 移植自 deep_analyze.py 的确定性数据计算逻辑
# ----------------------------------------------------------

def extract_title_patterns(titles):
    patterns = {
        "数字型": r"\d+",
        "疑问型": r"[？?]|怎么|如何|为什么|什么",
        "感叹型": r"[！!]|绝了|太|真的|居然|竟然",
        "教程型": r"教程|手把手|保姆级|步骤|方法|攻略",
        "列表型": r"合集|盘点|推荐|必备|top|榜",
        "对比型": r"vs|对比|区别|差异|还是",
        "故事型": r"我|亲身|经历|踩坑|分享|心得",
        "悬念型": r"\.\.\.|…|竟然|没想到|万万|千万",
    }
    results = {}
    for pattern_name, regex in patterns.items():
        count = sum(1 for t in titles if re.search(regex, t, re.IGNORECASE))
        if count > 0:
            pct = round(count / len(titles) * 100, 1)
            examples = [t for t in titles if re.search(regex, t, re.IGNORECASE)][:3]
            results[pattern_name] = {"count": count, "pct": pct, "examples": examples}
    return results

def extract_emoji_patterns(descs):
    emoji_pattern = re.compile(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        r"\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        r"\U00002600-\U000026FF]+"
    )
    emoji_counter = Counter()
    notes_with_emoji = 0
    for desc in descs:
        if not desc:
            continue
        emojis = emoji_pattern.findall(desc)
        if emojis:
            notes_with_emoji += 1
            for e in emojis:
                for char in e:
                    emoji_counter[char] += 1
    return {
        "notes_with_emoji": notes_with_emoji,
        "total_notes": len(descs),
        "emoji_usage_pct": round(notes_with_emoji / len(descs) * 100, 1) if descs else 0,
        "top_emojis": emoji_counter.most_common(10),
    }

def extract_cta_patterns(descs):
    cta_patterns = {
        "关注引导": [r"关注", r"点个关注", r"记得关注"],
        "收藏引导": [r"收藏", r"先收藏", r"码住", r"mark"],
        "点赞引导": [r"点赞", r"双击", r"给个赞"],
        "评论引导": [r"评论", r"留言", r"告诉我", r"你们觉得", r"欢迎讨论"],
        "转发引导": [r"转发", r"分享给"],
        "私信引导": [r"私信", r"私我", r"后台回复", r"滴滴"],
    }
    results = {}
    for cta_type, regexes in cta_patterns.items():
        combined = "|".join(regexes)
        count = sum(1 for d in descs if d and re.search(combined, d))
        if count > 0:
            pct = round(count / len(descs) * 100, 1) if descs else 0
            results[cta_type] = {"count": count, "pct": pct}
    return results

def analyze_content_structure(descs):
    results = {
        "avg_length": 0,
        "short_count": 0,
        "medium_count": 0,
        "long_count": 0,
        "has_list_count": 0,
        "has_number_heading": 0,
    }
    lengths = []
    for desc in descs:
        if not desc:
            continue
        length = len(desc)
        lengths.append(length)
        if length < 200:
            results["short_count"] += 1
        elif length < 500:
            results["medium_count"] += 1
        else:
            results["long_count"] += 1

        if re.search(r"^[\s]*[\-•●]\s", desc, re.MULTILINE):
            results["has_list_count"] += 1
        if re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]|[1-9][.、]", desc):
            results["has_number_heading"] += 1

    results["avg_length"] = round(sum(lengths) / len(lengths)) if lengths else 0
    return results

def detect_posting_frequency(notes):
    timestamps = sorted([n["time"] for n in notes if n.get("time", 0) > 0])
    if len(timestamps) < 2:
        return {"pattern": "数据不足", "avg_days_between": 0}

    divisor = (1000 * 86400) if timestamps[0] > 1e11 else 86400
    intervals = []
    for i in range(1, len(timestamps)):
        try:
            diff = (timestamps[i] - timestamps[i - 1])
            days = diff / divisor
            if 0 < days < 365:
                intervals.append(days)
        except (TypeError, ValueError):
            continue

    if not intervals:
        return {"pattern": "无法计算", "avg_days_between": 0}

    avg_days = round(sum(intervals) / len(intervals), 1)
    if avg_days <= 1:
        pattern = "日更"
    elif avg_days <= 3:
        pattern = "高频（2-3天/条）"
    elif avg_days <= 7:
        pattern = "周更"
    elif avg_days <= 14:
        pattern = "双周更"
    else:
        pattern = f"低频（约{int(avg_days)}天/条）"

    return {"pattern": pattern, "avg_days_between": avg_days}

def find_growth_pattern(notes):
    if len(notes) < 6:
        return None

    time_sorted = sorted([n for n in notes if n.get("time", 0) > 0], key=lambda x: x["time"])
    if len(time_sorted) < 6:
        return None

    mid = len(time_sorted) // 2
    early = time_sorted[:mid]
    recent = time_sorted[mid:]

    early_cats = Counter(n.get("category", "其他") for n in early)
    recent_cats = Counter(n.get("category", "其他") for n in recent)

    all_cats = set(list(early_cats.keys()) + list(recent_cats.keys()))
    changes = {}
    for cat in all_cats:
        e_pct = round(early_cats.get(cat, 0) / len(early) * 100, 1) if early else 0
        r_pct = round(recent_cats.get(cat, 0) / len(recent) * 100, 1) if recent else 0
        changes[cat] = {"early_pct": e_pct, "recent_pct": r_pct, "delta": round(r_pct - e_pct, 1)}

    return {
        "early_count": len(early),
        "recent_count": len(recent),
        "category_shifts": changes,
    }


# ----------------------------------------------------------
# 数据导入主逻辑
# ----------------------------------------------------------

def import_blogger_file(analysis_path):
    """读取指定博主分析 json 文件，导入到 SQLite 数据库中"""
    if not os.path.exists(analysis_path):
        print(f"[Importer] Error: File {analysis_path} not found.")
        return

    # 从文件名推导博主姓名（例如 "小A_analysis.json" -> "小A"）
    filename = os.path.basename(analysis_path)
    if not filename.endswith("_analysis.json"):
        print(f"[Importer] Skip: {filename} does not match [Blogger]_analysis.json pattern.")
        return
    blogger_name = filename.replace("_analysis.json", "")

    print(f"[Importer] Starting import for blogger: {blogger_name}...")

    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    stats = data.get("stats", {})
    if not stats:
        print(f"[Importer] Warning: No stats found in {filename}. Skipping.")
        return

    # 1. 寻找详情文件，以提取最完整的正文 desc 和热门评论
    details_path = os.path.join(DATA_DIR, f"{blogger_name}_notes_details.json")
    details_map = {}
    if os.path.exists(details_path):
        print(f"[Importer] Found details file: {details_path}, extracting full texts and comments.")
        try:
            with open(details_path, "r", encoding="utf-8") as df:
                details_list = json.load(df)
            for item in details_list:
                if "_error" in item:
                    continue
                # 获取 note
                if "note" in item and isinstance(item.get("note"), dict):
                    note = item["note"]
                else:
                    note = item.get("data", {}).get("note", item)
                
                nid = note.get("noteId", item.get("_feed_id", ""))
                comments_data = item.get("comments", {})
                comment_list = comments_data.get("list", []) if isinstance(comments_data, dict) else []
                
                details_map[nid] = {
                    "desc": note.get("desc", ""),
                    "comments": comment_list
                }
        except Exception as e:
            print(f"[Importer] Error reading details file: {e}")

    conn = get_db_connection()
    cursor = conn.cursor()

    # 2. 插入/更新博主主表
    cursor.execute("""
    INSERT INTO bloggers (
        name, home_url, total_notes, video_count, normal_count, 
        avg_likes, avg_collects, avg_comments, 
        total_likes, total_collects, total_comments
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(name) DO UPDATE SET
        total_notes=excluded.total_notes,
        video_count=excluded.video_count,
        normal_count=excluded.normal_count,
        avg_likes=excluded.avg_likes,
        avg_collects=excluded.avg_collects,
        avg_comments=excluded.avg_comments,
        total_likes=excluded.total_likes,
        total_collects=excluded.total_collects,
        total_comments=excluded.total_comments
    """, (
        blogger_name,
        "",  # 默认初始为空字符串，后续由用户在前端配置主页链接
        stats.get("total", 0),
        stats.get("video_count", 0),
        stats.get("normal_count", 0),
        stats.get("avg_likes", 0),
        stats.get("avg_collects", 0),
        stats.get("avg_comments", 0),
        stats.get("total_likes", 0),
        stats.get("total_collects", 0),
        stats.get("total_comments", 0)
    ))

    # 获取博主数据库自增 id
    cursor.execute("SELECT id FROM bloggers WHERE name = ?;", (blogger_name,))
    blogger_id = cursor.fetchone()["id"]

    # 3. 整理笔记并插入/更新笔记详情表
    notes = data.get("notes", [])
    top10 = data.get("top10", [])
    top10_map = {n["id"]: n for n in top10}

    # 汇总计算所用的描述列表
    all_descs = []
    
    for note in notes:
        nid = note["id"]
        # 获取最完整的正文 description
        full_desc = ""
        comments = []

        if nid in details_map:
            full_desc = details_map[nid]["desc"]
            comments = details_map[nid]["comments"]
        elif nid in top10_map:
            full_desc = top10_map[nid].get("desc", "")
            comments = top10_map[nid].get("comment_list", [])
        else:
            full_desc = note.get("title", "") # 降级使用标题

        if full_desc:
            all_descs.append(full_desc)

        # 格式化日期格式
        pub_at = ""
        timestamp = note.get("time", 0)
        if timestamp > 0:
            if timestamp > 1e11:  # 毫秒转秒
                timestamp = timestamp / 1000
            pub_at = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
        INSERT INTO blogger_notes (
            id, blogger_id, title, desc, type, 
            likes, collects, comments, shares, category, 
            tags_json, comments_json, published_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            blogger_id=excluded.blogger_id,
            title=excluded.title,
            desc=excluded.desc,
            type=excluded.type,
            likes=excluded.likes,
            collects=excluded.collects,
            comments=excluded.comments,
            shares=excluded.shares,
            category=excluded.category,
            tags_json=excluded.tags_json,
            comments_json=excluded.comments_json,
            published_at=excluded.published_at
        """, (
            nid,
            blogger_id,
            note.get("title", ""),
            full_desc,
            note.get("type", "normal"),
            note.get("likes", 0),
            note.get("collects", 0),
            note.get("comments_count", 0),
            note.get("shares", 0),
            note.get("category", "其他"),
            json.dumps(note.get("tags", []), ensure_ascii=False),
            json.dumps(comments, ensure_ascii=False),
            pub_at
        ))

    # 4. 执行 Phase 3.5 确定性分析计算并存入蒸馏主表
    titles = [n["title"] for n in notes if n.get("title")]
    
    title_patterns = extract_title_patterns(titles) if titles else {}
    emoji_info = extract_emoji_patterns(all_descs) if all_descs else {}
    cta_info = extract_cta_patterns(all_descs) if all_descs else {}
    structure_info = analyze_content_structure(all_descs) if all_descs else {}
    frequency_info = detect_posting_frequency(notes) if notes else {}
    growth_info = find_growth_pattern(notes) if notes else None

    cursor.execute("""
    INSERT INTO blogger_distilled (
        blogger_id, category_stats_json, tag_freq_json, title_patterns_json,
        emoji_info_json, cta_info_json, structure_info_json, frequency_info_json,
        growth_info_json, opinion_candidates_json, writing_structure_json, value_words_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(blogger_id) DO UPDATE SET
        category_stats_json=excluded.category_stats_json,
        tag_freq_json=excluded.tag_freq_json,
        title_patterns_json=excluded.title_patterns_json,
        emoji_info_json=excluded.emoji_info_json,
        cta_info_json=excluded.cta_info_json,
        structure_info_json=excluded.structure_info_json,
        frequency_info_json=excluded.frequency_info_json,
        growth_info_json=excluded.growth_info_json,
        opinion_candidates_json=excluded.opinion_candidates_json,
        writing_structure_json=excluded.writing_structure_json,
        value_words_json=excluded.value_words_json
    """, (
        blogger_id,
        json.dumps(data.get("category_stats", {}), ensure_ascii=False),
        json.dumps(data.get("tag_freq", []), ensure_ascii=False),
        json.dumps(title_patterns, ensure_ascii=False),
        json.dumps(emoji_info, ensure_ascii=False),
        json.dumps(cta_info, ensure_ascii=False),
        json.dumps(structure_info, ensure_ascii=False),
        json.dumps(frequency_info, ensure_ascii=False),
        json.dumps(growth_info, ensure_ascii=False) if growth_info else "{}",
        json.dumps(data.get("opinion_candidates", []), ensure_ascii=False),
        json.dumps(data.get("writing_structure", {}), ensure_ascii=False),
        json.dumps(data.get("value_words", []), ensure_ascii=False)
    ))

    conn.commit()
    conn.close()
    print(f"[Importer] Successfully imported blogger data for '{blogger_name}' into SQLite.")


def run_full_import():
    """扫描 data/ 目录下一键导入所有博主数据"""
    # 确保数据库表已建好
    init_db()

    # 扫描 data 目录
    if not os.path.exists(DATA_DIR):
        print(f"[Importer] Data dir {DATA_DIR} not found. Creating it.")
        os.makedirs(DATA_DIR, exist_ok=True)
        return

    imported_any = False
    for filename in os.listdir(DATA_DIR):
        if filename.endswith("_analysis.json"):
            filepath = os.path.join(DATA_DIR, filename)
            import_blogger_file(filepath)
            imported_any = True

    if not imported_any:
        print("[Importer] No blogger analysis JSON files found in data/ to import.")

if __name__ == "__main__":
    run_full_import()
