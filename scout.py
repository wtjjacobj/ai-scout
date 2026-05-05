#!/usr/bin/env python3
"""
AI Scout - AI工具发现管线
信息源：GitHub Trending + Hacker News + MCP Registry + GitHub Topic
输出：结构化JSON，供LLM二次筛选
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# 不走代理，直连（Mac上SR代理不稳定，GitHub API和HN API都能直连）
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# 中国时区
CST = timezone(timedelta(hours=8))
TODAY = datetime.now(CST).strftime("%Y-%m-%d")

# ============================================================================
# Source 1: GitHub Search - 最近7天高star新项目（替代Trending爬虫）
# ============================================================================

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


def is_ai_related(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in AI_KEYWORDS)


def fetch_github_recent():
    """用GitHub Search API找最近7天高star项目"""
    results = []
    since_date = (datetime.now(CST) - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github.v3+json"}

    queries = [
        # AI相关，按star排序
        f"created:>{since_date} stars:>50 topic:ai",
        f"created:>{since_date} stars:>50 topic:llm",
        f"created:>{since_date} stars:>50 topic:machine-learning",
        f"created:>{since_date} stars:>50 topic:mcp",
        f"created:>{since_date} stars:>50 topic:agent",
        # 通用高star新项目
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

                desc = repo.get("description", "") or ""
                results.append({
                    "source": "github_recent",
                    "name": full_name,
                    "url": repo.get("html_url", ""),
                    "description": desc,
                    "language": repo.get("language", ""),
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "created_at": repo.get("created_at", ""),
                    "topics": repo.get("topics", []),
                    "is_ai_related": is_ai_related(f"{full_name} {desc}"),
                })
        except Exception as e:
            print(f"  [WARN] GitHub search '{query[:40]}...' failed: {e}", file=sys.stderr)
            time.sleep(2)  # rate limit

    # AI优先，star降序
    results.sort(key=lambda x: (not x["is_ai_related"], -x["stars"]))
    return results


# ============================================================================
# Source 2: Hacker News (Algolia API) - AI相关热门帖子
# ============================================================================

def fetch_hackernews():
    """通过Algolia HN Search API获取AI相关热门帖子"""
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

                results.append({
                    "source": "hackernews",
                    "title": hit.get("title", ""),
                    "url": hit.get("url", "") or f"https://news.ycombinator.com/item?id={obj_id}",
                    "hn_url": f"https://news.ycombinator.com/item?id={obj_id}",
                    "points": hit.get("points", 0),
                    "num_comments": hit.get("num_comments", 0),
                    "author": hit.get("author", ""),
                    "created_at": hit.get("created_at", ""),
                })
        except Exception as e:
            print(f"  [WARN] HN search '{query}' failed: {e}", file=sys.stderr)

    # 按points降序
    results.sort(key=lambda x: x["points"], reverse=True)
    return results


# ============================================================================
# Source 3: GitHub Topic搜索 - MCP servers
# ============================================================================

def fetch_github_mcp_servers():
    """搜索GitHub上最近创建的MCP server仓库"""
    results = []
    
    # 搜索最近一周push的MCP server仓库，按star排序
    since_date = (datetime.now(CST) - timedelta(days=14)).strftime("%Y-%m-%d")
    queries = [
        f"mcp server pushed:>{since_date} stars:>10",
        f"topic:mcp-server pushed:>{since_date}",
        f"model context protocol pushed:>{since_date} stars:>5",
    ]

    seen_full_names = set()
    
    for query in queries:
        try:
            url = "https://api.github.com/search/repositories"
            params = {
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": 30,
            }
            headers = {"Accept": "application/vnd.github.v3+json"}
            # 尝试不加token先
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for repo in data.get("items", []):
                full_name = repo.get("full_name", "")
                if full_name in seen_full_names:
                    continue
                seen_full_names.add(full_name)

                results.append({
                    "source": "github_mcp",
                    "name": full_name,
                    "url": repo.get("html_url", ""),
                    "description": repo.get("description", "") or "",
                    "stars": repo.get("stargazers_count", 0),
                    "language": repo.get("language", ""),
                    "created_at": repo.get("created_at", ""),
                    "pushed_at": repo.get("pushed_at", ""),
                    "topics": repo.get("topics", []),
                })
        except Exception as e:
            print(f"  [WARN] GitHub MCP search failed: {e}", file=sys.stderr)

    # 按stars降序去重
    results.sort(key=lambda x: x["stars"], reverse=True)
    return results


# ============================================================================
# Source 4: OSSInsight Trending (备用，更稳定)
# ============================================================================

def fetch_ossinsight_trending():
    """通过OSSInsight API获取trending repos"""
    results = []
    
    try:
        url = "https://api.ossinsight.io/v1/repos/trending"
        params = {"period": "last_week", "language": "Python", "n": 30}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for repo in data.get("data", {}).get("rows", []):
            results.append({
                "source": "ossinsight",
                "name": repo.get("repo_name", ""),
                "url": f"https://github.com/{repo.get('repo_name', '')}",
                "description": repo.get("description", "") or "",
                "language": repo.get("language", ""),
                "stars": repo.get("stars", 0),
                "forks": repo.get("forks", 0),
                "score": repo.get("score", 0),
            })
    except Exception as e:
        print(f"  [WARN] OSSInsight trending failed: {e}", file=sys.stderr)

    # 也拉TypeScript
    try:
        params = {"period": "last_week", "language": "TypeScript", "n": 30}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for repo in data.get("data", {}).get("rows", []):
            results.append({
                "source": "ossinsight",
                "name": repo.get("repo_name", ""),
                "url": f"https://github.com/{repo.get('repo_name', '')}",
                "description": repo.get("description", "") or "",
                "language": repo.get("language", ""),
                "stars": repo.get("stars", 0),
                "forks": repo.get("forks", 0),
                "score": repo.get("score", 0),
            })
    except Exception as e:
        print(f"  [WARN] OSSInsight TS trending failed: {e}", file=sys.stderr)

    return results


# ============================================================================
# Main
# ============================================================================

def main():
    print(f"[{datetime.now(CST).isoformat()}] AI Scout starting...")
    
    all_data = {}
    
    # 并发采集（串行实现，但各源独立）
    print("1/4 GitHub Recent (high-star new repos)...")
    all_data["github_recent"] = fetch_github_recent()
    ai_repos = [r for r in all_data["github_recent"] if r["is_ai_related"]]
    print(f"   → {len(all_data['github_recent'])} repos ({len(ai_repos)} AI-related)")
    
    print("2/4 Hacker News...")
    all_data["hackernews"] = fetch_hackernews()
    print(f"   → {len(all_data['hackernews'])} stories")
    
    print("3/4 GitHub MCP Servers...")
    all_data["github_mcp"] = fetch_github_mcp_servers()
    print(f"   → {len(all_data['github_mcp'])} MCP repos")
    
    print("4/4 OSSInsight Trending...")
    all_data["ossinsight"] = fetch_ossinsight_trending()
    print(f"   → {len(all_data['ossinsight'])} repos")
    
    # 统计
    total = sum(len(v) for v in all_data.values())
    
    # 保存原始数据
    output_file = OUTPUT_DIR / f"scout_{TODAY}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "date": TODAY,
            "collected_at": datetime.now(CST).isoformat(),
            "stats": {k: len(v) for k, v in all_data.items()},
            "total": total,
            "data": all_data,
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Done. {total} items collected → {output_file}")
    
    # 输出摘要到stdout（供cron job上下文使用）
    summary = {
        "date": TODAY,
        "stats": {k: len(v) for k, v in all_data.items()},
        "total": total,
        # AI相关高亮
        "ai_recent": ai_repos[:15],
        "top_hn": all_data["hackernews"][:10],
        "top_mcp": all_data["github_mcp"][:10],
    }
    print(f"\n=== SUMMARY JSON ===\n{json.dumps(summary, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
