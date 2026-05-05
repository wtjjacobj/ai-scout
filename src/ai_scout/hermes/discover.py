"""
AI Scout Discovery — 新项目发现引擎

从多个来源发现新的 AI agent 工具和基础设施：
1. GitHub Search: 新项目 (7d) + MCP servers (14d) + agent skills
2. Smithery API: MCP 注册表
3. npm/pypi: 新发布的 agent/mcp/skill 相关包
4. awesome-mcp-servers: 监控列表变更
5. GitHub Releases: 核心基础设施 watchlist 的新版本
6. HN AI keywords: Hacker News 热门 AI 讨论

去重逻辑：与现有 projects 表比对，新项目写入 raw_candidates 表待 triage。

Usage:
  python -m ai_scout.hermes.discover           # 全量发现
  python -m ai_scout.hermes.discover --github   # 只扫 GitHub
  python -m ai_scout.hermes.discover --smithery # 只扫 Smithery
  python -m ai_scout.hermes.discover --npm      # 只扫 npm
  python -m ai_scout.hermes.discover --status   # 看候选数量
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

DB_PATH = Path(os.environ.get(
    "AI_SCOUT_DB",
    str(Path(__file__).parents[2] / "data" / "ai_scout.db")
))
CST = timezone(timedelta(hours=8))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def is_already_known(con, full_name: str) -> bool:
    """Check if project already exists in projects or raw_candidates."""
    r1 = con.execute("SELECT id FROM projects WHERE full_name = ?", (full_name,)).fetchone()
    if r1:
        return True
    # Check raw_candidates payload for this full_name (last 7 days)
    cutoff = (datetime.now(CST) - timedelta(days=7)).isoformat()
    r2 = con.execute(
        "SELECT id FROM raw_candidates WHERE external_id = ? AND discovered_at > ?",
        (full_name, cutoff)
    ).fetchone()
    return r2 is not None


def add_candidate(con, full_name: str, url: str, source: str, **kwargs):
    """Add a new candidate to raw_candidates table (v0.4 schema)."""
    now = datetime.now(CST).isoformat()
    payload = {
        "full_name": full_name,
        "url": url,
        "description": kwargs.get("description", ""),
        "language": kwargs.get("language", ""),
        "topics": kwargs.get("topics", []),
        "stars": kwargs.get("stars", 0),
        **{k: v for k, v in kwargs.items()
           if k not in ("description", "language", "topics", "stars")}
    }
    try:
        con.execute(
            """INSERT OR IGNORE INTO raw_candidates
               (source, external_id, payload, discovered_at, triage_status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (source, full_name, json.dumps(payload, ensure_ascii=False), now)
        )
        return True
    except Exception as e:
        print(f"  [WARN] Failed to add candidate {full_name}: {e}", file=sys.stderr)
        return False


# =============================================================================
# Source 1: GitHub Search
# =============================================================================

def discover_github(con) -> int:
    """Discover new projects from GitHub Search API."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    since_7d = (datetime.now(CST) - timedelta(days=7)).strftime("%Y-%m-%d")
    since_14d = (datetime.now(CST) - timedelta(days=14)).strftime("%Y-%m-%d")

    queries = [
        # New hot AI projects
        f"created:>{since_7d} stars:>50 topic:mcp",
        f"created:>{since_7d} stars:>50 topic:agent",
        f"created:>{since_7d} stars:>100 topic:ai",
        # MCP servers (broader time window)
        f"mcp-server pushed:>{since_14d} stars:>10",
        f"topic:mcp-server pushed:>{since_14d}",
        f"model context protocol pushed:>{since_14d} stars:>5",
        # Agent tools & skills
        f"claude skill pushed:>{since_14d} stars:>5",
        f"agent tool pushed:>{since_14d} stars:>20",
        # Specific infra categories
        f"ai memory agent pushed:>{since_14d} stars:>10",
        f"agent framework pushed:>{since_14d} stars:>20",
        f"rag mcp pushed:>{since_14d} stars:>5",
        f"browser agent pushed:>{since_14d} stars:>10",
    ]

    found = 0
    for query in queries:
        try:
            resp = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 30},
                headers=headers,
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"  GitHub API {resp.status_code} for: {query[:50]}")
                continue

            items = resp.json().get("items", [])
            for repo in items:
                full_name = repo.get("full_name", "")
                if not full_name or is_already_known(con, full_name):
                    continue
                added = add_candidate(
                    con, full_name,
                    url=repo.get("html_url", ""),
                    source="github_search",
                    description=repo.get("description", ""),
                    language=repo.get("language", ""),
                    topics=repo.get("topics", []),
                    stars=repo.get("stargazers_count", 0),
                )
                if added:
                    found += 1

            # Respect rate limit
            remaining = resp.headers.get("X-RateLimit-Remaining", "10")
            if int(remaining) < 5:
                print(f"  GitHub rate limit low ({remaining}), waiting 60s...")
                time.sleep(60)
            else:
                time.sleep(2)

        except Exception as e:
            print(f"  [WARN] GitHub query failed: {e}", file=sys.stderr)

    return found


# =============================================================================
# Source 2: Smithery (MCP Registry)
# =============================================================================

def discover_smithery(con) -> int:
    """Discover new MCP servers from Smithery registry."""
    found = 0
    try:
        # Smithery search API
        for page in range(1, 4):  # First 3 pages
            resp = requests.get(
                "https://smithery.ai/api/servers",
                params={"pageSize": 30, "page": page},
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"  Smithery API returned {resp.status_code}")
                break

            data = resp.json()
            servers = data if isinstance(data, list) else data.get("servers", [])

            for server in servers:
                name = server.get("fullName") or server.get("name", "")
                # Try to construct GitHub URL from name
                if "/" in name:
                    full_name = name
                    url = f"https://github.com/{name}"
                else:
                    full_name = f"smithery/{name}"
                    url = server.get("url", f"https://smithery.ai/server/{name}")

                if is_already_known(con, full_name):
                    continue

                added = add_candidate(
                    con, full_name,
                    url=url,
                    source="smithery",
                    description=server.get("description", ""),
                    stars=server.get("stars", 0) or server.get("downloads", 0),
                )
                if added:
                    found += 1

            time.sleep(1)

    except Exception as e:
        print(f"  [WARN] Smithery discovery failed: {e}", file=sys.stderr)

    return found


# =============================================================================
# Source 3: npm & PyPI new packages
# =============================================================================

def discover_npm(con) -> int:
    """Discover new npm packages related to agent/mcp."""
    found = 0
    keywords = ["mcp-server", "mcp-tool", "agent-tool", "claude-skill", "langchain-tool"]

    for kw in keywords:
        try:
            resp = requests.get(
                "https://registry.npmjs.org/-/v1/search",
                params={"text": kw, "size": 20},
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            results = resp.json().get("objects", [])
            for obj in results:
                pkg = obj.get("package", {})
                name = pkg.get("name", "")
                links = pkg.get("links", {})
                repo_url = links.get("repository", links.get("npm", ""))

                # Extract GitHub full_name from repo URL
                full_name = ""
                if "github.com" in repo_url:
                    match = re.search(r"github\.com/([^/]+/[^/]+)", repo_url)
                    if match:
                        full_name = match.group(1).rstrip(".git")

                if not full_name or is_already_known(con, full_name):
                    continue

                added = add_candidate(
                    con, full_name,
                    url=repo_url,
                    source="npm",
                    description=pkg.get("description", ""),
                    language="TypeScript",
                    stars=0,
                    package_name=name,
                    package_version=pkg.get("version", ""),
                )
                if added:
                    found += 1

            time.sleep(1)
        except Exception as e:
            print(f"  [WARN] npm search failed for '{kw}': {e}", file=sys.stderr)

    return found


def discover_pypi(con) -> int:
    """Discover new PyPI packages related to agent/mcp."""
    found = 0
    keywords = ["mcp-server", "agent-tool", "langchain", "claude-skill", "ai-agent"]

    for kw in keywords:
        try:
            resp = requests.get(
                "https://pypi.org/search/",
                params={"q": kw, "o": "-created", "c": "Programming Language :: Python :: 3"},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            # PyPI search doesn't have a clean JSON API, skip if no results
            if resp.status_code != 200:
                continue
            time.sleep(1)
        except Exception:
            pass

    return found


# =============================================================================
# Source 4: Watchlist (Core infra releases)
# =============================================================================

# Key projects to watch for major releases
WATCHLIST = [
    # Memory
    "mem0ai/mem0", "letta-ai/letta", "cpacker/MemGPT",
    # Runtimes
    "e2b-dev/E2B", "browserbase/node-sdk", "daytonaio/daytona",
    # Frameworks
    "langchain-ai/langgraph", "crewAIInc/crewAI", "microsoft/autogen",
    "stanfordnlp/dspy",
    # Observability
    "langfuse/langfuse", "langchain-ai/langsmith-sdk",
    # Routing
    "BerriAI/litellm",
    # Knowledge
    "chroma-core/chroma", "qdrant/qdrant",
    # MCP SDKs
    "modelcontextprotocol/python-sdk", "modelcontextprotocol/typescript-sdk",
    # Browser agents
    "browser-use/browser-use", "browser-use/pydoll",
    # Design / creative
    "openai/openai-agents-python",
    # Tools
    "upstash/context7", "github/github-mcp-server",
    "microsoft/playwright-mcp", "ChromeDevTools/chrome-devtools-mcp",
    "PrefectHQ/fastmcp",
]

def discover_watchlist(con) -> int:
    """Check watchlist projects for major updates."""
    found = 0
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    for repo in WATCHLIST:
        try:
            # Get latest release
            resp = requests.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            release = resp.json()
            published = release.get("published_at", "")
            tag = release.get("tag_name", "")

            # Check if release is within last 7 days
            if published:
                pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - pub_date).days > 7:
                    continue

            # If not already known, add as candidate
            if not is_already_known(con, repo):
                desc_resp = requests.get(
                    f"https://api.github.com/repos/{repo}",
                    headers=headers,
                    timeout=10,
                )
                desc = ""
                stars = 0
                if desc_resp.status_code == 200:
                    desc = desc_resp.json().get("description", "")
                    stars = desc_resp.json().get("stargazers_count", 0)

                added = add_candidate(
                    con, repo,
                    url=f"https://github.com/{repo}",
                    source="watchlist_release",
                    description=desc,
                    stars=stars,
                    release_tag=tag,
                    release_notes_url=release.get("html_url", ""),
                )
                if added:
                    found += 1

            time.sleep(1)
        except Exception:
            pass

    return found


# =============================================================================
# Source 5: Awesome-list monitoring
# =============================================================================

def discover_awesome_mcp(con) -> int:
    """Parse awesome-mcp-servers README for new entries."""
    found = 0
    try:
        resp = requests.get(
            "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
            timeout=20,
        )
        if resp.status_code != 200:
            return 0

        # Find all GitHub links
        github_repos = set(re.findall(
            r'https://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)',
            resp.text
        ))

        for full_name in github_repos:
            # Skip orgs/pages that aren't repos
            if "." in full_name.split("/")[-1]:
                continue
            if is_already_known(con, full_name):
                continue

            added = add_candidate(
                con, full_name,
                url=f"https://github.com/{full_name}",
                source="awesome-mcp-servers",
                description="",
                stars=0,
            )
            if added:
                found += 1

    except Exception as e:
        print(f"  [WARN] awesome-mcp-servers parse failed: {e}", file=sys.stderr)

    return found


# =============================================================================
# Triage: filter candidates
# =============================================================================

AGENT_KEYWORDS = [
    "mcp", "agent", "claude", "cursor", "langchain", "langgraph",
    "tool", "skill", "automation", "workflow", "rag", "memory",
    "embedding", "vector", "browser", "sandbox", "runtime",
    "inference", "llm", "model", "ai", "copilot", "openai",
    "anthropic", "deepseek", "ollama", "vllm",
]

def triage_candidates(con, limit=50) -> int:
    """Filter pending candidates: keep agent-related ones, drop irrelevant."""
    rows = con.execute(
        """SELECT id, external_id, payload
           FROM raw_candidates
           WHERE triage_status = 'pending'
           ORDER BY discovered_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()

    promoted = 0
    dropped = 0

    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            pass

        full_name = row["external_id"]
        description = payload.get("description", "")
        text = f"{full_name} {description}"

        is_agent_related = any(kw in text.lower() for kw in AGENT_KEYWORDS)

        if is_agent_related:
            # Promote to projects table
            url = payload.get("url", f"https://github.com/{full_name}")
            now = datetime.now(CST).isoformat()

            try:
                con.execute(
                    """INSERT OR IGNORE INTO projects
                       (full_name, url, description, language, topics, category,
                        source, first_seen, last_seen, is_active)
                       VALUES (?, ?, ?, '', '[]', '', ?, ?, ?, 1)""",
                    (full_name, url, description, "discovery", now, now)
                )
                project_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute(
                    "UPDATE raw_candidates SET triage_status = 'accepted', project_id = ?, triage_reason = ?, triage_at = ? WHERE id = ?",
                    (project_id, "auto-promoted: agent keyword match", now, row["id"])
                )
                promoted += 1
            except Exception:
                pass
        else:
            con.execute(
                "UPDATE raw_candidates SET triage_status = 'rejected', triage_reason = ?, triage_at = ? WHERE id = ?",
                ("no agent keywords found", datetime.now(CST).isoformat(), row["id"])
            )
            dropped += 1

    con.commit()
    return promoted


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="AI Scout Discovery Engine")
    parser.add_argument("--github", action="store_true", help="Only GitHub search")
    parser.add_argument("--smithery", action="store_true", help="Only Smithery")
    parser.add_argument("--npm", action="store_true", help="Only npm")
    parser.add_argument("--watchlist", action="store_true", help="Only watchlist releases")
    parser.add_argument("--awesome", action="store_true", help="Only awesome-mcp-servers")
    parser.add_argument("--triage", action="store_true", help="Only triage pending candidates")
    parser.add_argument("--status", action="store_true", help="Show candidate stats")
    args = parser.parse_args()

    con = get_db()

    if args.status:
        pending = con.execute("SELECT COUNT(*) FROM raw_candidates WHERE triage_status='pending'").fetchone()[0]
        accepted = con.execute("SELECT COUNT(*) FROM raw_candidates WHERE triage_status='accepted'").fetchone()[0]
        rejected = con.execute("SELECT COUNT(*) FROM raw_candidates WHERE triage_status='rejected'").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM raw_candidates").fetchone()[0]
        print(f"Raw candidates: {total} total ({pending} pending, {accepted} accepted, {rejected} rejected)")
        con.close()
        return

    total_new = 0
    run_all = not any([args.github, args.smithery, args.npm, args.watchlist, args.awesome, args.triage])

    if run_all or args.github:
        print("[1/5] GitHub search...")
        n = discover_github(con)
        print(f"  Found {n} new candidates")
        total_new += n

    if run_all or args.smithery:
        print("[2/5] Smithery registry...")
        n = discover_smithery(con)
        print(f"  Found {n} new candidates")
        total_new += n

    if run_all or args.npm:
        print("[3/5] npm packages...")
        n = discover_npm(con)
        print(f"  Found {n} new candidates")
        total_new += n

    if run_all or args.watchlist:
        print("[4/5] Watchlist releases...")
        n = discover_watchlist(con)
        print(f"  Found {n} new candidates")
        total_new += n

    if run_all or args.awesome:
        print("[5/5] awesome-mcp-servers...")
        n = discover_awesome_mcp(con)
        print(f"  Found {n} new candidates")
        total_new += n

    if total_new > 0:
        con.commit()

    # Triage
    if run_all or args.triage:
        print(f"\n[Triage] Processing candidates...")
        promoted = triage_candidates(con)
        print(f"  Promoted {promoted} to projects table")

    print(f"\n=== Discovery complete: {total_new} new candidates found ===")
    con.close()


if __name__ == "__main__":
    main()
