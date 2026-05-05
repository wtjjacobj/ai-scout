"""
AI Scout 输出格式化 — 飞书/Telegram/终端
飞书限制：长消息截断，所以必须精简。表格5-8行，总分Top10。
"""

import sqlite3
from datetime import datetime, timezone, timedelta

from .db import get_db, CST


def format_daily_feishu(limit: int = 10) -> str:
    """飞书每日推送格式 — 精简版，防止截断"""
    con = get_db()
    try:
        # 最新评分日期
        score_date = con.execute(
            "SELECT MAX(score_date) FROM scores"
        ).fetchone()[0]
        if not score_date:
            return "⚠️ AI Scout 暂无评分数据"

        # Top N
        top = con.execute("""
            SELECT p.full_name, p.category, p.description,
                   s.composite_score, s.momentum_score,
                   snap.stars, snap.stars_24h, snap.hn_points
            FROM projects p
            JOIN scores s ON s.project_id = p.id AND s.score_date = ?
            JOIN snapshots snap ON snap.project_id = p.id
                AND snap.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots)
            WHERE p.is_active = 1
            ORDER BY s.composite_score DESC
            LIMIT ?
        """, (score_date, limit)).fetchall()

        # 分类统计
        cats = con.execute("""
            SELECT p.category, COUNT(*) as cnt
            FROM projects p WHERE p.is_active = 1 AND p.category != ''
            GROUP BY p.category ORDER BY cnt DESC
        """).fetchall()

        # 新项目（7天内创建）
        new_count = con.execute("""
            SELECT COUNT(*) FROM projects p
            JOIN snapshots snap ON snap.project_id = p.id
                AND snap.snapshot_date = ?
            WHERE p.is_active = 1 AND p.created_at > datetime('now', '-7 days')
        """, (score_date,)).fetchone()[0]

        # 组装
        date_str = datetime.now(CST).strftime('%m-%d')
        lines = [f"🔍 AI Scout 日报 | {date_str}"]
        lines.append(f"追踪 {sum(r['cnt'] for r in cats)} 个项目 | 本日新增 {new_count}")
        lines.append("")

        # 分类一行
        cat_str = " ".join(f"{r['category']}:{r['cnt']}" for r in cats[:5])
        lines.append(f"📊 {cat_str}")
        lines.append("")

        # Top项目
        for i, r in enumerate(top, 1):
            name = r['full_name']
            score = r['composite_score']
            stars = _fmt_stars(r['stars'])
            cat = (r['category'] or 'other')[:3]
            desc = (r['description'] or '')[:40]

            # 增速标记
            extra = ""
            if r['stars_24h'] and r['stars_24h'] > 100:
                extra = f" 🚀+{r['stars_24h']}/d"
            elif r['hn_points'] and r['hn_points'] > 50:
                extra = f" 🔥HN:{r['hn_points']}"

            lines.append(f"{i}. [{score:.0f}] {name} ⭐{stars}[{cat}]{extra}")
            if desc:
                lines.append(f"   {desc}")

        lines.append("")
        lines.append("💡 查更多: search_projects / get_trending")

        return "\n".join(lines)

    finally:
        con.close()


def format_daily_telegram(limit: int = 15) -> str:
    """Telegram推送格式 — 可以长一点"""
    con = get_db()
    try:
        score_date = con.execute(
            "SELECT MAX(score_date) FROM scores"
        ).fetchone()[0]
        if not score_date:
            return "⚠️ AI Scout 暂无评分数据"

        # 获取连续两天快照用于计算真实增速
        latest_date = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots"
        ).fetchone()[0]
        prev_date = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots WHERE snapshot_date < ?",
            (latest_date,)
        ).fetchone()[0]

        top = con.execute("""
            SELECT p.full_name, p.category, p.description, p.url,
                   s.composite_score, s.momentum_score, s.quality_score,
                   snap.stars, snap.hn_points
            FROM projects p
            JOIN scores s ON s.project_id = p.id AND s.score_date = ?
            JOIN snapshots snap ON snap.project_id = p.id
                AND snap.snapshot_date = ?
            WHERE p.is_active = 1
            ORDER BY s.composite_score DESC
            LIMIT ?
        """, (score_date, latest_date, limit)).fetchall()

        date_str = datetime.now(CST).strftime('%m-%d')
        lines = [f"🔍 **AI Scout 日报** | {date_str}\n"]

        for i, r in enumerate(top, 1):
            name = r['full_name']
            score = r['composite_score']
            stars = _fmt_stars(r['stars'])
            cat = r['category'] or 'other'
            desc = (r['description'] or '')[:80]

            # 计算真实增速
            badges = []
            if prev_date:
                growth_row = con.execute("""
                    SELECT s1.stars - s2.stars as growth
                    FROM snapshots s1
                    JOIN snapshots s2 ON s2.project_id = s1.project_id AND s2.snapshot_date = ?
                    WHERE s1.project_id = (SELECT id FROM projects WHERE full_name = ?)
                      AND s1.snapshot_date = ?
                """, (prev_date, name, latest_date)).fetchone()
                if growth_row and growth_row['growth'] and growth_row['growth'] > 0:
                    badges.append(f"⬆️+{growth_row['growth']}/d")

            if r['hn_points'] and r['hn_points'] > 30:
                badges.append(f"🔥HN:{r['hn_points']}")
            badge_str = " ".join(badges)

            lines.append(
                f"**{i}. [{score:.0f}] {name}** ⭐{stars} [{cat}]"
            )
            if badge_str:
                lines.append(f"  {badge_str}")
            if desc:
                lines.append(f"  _{desc}_")
            lines.append("")

        return "\n".join(lines)

    finally:
        con.close()


def format_trending_feishu(limit: int = 8) -> str:
    """飞书趋势版 — 只看有真实增速的项目"""
    con = get_db()
    try:
        latest_date = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots"
        ).fetchone()[0]
        prev_date = con.execute(
            "SELECT MAX(snapshot_date) FROM snapshots WHERE snapshot_date < ?",
            (latest_date,)
        ).fetchone()[0]

        if not prev_date:
            return "⚠️ 需要至少2天数据才能计算趋势"

        # 真实增速 = 今天stars - 昨天stars
        trending = con.execute("""
            SELECT p.full_name, p.category,
                   s1.stars as today_stars,
                   s1.stars - s2.stars as star_growth,
                   s1.hn_points
            FROM projects p
            JOIN snapshots s1 ON s1.project_id = p.id AND s1.snapshot_date = ?
            JOIN snapshots s2 ON s2.project_id = p.id AND s2.snapshot_date = ?
            WHERE p.is_active = 1 AND (s1.stars - s2.stars) > 0
            ORDER BY (s1.stars - s2.stars) DESC
            LIMIT ?
        """, (latest_date, prev_date, limit)).fetchall()

        if not trending:
            return "📊 今日无新增star数据（采集间隔内无变化）"

        date_str = datetime.now(CST).strftime('%m-%d')
        lines = [f"🔥 AI Scout 增速榜 | {date_str}\n"]

        for i, r in enumerate(trending, 1):
            name = r['full_name']
            cat = (r['category'] or 'other')[:3]
            growth = r['star_growth']
            stars = _fmt_stars(r['today_stars'])
            hn = f" 🔥HN:{r['hn_points']}" if r['hn_points'] and r['hn_points'] > 20 else ""

            lines.append(f"{i}. +{growth}⭐ {name} ({stars}) [{cat}]{hn}")

        return "\n".join(lines)

    finally:
        con.close()


def _fmt_stars(n: int) -> str:
    """格式化star数"""
    if not n:
        return "0"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)
