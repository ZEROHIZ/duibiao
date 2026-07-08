# -*- coding: utf-8 -*-
"""
scripts/utils/recalculate.py
核心职责：从 SQLite 数据库的明细表 blogger_notes 中重新计算所有博主统计指标，
          并同步更新 bloggers 和 blogger_distilled 表，从而彻底抛弃对本地 _analysis.json 的二次读取依赖。
"""

import os
import sqlite3
import json
import re
from collections import Counter
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT_DIR, "data", "distiller.db")

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
        except:
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

def extract_opinion_sentences(notes):
    opinion_keywords = {
        "判断词": ["我觉得", "我认为", "其实", "本质上", "说白了", "归根结底", "核心是", "关键在于", "真正的", "最重要的"],
        "转折": ["但其实", "然而", "不是…而是", "不是...而是", "与其", "看起来", "实际上", "大家都说", "表面上"],
        "总结": ["所以", "因此", "这说明", "这意味着", "一句话概括", "总结一下", "换句话说"],
    }
    candidates = []
    for note in notes:
        desc = note.get("desc") or ""
        if not desc or desc.startswith("http"):
            continue
        sentences = re.split(r"[。！？\n]", desc)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 8:
                continue
            for match_type, keywords in opinion_keywords.items():
                if any(kw in sent for kw in keywords):
                    candidates.append({
                        "sentence": sent[:120],
                        "source_note_id": note.get("id", ""),
                        "source_title": note.get("title", "")[:30],
                        "source_likes": str(note.get("likes", 0)),
                        "match_type": match_type,
                    })
                    break
    return candidates

def analyze_writing_structure(notes):
    opening_patterns = {
        "故事开头": ["那天", "记得", "有一次", "上周", "上个月", "去年", "小时候", "从前"],
        "反问开头": ["你有没有", "你是不是", "为什么", "凭什么", "难道", "真的吗", "？"],
        "数据开头": ["%", "万", "个", "次", "元", "块", "倍", "调查", "数据"],
        "自嘲开头": ["我这个", "作为一个", "承认", "说实话", "坦白"],
        "观点直抛": ["我觉得", "我认为", "其实", "本质上", "说白了"],
    }
    ending_patterns = {
        "金句收尾": ["就是", "才是", "而已", "罢了", "本质", "归根"],
        "行动号召": ["关注", "收藏", "点赞", "试试", "去做", "行动"],
        "开放提问": ["你呢", "你觉得", "评论区", "留言", "告诉我", "你们"],
        "总结回顾": ["总结", "所以", "因此", "最后", "希望"],
    }
    opening_counts = {k: 0 for k in opening_patterns}
    ending_counts = {k: 0 for k in ending_patterns}

    for note in notes:
        desc = note.get("desc") or ""
        if not desc or desc.startswith("http"):
            continue
        head = desc[:50]
        tail = desc[-50:]
        for ptype, keywords in opening_patterns.items():
            if any(kw in head for kw in keywords):
                opening_counts[ptype] += 1
                break
        for ptype, keywords in ending_patterns.items():
            if any(kw in tail for kw in keywords):
                ending_counts[ptype] += 1
                break

    return {
        "opening_types": {k: v for k, v in opening_counts.items() if v > 0},
        "ending_types": {k: v for k, v in ending_counts.items() if v > 0},
    }

def extract_value_words(notes):
    stop_phrases = {"时候", "自己", "觉得", "一个", "一些", "一下", "一样", "一直", "一起",
                    "可以", "没有", "什么", "这个", "那个", "这样", "那样", "如果", "因为",
                    "所以", "但是", "然后", "还是", "已经", "非常", "真的", "感觉", "知道",
                    "现在", "时间", "东西", "事情", "问题", "方法", "内容", "大家", "我们",
                    "他们", "她们", "你们", "很多", "一点", "有点", "有些", "其实", "只是"}
    word_counter = Counter()
    for note in notes:
        desc = note.get("desc") or ""
        if not desc or desc.startswith("http"):
            continue
        desc = re.sub(r"#[^#\s]+?(?:\[.*?\])?#?", "", desc)
        tokens = re.split(r"[\s，。！？、；：""''【】《》\(\)（）\[\]…—\-/\\|]", desc)
        for token in tokens:
            token = token.strip()
            if 2 <= len(token) <= 4:
                if not re.match(r"^[\u4e00-\u9fff]+$", token):
                    continue
                if token in stop_phrases:
                    continue
                word_counter[token] += 1
    return [{"word": w, "count": c} for w, c in word_counter.most_common(15)]

def recalculate_blogger_stats(blogger_name):
    """从数据库的明细表中重新计算该博主的各项分析指标"""
    if not os.path.exists(DB_PATH):
        print(f"[Recalculator] Database path not found: {DB_PATH}")
        return False

    print(f"[Recalculator] Starting recalculation for blogger '{blogger_name}'...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 查找博主 ID
    cursor.execute("SELECT id FROM bloggers WHERE name = ?;", (blogger_name,))
    blogger_row = cursor.fetchone()
    if not blogger_row:
        print(f"[Recalculator] Blogger '{blogger_name}' not found in database.")
        conn.close()
        return False
    blogger_id = blogger_row["id"]

    # 2. 读取所有的笔记明细数据
    cursor.execute("SELECT * FROM blogger_notes WHERE blogger_id = ?;", (blogger_id,))
    notes_rows = cursor.fetchall()
    if not notes_rows:
        print(f"[Recalculator] No notes found for blogger '{blogger_name}' in blogger_notes.")
        conn.close()
        return False

    notes = []
    all_descs = []
    all_tags = []
    total_likes = 0
    total_collects = 0
    total_comments = 0
    video_count = 0
    normal_count = 0

    for nr in notes_rows:
        # 获取 tags_json
        tags = []
        try:
            if nr["tags_json"]:
                tags = json.loads(nr["tags_json"])
        except:
            pass
        all_tags.extend(tags)

        # 获取 comments_json
        comments = []
        try:
            if nr["comments_json"]:
                comments = json.loads(nr["comments_json"])
        except:
            pass

        # 整理基础属性
        notes.append({
            "id": nr["id"],
            "title": nr["title"],
            "desc": nr["desc"],
            "type": nr["type"],
            "likes": nr["likes"] or 0,
            "collects": nr["collects"] or 0,
            "comments_count": nr["comments"] or 0,
            "shares": nr["shares"] or 0,
            "category": nr["category"] or "环境" if nr["category"] else "其他", # 保留分类
            "time": 0,  # 尝试解析发布时间
            "tags": tags,
            "comment_list": comments
        })

        desc = nr["desc"] or ""
        if desc and not (desc.startswith("http://") or desc.startswith("https://")):
            all_descs.append(desc)

        total_likes += (nr["likes"] or 0)
        total_collects += (nr["collects"] or 0)
        total_comments += (nr["comments"] or 0)

        if nr["type"] == "video":
            video_count += 1
        else:
            normal_count += 1

        # 解析日期为秒时间戳
        if nr["published_at"]:
            try:
                dt = datetime.strptime(nr["published_at"], "%Y-%m-%d %H:%M:%S")
                notes[-1]["time"] = int(dt.timestamp())
            except:
                pass

    total_notes = len(notes)
    avg_likes = total_likes // total_notes if total_notes else 0
    avg_collects = total_collects // total_notes if total_notes else 0
    avg_comments = total_comments // total_notes if total_notes else 0

    # 3. 更新博主主表
    cursor.execute("""
    UPDATE bloggers SET
        total_notes = ?, video_count = ?, normal_count = ?,
        avg_likes = ?, avg_collects = ?, avg_comments = ?,
        total_likes = ?, total_collects = ?, total_comments = ?
    WHERE id = ?;
    """, (
        total_notes, video_count, normal_count,
        avg_likes, avg_collects, avg_comments,
        total_likes, total_collects, total_comments,
        blogger_id
    ))

    # 4. 重新计算分析指标
    category_dist = Counter(n["category"] for n in notes)
    category_stats = {}
    for cat, count in category_dist.most_common():
        cat_notes = [n for n in notes if n["category"] == cat]
        cat_likes = sum(n["likes"] for n in cat_notes)
        # 代表作选取赞数最高的一个
        sorted_cat_notes = sorted(cat_notes, key=lambda x: x["likes"], reverse=True)
        top_note = sorted_cat_notes[0]["title"] if sorted_cat_notes else ""
        category_stats[cat] = {
            "count": count,
            "pct": round(count / total_notes * 100, 1) if total_notes else 0,
            "avg_likes": cat_likes // len(cat_notes) if cat_notes else 0,
            "top_note": top_note,
        }

    tag_freq = Counter(all_tags).most_common(20)
    title_patterns = extract_title_patterns([n["title"] for n in notes if n["title"]])
    emoji_info = extract_emoji_patterns(all_descs)
    cta_info = extract_cta_patterns(all_descs)
    structure_info = analyze_content_structure(all_descs)
    frequency_info = detect_posting_frequency(notes)
    growth_info = find_growth_pattern(notes)
    opinion_candidates = extract_opinion_sentences(notes)
    writing_structure = analyze_writing_structure(notes)
    value_words = extract_value_words(notes)

    # 5. 更新蒸馏表
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
        value_words_json=excluded.value_words_json,
        distilled_at=CURRENT_TIMESTAMP
    """, (
        blogger_id,
        json.dumps(category_stats, ensure_ascii=False),
        json.dumps(tag_freq, ensure_ascii=False),
        json.dumps(title_patterns, ensure_ascii=False),
        json.dumps(emoji_info, ensure_ascii=False),
        json.dumps(cta_info, ensure_ascii=False),
        json.dumps(structure_info, ensure_ascii=False),
        json.dumps(frequency_info, ensure_ascii=False),
        json.dumps(growth_info, ensure_ascii=False) if growth_info else "{}",
        json.dumps(opinion_candidates, ensure_ascii=False),
        json.dumps(writing_structure, ensure_ascii=False),
        json.dumps(value_words, ensure_ascii=False)
    ))

    conn.commit()
    conn.close()
    print(f"[Recalculator] Successfully recalculated stats for '{blogger_name}'. Database synced.")

    # 6. 同步更新本地 data/<博主名>_analysis.json 文件，以维持文件与数据库高度一致
    # 构造 top10 数据结构（附带简化版评论列表）
    top10 = []
    for n in notes[:10]:
        top_comments = []
        # 查询评论并提取精简信息
        comments_list = n.get("comment_list") or []
        for c in comments_list[:5]:
            speaker = c.get("speaker") or c.get("userInfo", {}).get("nickname", "?")
            is_author = c.get("is_author")
            if is_author is None:
                is_author = "is_author" in str(c.get("showTags", []))
            comment_info = {
                "content": c.get("content", "")[:100],
                "likes": c.get("likeCount", c.get("like_count", "0")),
                "user": speaker,
                "is_author": bool(is_author),
                "sub_comments": [],
            }
            sub_list = c.get("subComments") or c.get("sub_comments") or []
            for sc in sub_list[:2]:
                sc_speaker = sc.get("speaker") or sc.get("userInfo", {}).get("nickname", "?")
                sc_is_author = sc.get("is_author")
                if sc_is_author is None:
                    sc_is_author = "is_author" in str(sc.get("showTags", []))
                sub_info = {
                    "content": sc.get("content", "")[:80],
                    "user": sc_speaker,
                    "is_author": bool(sc_is_author),
                }
                if sc.get("reply_to"):
                    sub_info["reply_to"] = sc["reply_to"]
                comment_info["sub_comments"].append(sub_info)
            top_comments.append(comment_info)
        
        top10.append({
            "id": n["id"],
            "title": n["title"],
            "desc": n["desc"],
            "type": n["type"],
            "likes": n["likes"],
            "likes_raw": str(n["likes"]),
            "collects": n["collects"],
            "collects_raw": str(n["collects"]),
            "comments_count": n["comments_count"],
            "comments_raw": str(n["comments_count"]),
            "shares": n["shares"],
            "tags": n["tags"],
            "category": n["category"],
            "time": n["time"],
            "comment_list": top_comments,
        })

    # 写入 analysis.json
    analysis_path = os.path.join(ROOT_DIR, "data", f"{blogger_name}_analysis.json")
    stats = {
        "total": total_notes,
        "errors": 0,
        "restricted": 0,
        "video_count": video_count,
        "normal_count": normal_count,
        "total_likes": total_likes,
        "total_collects": total_collects,
        "total_comments": total_comments,
        "avg_likes": avg_likes,
        "avg_collects": avg_collects,
        "avg_comments": avg_comments,
    }
    
    # 构造精简笔记明细，供分析文件使用
    save_notes = []
    for n in notes:
        save_notes.append({
            "id": n["id"],
            "title": n["title"],
            "type": n["type"],
            "likes": n["likes"],
            "likes_raw": str(n["likes"]),
            "collects": n["collects"],
            "collects_raw": str(n["collects"]),
            "comments_count": n["comments_count"],
            "comments_raw": str(n["comments_count"]),
            "shares": n["shares"],
            "tags": n["tags"],
            "category": n["category"],
            "time": n["time"],
        })

    save_data = {
        "stats": stats,
        "category_stats": category_stats,
        "tag_freq": tag_freq,
        "top10": top10,
        "comparison": None,
        "errors": [],
        "restricted_notes": [],
        "opinion_candidates": opinion_candidates,
        "opinion_extraction_mode": "full_text" if len(opinion_candidates) < 10 else "script_filtered",
        "writing_structure": writing_structure,
        "value_words": value_words,
        "notes": save_notes,
        "notes_count": len(save_notes)
    }

    try:
        with open(analysis_path, "w", encoding="utf-8") as af:
            json.dump(save_data, af, ensure_ascii=False, indent=2)
        print(f"[Recalculator] Successfully updated file: {analysis_path}")
    except Exception as af_err:
        print(f"[Recalculator] Error writing analysis JSON file: {af_err}")

    # 6.5. 同步回写 data/processed/<博主名>_notes_details.json，补充缺失的笔记并更新已有的转录文本
    details_path = os.path.join(ROOT_DIR, "data", "processed", f"{blogger_name}_notes_details.json")
    try:
        details_list = []
        if os.path.exists(details_path):
            with open(details_path, "r", encoding="utf-8") as df:
                details_list = json.load(df)
        
        # 建立当前 JSON 文件的索引，Key 为 noteId
        json_notes_map = {}
        for item in details_list:
            nid = str(item.get("note", {}).get("noteId") or item.get("_feed_id") or "")
            if nid:
                json_notes_map[nid] = item
        
        updated_any = False
        # 遍历数据库里的所有最新笔记，更新或补全
        for n in notes:
            nid = str(n["id"])
            db_desc = n["desc"] or ""
            
            if nid in json_notes_map:
                # 已存在，检查 desc 字段是否需要同步
                item = json_notes_map[nid]
                json_desc = item.get("note", {}).get("desc")
                if db_desc and json_desc != db_desc:
                    item["note"]["desc"] = db_desc
                    updated_any = True
            else:
                # 缺失了，使用数据库里的内容动态重构并补全到列表里
                new_item = {
                    "_feed_id": nid,
                    "_meta": {
                        "privacy_version": "v2",
                        "source": "douyin_converted",
                        "converted_at": int(datetime.now().timestamp())
                    },
                    "note": {
                        "noteId": nid,
                        "title": n["title"],
                        "desc": db_desc,
                        "type": n["type"],
                        "time": n["time"],
                        "interactInfo": {
                            "likedCount": str(n["likes"]),
                            "collectedCount": str(n["collects"]),
                            "commentCount": str(n["comments_count"]),
                            "sharedCount": str(n["shares"])
                        }
                    },
                    "comments": {
                        "list": n["comment_list"]
                    }
                }
                details_list.append(new_item)
                json_notes_map[nid] = new_item
                updated_any = True
                
        if updated_any or not os.path.exists(details_path):
            output_dir = os.path.dirname(os.path.abspath(details_path))
            os.makedirs(output_dir, exist_ok=True)
            with open(details_path, "w", encoding="utf-8") as df:
                json.dump(details_list, df, ensure_ascii=False, indent=2)
            print(f"[Recalculator] Successfully synced and rebuilt notes in {details_path}")
    except Exception as df_err:
        print(f"[Recalculator] Error syncing to notes_details JSON: {df_err}")

    # 7. 调用 deep_analyze 重新生成 AI 蒸馏任务文件（如 _AI蒸馏任务.md）
    try:
        import sys
        scripts_dir = os.path.join(ROOT_DIR, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from deep_analyze import deep_analyze
        output_dir = os.path.join(ROOT_DIR, "output")
        
        deep_analyze(analysis_path, blogger_name, output_dir, notes_details_path=details_path, mode="A")
        print(f"[Recalculator] Successfully regenerated AI Task file (_AI蒸馏任务.md)")
    except Exception as da_err:
        print(f"[Recalculator] Error invoking deep_analyze: {da_err}")

    return True
