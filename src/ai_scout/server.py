"""
AI Scout MCP Server — AI Agent直接连接查询
FastMCP 3.x, stdio + streamable-http双模式
"""

import json
from datetime import datetime, timezone, timedelta
from fastmcp import FastMCP

from .db import (
    get_db, query_projects, query_trending,
    get_project_detail, get_stats,
)

CST = timezone(timedelta(hours=8))

mcp = FastMCP(
    "ai-scout",
    version="0.2.0",
    instructions=(
        "AI Scout — AI项目发现引擎。帮你找到今天最值得关注的AI开源项目。"
        "数据来源：GitHub Trending + Hacker News + MCP Registry + OSSInsight。"
        "支持按分类、评分、趋势查询。"
    ),
)


@mcp.tool(name="daily_report", description="获取今日精选AI项目（综合评分最高的项目）")
def daily_report(limit: int = 10) -> str:
    con = get_db()
    try:
        projects = query_projects(con, limit=limit, order_by="composite_score")
        if not projects:
            return "今日暂无数据，请先运行采集。"

        lines = [f"## AI Scout 每日精选 ({datetime.now(CST).strftime('%Y-%m-%d')})\n"]
        lines.append(f"共 {len(projects)} 个高价值AI项目：\n")

        for i, p in enumerate(projects, 1):
            score = p.get("composite_score", 0)
            stars = p.get("latest_snapshot", {}).get("stars", "?") if "latest_snapshot" in p else "?"
            cat = p.get("category", "other")
            lines.append(
                f"**{i}. [{score:.0f}分] [{cat}] {p['full_name']}**\n"
                f"   {p.get('description', '')[:120]}\n"
                f"   {p.get('url', '')}\n"
            )

        return "\n".join(lines)
    finally:
        con.close()


@mcp.tool(name="search_projects", description="搜索AI项目，支持按分类/关键词/评分过滤")
def search_projects(
    category: str = "",
    min_score: float = 0,
    limit: int = 10,
    order_by: str = "composite_score",
) -> str:
    con = get_db()
    try:
        projects = query_projects(con, category=category, min_score=min_score,
                                  limit=limit, order_by=order_by)
        if not projects:
            return f"未找到符合条件的项目 (category={category}, min_score={min_score})"

        cat_label = f"[{category}]" if category else "[全部]"
        lines = [f"## AI项目搜索结果 {cat_label}\n"]

        for i, p in enumerate(projects, 1):
            score = p.get("composite_score", 0)
            cat = p.get("category", "other")
            desc = (p.get("description") or "")[:100]
            lines.append(
                f"**{i}. [{score:.0f}分] [{cat}] {p['full_name']}** — {desc}\n"
                f"   {p.get('url', '')}\n"
            )

        return "\n".join(lines)
    finally:
        con.close()


@mcp.tool(name="get_trending", description="查询趋势AI项目（基于star增速）")
def get_trending(days: int = 7, limit: int = 15) -> str:
    con = get_db()
    try:
        results = query_trending(con, days=days, limit=limit)
        if not results:
            return "暂无趋势数据。"

        lines = [f"## 🔥 AI项目趋势榜 (近{days}天)\n"]
        for i, r in enumerate(results, 1):
            stars_7d = r.get("stars_7d", 0)
            current = r.get("current_stars", 0)
            cat = r.get("category", "other")
            desc = (r.get("description") or "")[:80]
            lines.append(
                f"**{i}. ⬆️+{stars_7d} 7d [{cat}] {r['full_name']}** (⭐{current})\n"
                f"   {desc}\n"
            )

        return "\n".join(lines)
    finally:
        con.close()


@mcp.tool(name="project_detail", description="获取单个AI项目的详细信息（含评分、历史、HN讨论）")
def project_detail(full_name: str) -> str:
    con = get_db()
    try:
        detail = get_project_detail(con, full_name)
        if not detail:
            return f"未找到项目: {full_name}"

        lines = [f"## {detail['full_name']}\n"]
        lines.append(f"**URL**: {detail.get('url', '')}\n")
        lines.append(f"**分类**: {detail.get('category', 'other')} / {detail.get('subcategory', '')}\n")
        lines.append(f"**描述**: {detail.get('description', '')}\n")

        # 最新快照
        snap = detail.get("latest_snapshot", {})
        if snap:
            lines.append(f"\n### 最新数据 ({snap.get('snapshot_date', '')})\n")
            lines.append(f"- ⭐ Stars: {snap.get('stars', 0)}")
            lines.append(f"- 🍴 Forks: {snap.get('forks', 0)}")
            lines.append(f"- ⬆️ 7天增速: +{snap.get('stars_7d', 0)}")
            lines.append(f"- ⬆️ 24h增速: +{snap.get('stars_24h', 0)}")
            lines.append(f"- 💬 HN热度: {snap.get('hn_points', 0)} points")

        # 评分
        score = detail.get("latest_score", {})
        if score:
            lines.append(f"\n### 评分\n")
            lines.append(f"- 🏆 综合: {score.get('composite_score', 0):.1f}")
            lines.append(f"- 🚀 动量: {score.get('momentum_score', 0):.1f}")
            lines.append(f"- 💎 质量: {score.get('quality_score', 0):.1f}")
            lines.append(f"- 🏷️ 分类: {score.get('category_score', 0):.1f}")
            lines.append(f"- 📊 排名: #{score.get('rank_total', '?')}")

        # HN讨论
        hn_refs = detail.get("hn_refs", [])
        if hn_refs:
            lines.append(f"\n### Hacker News 讨论\n")
            for ref in hn_refs[:5]:
                lines.append(f"- [{ref.get('points', 0)}pts] {ref.get('title', '')} — {ref.get('hn_url', '')}")

        return "\n".join(lines)
    finally:
        con.close()


@mcp.tool(name="scout_stats", description="获取AI Scout数据库统计信息")
def scout_stats() -> str:
    con = get_db()
    try:
        stats = get_stats(con)
        lines = ["## AI Scout 数据统计\nn"]
        lines.append(f"- 📦 总项目数: {stats.get('total_projects', 0)}")
        lines.append(f"- 📊 快照记录: {stats.get('total_snapshots', 0)}")
        lines.append(f"- 💬 HN引用: {stats.get('total_hn_refs', 0)}")
        lines.append(f"- 📅 最新采集: {stats.get('latest_snapshot_date', 'N/A')}")
        lines.append(f"- 📅 最新评分: {stats.get('latest_score_date', 'N/A')}")

        cats = stats.get("by_category", {})
        if cats:
            lines.append("\n### 按分类\n")
            for cat, cnt in cats.items():
                lines.append(f"- {cat}: {cnt}")

        return "\n".join(lines)
    finally:
        con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Scout MCP Server")
    parser.add_argument("--streamable-http", action="store_true",
                        help="Run as HTTP server instead of stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args()

    if args.streamable_http:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
