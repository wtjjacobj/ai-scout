"""
AI Scout 异步采集器 — aiohttp并发 + 更多数据源
替代同步版collector，3-5x更快
"""

import asyncio
import json
import os
import sys
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

# 不走代理
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

# 同步导入DB（SQLite不支持跨线程共享连接，每个任务独立）
from .db import (
    get_db, upsert_project, add_snapshot, add_hn_ref,
    get_star_velocity, init_db, CST
)

TODAY = datetime.now(CST).strftime("%Y-%m-%d")

AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "model", "agent", "mcp",
    "neural", "transformer", "deep learning", "ml", "nlp",
    "diffusion", "rag", "embedding", "inference", "training",
    "fine-tun", "lora", "quantiz", "tokenizer", "chatbot",
    "copilot", "cursor", "code-gen", "automat", "vision",
    "speech", "tts", "stt", "whisper", "ocr", "generation",
    "prompt", "chain-of-thought", "reasoning", "tool-use",
    "multi-agent", "orchestrat", "workflow", "pipeline",
    "voice", "video", "image", "audio", "music",
]

# GitHub API token（可选，提高rate limit）
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def is_ai_related(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in AI_KEYWORDS)


# ============================================================================
# GitHub Search（并发）
# ============================================================================

async def fetch_github_search(session: aiohttp.ClientSession, query: str,
                               headers: dict) -> list[dict]:
    """单个GitHub Search API查询"""
    try:
        url = "https://api.github.com/search/repositories"
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": 30}
        async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("items", [])
    except Exception as e:
        print(f"  [WARN] GitHub search '{query[:40]}...' failed: {e}", file=sys.stderr)
        return []


async def fetch_all_github_recent(session: aiohttp.ClientSession) -> list[dict]:
    """并发查询所有GitHub搜索"""
    since_date = (datetime.now(CST) - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    queries = [
        f"created:>{since_date} stars:>50 topic:ai",
        f"created:>{since_date} stars:>50 topic:llm",
        f"created:>{since_date} stars:>50 topic:machine-learning",
        f"created:>{since_date} stars:>50 topic:mcp",
        f"created:>{since_date} stars:>50 topic:agent",
        f"created:>{since_date} stars:>200",
    ]

    tasks = [fetch_github_search(session, q, headers) for q in queries]
    results = await asyncio.gather(*tasks)

    seen = set()
    repos = []
    for items in results:
        for repo in items:
            full_name = repo.get("full_name", "")
            if full_name in seen:
                continue
            seen.add(full_name)
            repos.append({
                "full_name": full_name,
                "url": repo.get("html_url", ""),
                "description": repo.get("description", "") or "",
                "language": repo.get("language", ""),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "open_issues": repo.get("open_issues_count", 0),
                "topics": repo.get("topics", []),
                "created_at": repo.get("created_at", ""),
                "is_ai": is_ai_related(f"{full_name} {repo.get('description', '')}"),
            })

    repos.sort(key=lambda x: (not x["is_ai"], -x["stars"]))
    return repos


async def fetch_all_github_mcp(session: aiohttp.ClientSession) -> list[dict]:
    """并发搜索MCP servers"""
    since_date = (datetime.now(CST) - timedelta(days=14)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    queries = [
        f"mcp server pushed:>{since_date} stars:>10",
        f"topic:mcp-server pushed:>{since_date}",
        f"model context protocol pushed:>{since_date} stars:>5",
    ]

    tasks = [fetch_github_search(session, q, headers) for q in queries]
    results = await asyncio.gather(*tasks)

    seen = set()
    repos = []
    for items in results:
        for repo in items:
            full_name = repo.get("full_name", "")
            if full_name in seen:
                continue
            seen.add(full_name)
            repos.append({
                "full_name": full_name,
                "url": repo.get("html_url", ""),
                "description": repo.get("description", "") or "",
                "language": repo.get("language", ""),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "topics": repo.get("topics", []),
                "created_at": repo.get("created_at", ""),
            })

    repos.sort(key=lambda x: -x["stars"])
    return repos


# ============================================================================
# Hacker News（并发）
# ============================================================================

async def fetch_hn_query(session: aiohttp.ClientSession, query: str,
                          cutoff_ts: int) -> list[dict]:
    try:
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": query, "tags": "story",
            "numericFilters": f"created_at_i>{cutoff_ts},points>20",
            "hitsPerPage": 20,
        }
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("hits", [])
    except Exception as e:
        print(f"  [WARN] HN search '{query}' failed: {e}", file=sys.stderr)
        return []


async def fetch_all_hackernews(session: aiohttp.ClientSession) -> list[dict]:
    queries = [
        "AI agent framework", "LLM open source tool",
        "MCP server model context", "AI coding assistant",
    ]
    cutoff_ts = int((datetime.now(CST) - timedelta(days=7)).timestamp())

    tasks = [fetch_hn_query(session, q, cutoff_ts) for q in queries]
    results = await asyncio.gather(*tasks)

    seen_ids = set()
    stories = []
    for hits in results:
        for hit in hits:
            obj_id = hit.get("objectID", "")
            if obj_id in seen_ids:
                continue
            seen_ids.add(obj_id)
            stories.append({
                "hn_id": obj_id,
                "title": hit.get("title", ""),
                "url": hit.get("url", "") or f"https://news.ycombinator.com/item?id={obj_id}",
                "hn_url": f"https://news.ycombinator.com/item?id={obj_id}",
                "points": hit.get("points", 0),
                "num_comments": hit.get("num_comments", 0),
                "author": hit.get("author", ""),
                "posted_at": hit.get("created_at", ""),
            })

    stories.sort(key=lambda x: -x["points"])
    return stories


# ============================================================================
# DB写入（同步，批量）
# ============================================================================

def write_github_repos_to_db(repos: list[dict], source: str):
    """批量写入GitHub repos到DB"""
    con = get_db()
    try:
        for r in repos:
            pid = upsert_project(con, r["full_name"],
                url=r["url"], description=r["description"],
                language=r["language"], topics=r.get("topics", []),
                source=source, created_at=r.get("created_at", ""))

            stars_24h, stars_7d = get_star_velocity(con, pid)
            add_snapshot(con, pid, TODAY,
                stars=r.get("stars", 0), forks=r.get("forks", 0),
                open_issues=r.get("open_issues", 0),
                stars_24h=stars_24h, stars_7d=stars_7d,
                raw_data=r)
        con.commit()
    finally:
        con.close()


def write_hn_to_db(stories: list[dict]):
    """批量写入HN stories"""
    con = get_db()
    try:
        for s in stories:
            project_id = None
            url = s.get("url", "")
            if "github.com" in url:
                match = re.match(r"https?://github\.com/([^/]+/[^/]+)", url)
                if match:
                    repo_name = match.group(1).rstrip("/")
                    existing = con.execute(
                        "SELECT id FROM projects WHERE full_name = ?", (repo_name,)
                    ).fetchone()
                    if existing:
                        project_id = existing["id"]

            add_hn_ref(con, project_id, s["hn_id"],
                title=s["title"], url=url, hn_url=s["hn_url"],
                points=s["points"], num_comments=s["num_comments"],
                author=s["author"], posted_at=s["posted_at"])

        # 更新project snapshot的HN热度
        for s in stories:
            if s.get("_project_id"):
                total_pts = con.execute(
                    "SELECT COALESCE(SUM(points), 0) FROM hn_refs WHERE project_id = ?",
                    (s["_project_id"],)
                ).fetchone()[0]
                total_cmt = con.execute(
                    "SELECT COALESCE(SUM(num_comments), 0) FROM hn_refs WHERE project_id = ?",
                    (s["_project_id"],)
                ).fetchone()[0]
                con.execute(
                    "UPDATE snapshots SET hn_points=?, hn_comments=? WHERE project_id=? AND snapshot_date=?",
                    (total_pts, total_cmt, s["_project_id"], TODAY))

        con.commit()
    finally:
        con.close()


# ============================================================================
# Main
# ============================================================================

async def run_collection_async():
    """异步采集主函数"""
    init_db()
    print(f"[{datetime.now(CST).isoformat()}] AI Scout v0.3 async collection starting...")

    async with aiohttp.ClientSession() as session:
        # 并发采集所有源
        print("1/3 GitHub Recent + MCP (parallel)...")
        github_task = fetch_all_github_recent(session)
        mcp_task = fetch_all_github_mcp(session)
        hn_task = fetch_all_hackernews(session)

        github_results, mcp_results, hn_results = await asyncio.gather(
            github_task, mcp_task, hn_task
        )

    ai_count = sum(1 for r in github_results if r.get("is_ai"))
    print(f"   GitHub Recent: {len(github_results)} repos ({ai_count} AI-related)")
    print(f"   GitHub MCP: {len(mcp_results)} repos")
    print(f"   Hacker News: {len(hn_results)} stories")

    # 写入DB
    print("2/3 Writing to DB...")
    write_github_repos_to_db(github_results, "github_recent")
    write_github_repos_to_db(mcp_results, "github_mcp")
    write_hn_to_db(hn_results)

    # 统计
    total = len(github_results) + len(mcp_results) + len(hn_results)
    print(f"\n✅ Done. {total} items collected → SQLite ({TODAY})")

    # 摘要
    summary = {
        "date": TODAY,
        "stats": {
            "github_recent": len(github_results),
            "github_mcp": len(mcp_results),
            "hackernews": len(hn_results),
        },
        "total": total,
        "ai_recent_top5": [
            {"name": r["full_name"], "stars": r["stars"]}
            for r in github_results if r.get("is_ai")
        ][:5],
    }
    print(f"\n=== SUMMARY ===\n{json.dumps(summary, ensure_ascii=False)}")


def run_collection():
    """同步入口"""
    asyncio.run(run_collection_async())


if __name__ == "__main__":
    run_collection()
