"""
数据库种子数据填充模块 (seed.py)
核心职责：在数据库启动或重置时，自动填充思维模型库、行业资讯与流量热度，避免初始界面空白（冷启动问题）。
"""

import os
import sys
from datetime import datetime

# 引入本级 database 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import get_db_connection

def seed_knowledge_base(cursor):
    """播种经典思维模型"""
    models = [
        (
            "战术勤奋与战略懒惰",
            "个人成长 / 商业分析",
            "用高强度的机械重复性工作（如盲目刷量、拼工作时长），来掩盖核心战略思考（如人设定位、商业闭环）的缺失。",
            "认为每天熬夜写出 10 条平庸的内容就能获得增长，而逃避去痛苦思索“我的核心竞争壁垒在哪里”。",
            "在漏水的航船上拼命舀水（战术勤奋），却不肯停靠下来补好那个致命的破洞（战略懒惰）。"
        ),
        (
            "第二层次思维 (Second-Order Thinking)",
            "商业博弈 / 投资决策",
            "第一层次思维看到共识，直觉行动；第二层次思维看到共识背后的博弈，分析共识产生的价格溢价或透支风险。",
            "大家都在疯狂追逐同一个大热爆款题材时，第一层思维大喊“冲啊”，却不知大家都进场时，红利早就被稀释完毕。",
            "普通人在看选美选手的脸蛋（第一层次），高手在观察底下评委的投票表情和偏好（第二层次）。"
        ),
        (
            "遛狗理论 (The Walking Dog Analogy)",
            "价值投资 / 财经分析",
            "事物在市场上的价格（小狗）经常会因为情绪的贪婪和恐惧剧烈上下波动，但长远看必然回归其真实价值中枢（主人）。",
            "当股价或流量暴跌时惊慌失措地抛售，误以为这个生意的本质价值也跟着暴跌了一半。",
            "主人在公园慢步前行，小狗系着绳子忽快忽慢地乱跑，但无论它跑多远，绳子拉紧后最终都会回到主人脚边。"
        ),
        (
            "逆向思维 (Inversion Thinking)",
            "问题解决 / 内容策划",
            "相比于思考“怎样才能做出爆款”，先痛苦去思考“写成什么鬼样子就绝对没有人看”，然后一一反向避坑。",
            "一味追求迎合大众喜好去盲目逆向（为了反共识而反共识），而不是从常识和基本规律出发做独立的理性决策。",
            "查理·芒格常挂在嘴边的话：“如果我知道自己会死在什么地方，那我这辈子就绝对不去那里。”"
        ),
        (
            "幸存者偏差 (Survivorship Bias)",
            "认知模型 / 创作避坑",
            "因为死掉的、失败的数据无法说话，我们往往只盯着少数成名的大博主看他们的动作，总结出大量“伪成功规律”。",
            "某概念大号因为追热点暴涨，新手误以为“追热点就是唯一成功路径”，而忽略了 99.9% 追同样热点却石沉大海的夭折账号。",
            "二战时期英军仅根据返回轰炸机上的弹孔分布去加强装甲，却忽略了被击中引擎直接坠毁（没能飞回来）的飞机损伤。"
        )
    ]

    for model in models:
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO knowledge_base (topic, niche, insight, pitfall, analogy)
            VALUES (?, ?, ?, ?, ?);
            """, model)
        except Exception as e:
            print(f"[Seed] Error seeding knowledge: {e}")

def seed_industry_news(cursor):
    """播种行业资讯快讯"""
    news_items = [
        (
            "news_001",
            "抖音更新图文带货算法规则，大幅加权“高停留时间”原创图文",
            "抖音电商近日优化了图文带货的流量推送机制。新算法倾向于重度加权那些排版有精緻感、停留时间长、文字原创性高的内容，不再支持大批量工厂式生成的混剪同质化图片。",
            "36氪",
            "https://36kr.com",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ),
        (
            "news_002",
            "小红书搜索权重再次提升，官方推出“搜推一体”营销新路径",
            "小红书公开课上，官方强调用户在平台上的主动搜索意图已成为新爆款的起点。新商业体系将打通搜索广告与信息流推荐，对内容有深度价值（干货型、教程型）的笔记会有更长的长尾流量生命周期。",
            "华尔街见闻",
            "https://wallstreetcn.com",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ),
        (
            "news_003",
            "快手全面上线“可灵”视频大模型，支持文字/图像一键生成电影级视频",
            "快手正式对外开放可灵（Kling）大模型。该模型能生成高帧率、长时长的流畅视频，在物体运动物理规律的保真度上获得创作者好评。个人博主有望利用该模型零成本生成精美视频片头。",
            "极客公园",
            "https://www.geekpark.net",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    ]

    for item in news_items:
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO industry_news_cache (id, title, content, source, url, published_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, item)
        except Exception as e:
            print(f"[Seed] Error seeding news: {e}")

def seed_trending_topics(cursor):
    """播种全网流量热搜（Mock 数据占位，飞书同步接口保留）"""
    trends = [
        ("人工智能如何重塑大众日常学习模式", "480万", "微博", "https://weibo.com"),
        ("高考填报志愿避坑红线话题热议", "360万", "抖音", "https://douyin.com"),
        ("年轻人为何爱上深度慢阅读卡片", "290万", "小红书", "https://xiaohongshu.com"),
        ("数字游民生活方式的真实困境与出路", "210万", "知乎", "https://zhihu.com"),
        ("副业做自媒体写作者的核心变现闭环", "180万", "微信", "https://weixin.qq.com")
    ]

    for trend in trends:
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO trending_topics (title, heat, source, url)
            VALUES (?, ?, ?, ?);
            """, trend)
        except Exception as e:
            print(f"[Seed] Error seeding trends: {e}")

def seed_all():
    """执行全部播种逻辑"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    seed_knowledge_base(cursor)
    seed_industry_news(cursor)
    seed_trending_topics(cursor)
    
    conn.commit()
    conn.close()
    print("[MockData] Seeding check completed.")

if __name__ == "__main__":
    seed_all()
