"""
AI Scout 采集器 — 多源采集 + 写入SQLite
复用原scout.py逻辑，升级为直接写DB + star增速追踪
"""

import json
import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# 不走代理
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

from .db import (
    get_db, upsert_project, add_snapshot, add_hn_ref, get_star_velocity, CST
)

TODAY = datetime.now(CST).strftime("%Y-%m-%d")

# AI关键词
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


def is_ai_related(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in AI_KEYWORDS)


# ============================================================================
# Source 1: GitHub Search - 最近7天高star新项目
# ============================================================================

def fetch_github_recent(con) -> list[dict]:
    """采集GitHub最近高star项目，直接写入DB"""
    results = []
    since_date = (datetime.now(CST) - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github.v3+json"}

    queries = [
        f"created:>{since_date} stars:>50 topic:ai",
        f"created:>{since_date} stars:>50 topic:llm",
        f"created:>{since_date} stars:>50 topic:machine-learning",
        f"created:>{since_date} stars:>50 topic:mcp",
        f"created:>{since_date} stars:>50 topic:agent",
        f"created:>{since_date} stars:>200",
    ]

    seen = set()
    for query in queries:
        try:
            url = "https://api.github.com/search/repositories"
            params = {"q": query, "sort": "stars", "order": "desc", "per_page": 30}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for repo in data.get("items", []):
                full_name = repo.get("full_name", "")
                if full_name in seen:
                    continue
                seen.add(full_name)

                pid = upsert_project(con, full_name,
                    url=repo.get("html_url", ""),
                    description=repo.get("description", "") or "",
                    language=repo.get("language", ""),
                    topics=repo.get("topics", []),
                    source="github_recent",
                    created_at=repo.get("created_at", ""),
                )

                stars_24h, stars_7d = get_star_velocity(con, pid)

                add_snapshot(con, pid, TODAY,
                    stars=repo.get("stargazers_count", 0),
                    forks=repo.get("forks_count", 0),
                    open_issues=repo.get("open_issues_count", 0),
                    watchers=repo.get("watchers_count", 0),
                    stars_24h=stars_24h,
                    stars_7d=stars_7d,
                    raw_data=repo,
                )

                results.append({
                    "full_name": full_name,
                    "project_id": pid,
                    "stars": repo.get("stargazers_count", 0),
                    "is_ai": is_ai_related(f"{full_name} {repo.get('description', '')}"),
                })

        except Exception as e:
            print(f"  [WARN] GitHub search '{query[:40]}...' failed: {e}", file=sys.stderr)
            time.sleep(2)

    results.sort(key=lambda x: (not x["is_ai"], -x["stars"]))
    return results


# ============================================================================
# Source 2: Hacker News
# ============================================================================

def fetch_hackernews(con) -> list[dict]:
    """采集HN AI热门帖子"""
    results = []
    queries = [
        "AI agent framework",
        "LLM open source tool",
        "MCP server model context",
        "AI coding assistant",
    ]

    seen_ids = set()
    cutoff_ts = int((datetime.now(CST) - timedelta(days=7)).timestamp())

    for query in queries:
        try:
            url = "https://hn.algolia.com/api/v1/search"
            params = {
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{cutoff_ts},points>20",
                "hitsPerPage": 20,
            }
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for hit in data.get("hits", []):
                obj_id = hit.get("objectID", "")
                if obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)

                # 尝试从URL中提取GitHub repo
                project_id = None
                hit_url = hit.get("url", "")
                if "github.com" in hit_url:
                    match = re.match(r"https?://github\.com/([^/]+/[^/]+)", hit_url)
                    if match:
                        repo_name = match.group(1).rstrip("/")
                        existing = con.execute(
                            "SELECT id FROM projects WHERE full_name = ?", (repo_name,)
                        ).fetchone()
                        if existing:
                            project_id = existing["id"]

                add_hn_ref(con, project_id, obj_id,
                    title=hit.get("title", ""),
                    url=hit_url or f"https://news.ycombinator.com/item?id={obj_id}",
                    hn_url=f"https://news.ycombinator.com/item?id={obj_id}",
                    points=hit.get("points", 0),
                    num_comments=hit.get("num_comments", 0),
                    author=hit.get("author", ""),
                    posted_at=hit.get("created_at", ""),
                )

                results.append({
                    "hn_id": obj_id,
                    "title": hit.get("title", ""),
                    "points": hit.get("points", 0),
                    "project_id": project_id,
                })
        except Exception as e:
            print(f"  [WARN] HN search '{query}' failed: {e}", file=sys.stderr)

    results.sort(key=lambda x: x["points"], reverse=True)
    return results


# ============================================================================
# Source 3: GitHub MCP Servers
# ============================================================================

def fetch_github_mcp_servers(con) -> list[dict]:
    """搜索GitHub MCP server仓库"""
    results = []
    since_date = (datetime.now(CST) - timedelta(days=14)).strftime("%Y-%m-%d")
    queries = [
        f"mcp server pushed:>{since_date} stars:>10",
        f"topic:mcp-server pushed:>{since_date}",
        f"model context protocol pushed:>{since_date} stars:>5",
    ]

    seen = set()
    for query in queries:
        try:
            url = "https://api.github.com/search/repositories"
            params = {"q": query, "sort": "stars", "order": "desc", "per_page": 30}
            headers = {"Accept": "application/vnd.github.v3+json"}
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for repo in data.get("items", []):
                full_name = repo.get("full_name", "")
                if full_name in seen:
                    continue
                seen.add(full_name)

                pid = upsert_project(con, full_name,
                    url=repo.get("html_url", ""),
                    description=repo.get("description", "") or "",
                    language=repo.get("language", ""),
                    topics=repo.get("topics", []),
                    source="github_mcp",
                    created_at=repo.get("created_at", ""),
                )

                stars_24h, stars_7d = get_star_velocity(con, pid)

                add_snapshot(con, pid, TODAY,
                    stars=repo.get("stargazers_count", 0),
                    forks=repo.get("forks_count", 0),
                    open_issues=repo.get("open_issues_count", 0),
                    stars_24h=stars_24h,
                    stars_7d=stars_7d,
                    raw_data=repo,
                )

                results.append({"full_name": full_name, "project_id": pid})

        except Exception as e:
            print(f"  [WARN] GitHub MCP search failed: {e}", file=sys.stderr)

    return results


# ============================================================================
# Source 4: OSSInsight Trending
# ============================================================================

def fetch_ossinsight_trending(con) -> list[dict]:
    """通过OSSInsight API获取trending repos"""
    results = []
    for lang in ["Python", "TypeScript"]:
        try:
            url = "https://api.ossinsight.io/v1/repos/trending"
            params = {"period": "last_week", "language": lang, "n": 30}
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for repo in data.get("data", {}).get("rows", []):
                repo_name = repo.get("repo_name", "")
                if not repo_name:
                    continue

                pid = upsert_project(con, repo_name,
                    url=f"https://github.com/{repo_name}",
                    description=repo.get("description", "") or "",
                    language=repo.get("language", ""),
                    source="ossinsight",
                )

                stars_24h, stars_7d = get_star_velocity(con, pid)

                add_snapshot(con, pid, TODAY,
                    stars=repo.get("stars", 0),
                    forks=repo.get("forks", 0),
                    stars_24h=stars_24h,
                    stars_7d=stars_7d,
                    raw_data=repo,
                )

                results.append({"full_name": repo_name, "project_id": pid})

        except Exception as e:
            print(f"  [WARN] OSSInsight {lang} trending failed: {e}", file=sys.stderr)

    return results


# ============================================================================
# Main
# ============================================================================

def run_collection():
    """执行全量采集"""
    from .db import init_db
    init_db()

    con = get_db()
    stats = {}

    try:
        print(f"[{datetime.now(CST).isoformat()}] AI Scout v0.2 collection starting...")

        print("1/4 GitHub Recent...")
        github_results = fetch_github_recent(con)
        ai_count = sum(1 for r in github_results if r.get("is_ai"))
        stats["github_recent"] = len(github_results)
        print(f"   → {len(github_results)} repos ({ai_count} AI-related)")

        print("2/4 Hacker News...")
        hn_results = fetch_hackernews(con)
        stats["hackernews"] = len(hn_results)
        print(f"   → {len(hn_results)} stories")

        print("3/4 GitHub MCP Servers...")
        mcp_results = fetch_github_mcp_servers(con)
        stats["github_mcp"] = len(mcp_results)
        print(f"   → {len(mcp_results)} MCP repos")

        print("4/4 OSSInsight Trending...")
        ossi_results = fetch_ossinsight_trending(con)
        stats["ossinsight"] = len(ossi_results)
        print(f"   → {len(ossi_results)} repos")

        # 更新HN引用的积分到对应项目的snapshot
        for hn in hn_results:
            if hn.get("project_id"):
                total_points = con.execute(
                    "SELECT COALESCE(SUM(points), 0) as total FROM hn_refs WHERE project_id = ?",
                    (hn["project_id"],)
                ).fetchone()["total"]
                total_comments = con.execute(
                    "SELECT COALESCE(SUM(num_comments), 0) as total FROM hn_refs WHERE project_id = ?",
                    (hn["project_id"],)
                ).fetchone()["total"]
                con.execute(
                    "UPDATE snapshots SET hn_points = ?, hn_comments = ? WHERE project_id = ? AND snapshot_date = ?",
                    (total_points, total_comments, hn["project_id"], TODAY)
                )

        con.commit()
        total = sum(stats.values())
        print(f"\n✅ Done. {total} items collected → SQLite ({TODAY})")

        # 输出摘要
        summary = {
            "date": TODAY,
            "stats": stats,
            "total": total,
            "ai_recent": [r for r in github_results if r.get("is_ai")][:10],
            "top_hn": hn_results[:5],
        }
        print(f"\n=== SUMMARY JSON ===\n{json.dumps(summary, ensure_ascii=False)}")

    except Exception as e:
        con.rollback()
        print(f"[ERROR] Collection failed: {e}", file=sys.stderr)
        raise
    finally:
        con.close()


if __name__ == "__main__":
    run_collection()
