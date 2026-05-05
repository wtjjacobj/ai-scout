"""
AI Scout 评分引擎 — 多维综合评分
参考GitScout的5维模型，加入AI分类权重

评分维度：
  momentum_score: 动量分 (0-100) — star增速 + 社交热度
  quality_score:  质量分 (0-100) — star绝对值 + contributors + forks
  category_score: 分类权重 (0-30) — AI项目加分，MCP生态加分
  composite_score: 综合分 = momentum * 0.5 + quality * 0.3 + category * 0.2
"""

import json
from datetime import datetime, timezone, timedelta

from .db import get_db, CST

TODAY = datetime.now(CST).strftime("%Y-%m-%d")

# AI分类关键词映射
CATEGORY_RULES = {
    "framework": {
        "keywords": ["framework", "sdk", "library", "engine", "platform", "runtime"],
        "topics": ["agent", "multi-agent", "orchestrat", "rag", "workflow"],
    },
    "model": {
        "keywords": ["model", "llm", "gpt", "bert", "transformer", "diffusion", "checkpoint"],
        "topics": ["llm", "language-model", "diffusion-model", "text-to-image", "tts"],
    },
    "tool": {
        "keywords": ["tool", "cli", "util", "helper", "wrapper", "client", "server", "mcp"],
        "topics": ["mcp", "mcp-server", "tool-use", "copilot", "browser", "scraper"],
    },
    "data": {
        "keywords": ["dataset", "benchmark", "corpus", "embedding", "vector", "database"],
        "topics": ["dataset", "embedding", "vector-database", "benchmark"],
    },
    "infra": {
        "keywords": ["inference", "serving", "deploy", "training", "fine-tun", "quantiz", "gpu"],
        "topics": ["inference", "serving", "training", "fine-tuning", "quantization"],
    },
}

# 高价值主题加分
PREMIUM_TOPICS = {
    "mcp": 10, "mcp-server": 10,
    "agent": 8, "multi-agent": 8,
    "rag": 7, "tool-use": 7,
    "llm": 5, "inference": 5,
}

# Spam/垃圾项目关键词 — 直接归零
SPAM_PATTERNS = [
    "stake", "casino", "bonus", "gambl", "crypto-airdrop",
    "free-token", "airdrop", "claim-bonus", "spin-wheel",
    "lottery", "betting", "poker", "slot-machine",
]

# 安全POC/漏洞利用 — 降权（不是AI项目）
SECURITY_PATTERNS = [
    "cve-", "exploit", "poc-", "vulnerability", "rce",
    "auth-bypass", "lpe", "privilege-escalat",
    "inject", "xss", "csrf", "shellcode",
]


def is_spam(text: str) -> bool:
    """检测垃圾/赌博/空投项目"""
    text_lower = text.lower()
    return any(p in text_lower for p in SPAM_PATTERNS)


def is_security_exploit(text: str) -> bool:
    """检测安全POC/漏洞利用（非AI项目）"""
    text_lower = text.lower()
    return any(p in text_lower for p in SECURITY_PATTERNS)


def classify_project(description: str, topics: list, name: str) -> tuple[str, str]:
    """基于规则的项目分类 → (category, subcategory)"""
    text = f"{name} {description} {' '.join(topics)}".lower()

    scores = {}
    for cat, rules in CATEGORY_RULES.items():
        score = 0
        for kw in rules["keywords"]:
            if kw in text:
                score += 2
        for topic in rules.get("topics", []):
            if topic in text:
                score += 3
        scores[cat] = score

    if max(scores.values()) == 0:
        return "other", ""

    best_cat = max(scores, key=scores.get)
    subcategory = _extract_subcategory(best_cat, text)
    return best_cat, subcategory


def _extract_subcategory(category: str, text: str) -> str:
    """提取子分类"""
    subcats = {
        "framework": ["agent", "rag", "workflow", "orchestration", "coding"],
        "model": ["llm", "diffusion", "tts", "stt", "vision", "embedding"],
        "tool": ["mcp", "browser", "scraper", "cli", "copilot", "search"],
        "data": ["dataset", "vector-db", "benchmark", "embedding"],
        "infra": ["inference", "training", "fine-tuning", "quantization", "serving"],
    }
    for sub in subcats.get(category, []):
        if sub in text:
            return sub
    return ""


def calc_momentum_score(snap: dict) -> float:
    """动量分 (0-100): star增速 + 社交热度"""
    score = 0.0

    # Star增速 (0-50)
    stars_24h = snap.get("stars_24h", 0) or 0
    stars_7d = snap.get("stars_7d", 0) or 0

    # 24h增速
    if stars_24h > 0:
        score += min(stars_24h / 20 * 20, 20)  # 20分满分
    # 7d增速
    if stars_7d > 0:
        score += min(stars_7d / 100 * 30, 30)  # 30分满分

    # 社交热度 (0-50)
    hn_points = snap.get("hn_points", 0) or 0
    hn_comments = snap.get("hn_comments", 0) or 0

    if hn_points > 0:
        score += min(hn_points / 5 * 25, 25)  # 25分满分
    if hn_comments > 0:
        score += min(hn_comments / 10 * 25, 25)  # 25分满分

    return round(min(score, 100), 1)


def calc_quality_score(snap: dict) -> float:
    """质量分 (0-100): star绝对值 + forks"""
    score = 0.0
    stars = snap.get("stars", 0) or 0
    forks = snap.get("forks", 0) or 0

    # Star绝对值 (0-70)
    if stars > 50000:
        score += 70
    elif stars > 10000:
        score += 55
    elif stars > 5000:
        score += 45
    elif stars > 1000:
        score += 30
    elif stars > 200:
        score += 15
    elif stars > 50:
        score += 5

    # Forks (0-30)
    if forks > 5000:
        score += 30
    elif forks > 1000:
        score += 20
    elif forks > 200:
        score += 10
    elif forks > 50:
        score += 5

    return round(min(score, 100), 1)


def calc_category_score(project: dict) -> float:
    """分类权重 (0-30): AI项目加分 + 热门主题加分"""
    score = 0.0
    category = project.get("category", "")
    topics = project.get("topics", "[]")
    if isinstance(topics, str):
        topics = json.loads(topics)
    description = project.get("description", "")

    # AI项目基础分
    ai_cats = {"framework": 15, "tool": 12, "model": 10, "infra": 10, "data": 8}
    score += ai_cats.get(category, 3)

    # 高价值主题加分
    text = f"{description} {' '.join(topics)}".lower()
    for topic, bonus in PREMIUM_TOPICS.items():
        if topic in text:
            score += bonus

    return round(min(score, 30), 1)


def run_scoring():
    """对当天所有活跃项目计算评分"""
    con = get_db()

    try:
        print(f"[{datetime.now(CST).isoformat()}] Scoring projects...")

        # 找最新的snapshot_date
        latest_date_row = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots"
        ).fetchone()
        target_date = latest_date_row[0] if latest_date_row else TODAY
        print(f"  Scoring date: {target_date}")

        # 获取有快照的所有项目
        projects = con.execute("""
            SELECT p.*, s.stars, s.forks, s.stars_24h, s.stars_7d,
                   s.hn_points, s.hn_comments, s.contributors
            FROM projects p
            JOIN snapshots s ON s.project_id = p.id AND s.snapshot_date = ?
            WHERE p.is_active = 1
        """, (target_date,)).fetchall()

        scored = []
        spam_count = 0
        security_count = 0
        for proj in projects:
            proj_dict = dict(proj)
            text = f"{proj_dict.get('full_name', '')} {proj_dict.get('description', '')}"

            # 0. Spam过滤 — 直接跳过
            if is_spam(text):
                con.execute("UPDATE projects SET is_active = 0 WHERE id = ?", (proj_dict["id"],))
                spam_count += 1
                continue

            # 1. 自动分类（如果未分类）
            if not proj_dict.get("category"):
                topics = proj_dict.get("topics", "[]")
                if isinstance(topics, str):
                    topics = json.loads(topics)
                cat, subcat = classify_project(
                    proj_dict.get("description", ""),
                    topics,
                    proj_dict.get("full_name", ""),
                )
                con.execute(
                    "UPDATE projects SET category = ?, subcategory = ? WHERE id = ?",
                    (cat, subcat, proj_dict["id"])
                )
                proj_dict["category"] = cat
                proj_dict["subcategory"] = subcat

            # 2. 计算各维度分数
            snap = {
                "stars": proj_dict.get("stars", 0),
                "forks": proj_dict.get("forks", 0),
                "stars_24h": proj_dict.get("stars_24h", 0),
                "stars_7d": proj_dict.get("stars_7d", 0),
                "hn_points": proj_dict.get("hn_points", 0),
                "hn_comments": proj_dict.get("hn_comments", 0),
                "contributors": proj_dict.get("contributors", 0),
            }

            momentum = calc_momentum_score(snap)
            quality = calc_quality_score(snap)
            category = calc_category_score(proj_dict)
            composite = round(momentum * 0.5 + quality * 0.3 + category * 0.2, 1)

            # Security POC降权50%
            if is_security_exploit(text):
                composite = round(composite * 0.5, 1)
                category = round(category * 0.5, 1)
                security_count += 1

            # 3. 写入评分
            con.execute("""
                INSERT OR REPLACE INTO scores
                (project_id, score_date, momentum_score, quality_score,
                 category_score, composite_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """,            (proj_dict["id"], target_date, momentum, quality, category, composite))

            scored.append({
                "full_name": proj_dict["full_name"],
                "category": proj_dict.get("category", ""),
                "momentum": momentum,
                "quality": quality,
                "category_score": category,
                "composite": composite,
                "stars": snap["stars"],
            })

        con.commit()

        # 4. 计算排名
        ranked = con.execute("""
            SELECT s.project_id,
                   RANK() OVER (ORDER BY s.composite_score DESC) as rank_total
            FROM scores s WHERE s.score_date = ?
        """, (target_date,)).fetchall()

        for r in ranked:
            con.execute(
                "UPDATE scores SET rank_total = ? WHERE project_id = ? AND score_date = ?",
                (r["rank_total"], r["project_id"], target_date)
            )

        # 分类排名
        for cat in ["framework", "model", "tool", "data", "infra", "other"]:
            cat_ranked = con.execute("""
                SELECT s.project_id,
                       RANK() OVER (ORDER BY s.composite_score DESC) as rank_category
                FROM scores s
                JOIN projects p ON p.id = s.project_id
                WHERE s.score_date = ? AND p.category = ?
            """, (target_date, cat)).fetchall()

            for r in cat_ranked:
                con.execute(
                    "UPDATE scores SET rank_category = ? WHERE project_id = ? AND score_date = ?",
                    (r["rank_category"], r["project_id"], target_date)
                )

        con.commit()

        # 输出Top 20
        scored.sort(key=lambda x: x["composite"], reverse=True)
        print(f"\n✅ Scored {len(scored)} projects "
              f"(spam filtered: {spam_count}, security downgraded: {security_count}). Top 10:")
        for i, s in enumerate(scored[:10], 1):
            print(f"  {i}. [{s['composite']:5.1f}] {s['full_name']} "
                  f"(M:{s['momentum']:.0f} Q:{s['quality']:.0f} C:{s['category_score']:.0f}) "
                  f"⭐{s['stars']} [{s['category']}]")

    except Exception as e:
        con.rollback()
        print(f"[ERROR] Scoring failed: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    run_scoring()
