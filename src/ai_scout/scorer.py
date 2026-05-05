"""
AI Scout 评分引擎 v2 — 多维综合评分 + 高区分度
核心改进：
  1. 动量分基于真实star增速（对比前日快照）
  2. 质量分用连续函数替代阶梯函数
  3. 新鲜度加分（新项目比老项目更值得关注）
  4. Spam/Security过滤
  5. 高区分度评分分布
"""

import json
import math
from datetime import datetime, timezone, timedelta

from .db import get_db, CST

TODAY = datetime.now(CST).strftime("%Y-%m-%d")

# AI分类关键词映射
CATEGORY_RULES = {
    "framework": {
        "keywords": ["framework", "sdk", "library", "engine", "platform", "runtime", "orchestrat", "pipeline", "relay"],
        "topics": ["agent", "multi-agent", "orchestrat", "rag", "workflow", "langchain", "crewai"],
    },
    "model": {
        "keywords": ["model", "llm", "gpt", "bert", "transformer", "diffusion", "checkpoint", "weights", "image-gen"],
        "topics": ["llm", "language-model", "diffusion-model", "text-to-image", "tts", "stt", "whisper", "gpt-image"],
    },
    "tool": {
        "keywords": ["tool", "cli", "util", "helper", "wrapper", "client", "server", "mcp", "bot",
                      "skill", "spec", "template", "generator", "scraper", "monitor", "scanner",
                      "explainer", "audit", "dashboard", "vpn", "proxy", "tunnel"],
        "topics": ["mcp", "mcp-server", "mcp-client", "tool-use", "copilot", "browser", "scraper",
                   "claude-code", "codex", "opencode", "hermes", "cursor", "skill"],
    },
    "data": {
        "keywords": ["dataset", "benchmark", "corpus", "embedding", "vector", "database", "retrieval"],
        "topics": ["dataset", "embedding", "vector-database", "benchmark", "retrieval"],
    },
    "infra": {
        "keywords": ["inference", "serving", "deploy", "training", "fine-tun", "quantiz", "gpu", "cuda",
                      "hosting", "container", "docker"],
        "topics": ["inference", "serving", "training", "fine-tuning", "quantization", "llama-cpp"],
    },
}

# 高价值主题加分
PREMIUM_TOPICS = {
    "mcp": 15, "mcp-server": 15, "mcp-client": 12,
    "agent": 12, "multi-agent": 14,
    "rag": 10, "tool-use": 10,
    "llm": 8, "inference": 8,
    "codex": 8, "cursor": 8, "claude": 8,
}

# Spam/垃圾项目关键词
SPAM_PATTERNS = [
    "stake", "casino", "bonus", "gambl", "crypto-airdrop",
    "free-token", "airdrop", "claim-bonus", "spin-wheel",
    "lottery", "betting", "poker", "slot-machine",
    "inject", "hack-", "cheat-", "crack-", "patcher",
    "keygen", "pirat", "warez",
]

# 安全POC/漏洞利用
SECURITY_PATTERNS = [
    "cve-", "exploit", "poc-", "vulnerability", "rce",
    "auth-bypass", "lpe", "privilege-escalat",
    "xss", "csrf", "shellcode", "0day",
]


def is_spam(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in SPAM_PATTERNS)


def is_security_exploit(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in SECURITY_PATTERNS)


def classify_project(description: str, topics: list, name: str) -> tuple[str, str]:
    """规则分类 → (category, subcategory)"""
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
    subcats = {
        "framework": ["agent", "rag", "workflow", "orchestration", "coding"],
        "model": ["llm", "diffusion", "tts", "stt", "vision", "embedding"],
        "tool": ["mcp", "browser", "scraper", "cli", "copilot", "search", "ide"],
        "data": ["dataset", "vector-db", "benchmark", "embedding"],
        "infra": ["inference", "training", "fine-tuning", "quantization", "serving", "gpu"],
    }
    for sub in subcats.get(category, []):
        if sub in text:
            return sub
    return ""


# ============================================================================
# 评分函数 — 连续函数，高区分度
# ============================================================================

def _log_score(value: float, max_val: float, max_points: float) -> float:
    """对数评分：value越大分越高，但增速递减。避免阶梯函数的同分问题"""
    if value <= 0 or max_val <= 0:
        return 0.0
    return max_points * math.log(1 + value) / math.log(1 + max_val)


def calc_momentum_score(snap: dict, prev_snap: dict | None) -> float:
    """动量分 (0-100): star增速 + 社交热度"""
    score = 0.0

    # === Star增速 (0-55) ===
    stars_24h = snap.get("stars_24h", 0) or 0
    stars_7d = snap.get("stars_7d", 0) or 0

    # 如果有前日快照，直接算真实增速
    if prev_snap and prev_snap.get("stars", 0):
        actual_24h = (snap.get("stars", 0) or 0) - (prev_snap["stars"] or 0)
        if actual_24h > 0:
            stars_24h = actual_24h

    # 24h增速 (0-25)
    if stars_24h > 0:
        score += _log_score(stars_24h, 500, 25)

    # 7d增速 (0-30)
    if stars_7d > 0:
        score += _log_score(stars_7d, 5000, 30)

    # === 社交热度 (0-45) ===
    hn_points = snap.get("hn_points", 0) or 0
    hn_comments = snap.get("hn_comments", 0) or 0

    if hn_points > 0:
        score += _log_score(hn_points, 500, 30)
    if hn_comments > 0:
        score += _log_score(hn_comments, 200, 15)

    return round(min(score, 100), 1)


def calc_quality_score(snap: dict) -> float:
    """质量分 (0-100): star绝对值 + forks，连续对数函数"""
    score = 0.0
    stars = snap.get("stars", 0) or 0
    forks = snap.get("forks", 0) or 0

    # Stars (0-70): log scale
    if stars > 0:
        score += _log_score(stars, 200000, 70)

    # Forks (0-30): log scale
    if forks > 0:
        score += _log_score(forks, 50000, 30)

    return round(min(score, 100), 1)


def calc_freshness_score(created_at: str) -> float:
    """新鲜度分 (0-15): 新项目加分"""
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < 7:
            return 15.0
        elif age_days < 30:
            return 10.0
        elif age_days < 90:
            return 5.0
        elif age_days < 365:
            return 2.0
        return 0.0
    except (ValueError, TypeError):
        return 0.0


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
    score += ai_cats.get(category, 2)

    # 高价值主题加分
    text = f"{description} {' '.join(topics)}".lower()
    for topic, bonus in PREMIUM_TOPICS.items():
        if topic in text:
            score += bonus

    return round(min(score, 30), 1)


# ============================================================================
# 主评分函数
# ============================================================================

def run_scoring():
    con = get_db()
    try:
        print(f"[{datetime.now(CST).isoformat()}] Scoring projects...")

        # 找最新的snapshot_date
        latest_date_row = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots"
        ).fetchone()
        target_date = latest_date_row[0] if latest_date_row else TODAY
        print(f"  Scoring date: {target_date}")

        # 找前一天的日期（用于计算真实增速）
        prev_date_row = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots WHERE snapshot_date < ?",
            (target_date,)
        ).fetchone()
        prev_date = prev_date_row[0] if prev_date_row else None

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

            # 0. Spam过滤
            if is_spam(text):
                con.execute("UPDATE projects SET is_active = 0 WHERE id = ?", (proj_dict["id"],))
                spam_count += 1
                continue

            # 1. 自动分类
            if not proj_dict.get("category") or proj_dict.get("category") in ("", "uncategorized"):
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

            # 2. 获取前日快照（用于真实增速计算）
            prev_snap = None
            if prev_date:
                prev_row = con.execute(
                    "SELECT stars, forks FROM snapshots WHERE project_id = ? AND snapshot_date = ?",
                    (proj_dict["id"], prev_date)
                ).fetchone()
                if prev_row:
                    prev_snap = dict(prev_row)

            # 3. 计算各维度分数
            snap = {
                "stars": proj_dict.get("stars", 0),
                "forks": proj_dict.get("forks", 0),
                "stars_24h": proj_dict.get("stars_24h", 0),
                "stars_7d": proj_dict.get("stars_7d", 0),
                "hn_points": proj_dict.get("hn_points", 0),
                "hn_comments": proj_dict.get("hn_comments", 0),
                "contributors": proj_dict.get("contributors", 0),
            }

            momentum = calc_momentum_score(snap, prev_snap)
            quality = calc_quality_score(snap)
            category = calc_category_score(proj_dict)
            freshness = calc_freshness_score(proj_dict.get("created_at", ""))

            # 综合分 = 动量×0.4 + 质量×0.25 + 分类×0.2 + 新鲜度×0.15
            composite = round(
                momentum * 0.4 + quality * 0.25 + category * 0.2 + freshness * 0.15, 1
            )

            # Security POC降权60%
            if is_security_exploit(text):
                composite = round(composite * 0.4, 1)
                category = round(category * 0.4, 1)
                security_count += 1

            # 4. 写入评分
            con.execute("""
                INSERT OR REPLACE INTO scores
                (project_id, score_date, momentum_score, quality_score,
                 category_score, composite_score)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (proj_dict["id"], target_date, momentum, quality, category, composite))

            scored.append({
                "full_name": proj_dict["full_name"],
                "category": proj_dict.get("category", ""),
                "momentum": momentum,
                "quality": quality,
                "category_score": category,
                "freshness": freshness,
                "composite": composite,
                "stars": snap["stars"],
            })

        con.commit()

        # 5. 计算排名
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

        # 输出
        scored.sort(key=lambda x: x["composite"], reverse=True)
        print(f"\n✅ Scored {len(scored)} projects "
              f"(spam: {spam_count}, security↓: {security_count}). Top 10:")
        for i, s in enumerate(scored[:10], 1):
            print(f"  {i}. [{s['composite']:5.1f}] {s['full_name']} "
                  f"(M:{s['momentum']:.0f} Q:{s['quality']:.0f} "
                  f"C:{s['category_score']:.0f} F:{s['freshness']:.0f}) "
                  f"⭐{s['stars']} [{s['category']}]")

    except Exception as e:
        con.rollback()
        print(f"[ERROR] Scoring failed: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    run_scoring()
