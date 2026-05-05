"""
Hermes maintenance utilities for AI Scout.

This module provides data-plumbing functions (DB read/write, README fetch).
The actual "thinking" (categorization, quality scoring) is done by Hermes
agent itself via cron jobs — no external LLM API needed.

CLI commands:
  python -m ai_scout.hermes.maintain list-pending [--limit N]
  python -m ai_scout.hermes.maintain fetch-readme <owner/repo>
  python -m ai_scout.hermes.maintain write-manifest <project_id> --json '{...}'
  python -m ai_scout.hermes.maintain batch-pending [--limit N]  # outputs JSON for Hermes to process
  python -m ai_scout.hermes.maintain stats
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

from ..db import get_db, DB_PATH

CST = timezone(timedelta(hours=8))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
README_MAX_CHARS = 15_000


def fetch_readme(full_name: str) -> str:
    """Fetch a repo's README via GitHub API."""
    headers = {"Accept": "application/vnd.github.raw+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    url = f"https://api.github.com/repos/{full_name}/readme"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.text[:README_MAX_CHARS]
        return ""
    except Exception:
        return ""


def get_pending_projects(con, limit: int = 20) -> list[dict]:
    """Get unenriched active projects, highest star count first."""
    sql = """
        SELECT p.id, p.full_name, p.description, p.topics, p.language,
               p.category,
               (SELECT s.stars FROM snapshots s
                WHERE s.project_id = p.id
                ORDER BY s.snapshot_date DESC LIMIT 1) as stars
        FROM projects p
        WHERE p.is_active = 1
          AND (p.last_enriched_at IS NULL OR p.last_enriched_at = '')
        ORDER BY COALESCE((SELECT s.stars FROM snapshots s
                           WHERE s.project_id = p.id
                           ORDER BY s.snapshot_date DESC LIMIT 1), 0) DESC
        LIMIT ?
    """
    return [dict(r) for r in con.execute(sql, (limit,)).fetchall()]


def get_project_detail(con, project_id: int) -> dict | None:
    """Get full project info + README for enrichment."""
    row = con.execute(
        "SELECT * FROM projects WHERE id = ? AND is_active = 1",
        (project_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["topics"] = json.loads(d.get("topics") or "[]")
    d["readme"] = fetch_readme(d["full_name"])
    return d


def write_manifest(con, project_id: int, manifest: dict) -> None:
    """Write a manifest dict to the project row + audit log."""
    now = datetime.now(CST).isoformat()

    before = con.execute(
        "SELECT product_type, summary, llm_quality_score FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()

    solves_json = json.dumps(manifest.get("solves", []), ensure_ascii=False)
    compat_json = json.dumps(manifest.get("compatible_with", ["any"]), ensure_ascii=False)
    install_json = json.dumps(manifest.get("install", {}), ensure_ascii=False)
    requires_json = json.dumps(manifest.get("requires", []), ensure_ascii=False)

    con.execute(
        """UPDATE projects SET
              product_type = ?, summary = ?, solves = ?,
              compatible_with = ?, install = ?, integration_shape = ?,
              requires = ?, llm_quality_score = ?, last_enriched_at = ?
           WHERE id = ?""",
        (
            manifest.get("product_type", "other"),
            manifest.get("summary", ""),
            solves_json,
            compat_json,
            install_json,
            manifest.get("integration_shape", "library"),
            requires_json,
            manifest.get("llm_quality_score", 50),
            now,
            project_id,
        )
    )

    full_name = con.execute("SELECT full_name FROM projects WHERE id = ?", (project_id,)).fetchone()[0]
    diff = {
        "product_type": {"before": dict(before).get("product_type", "") if before else "",
                         "after": manifest.get("product_type", "")},
        "quality": {"before": dict(before).get("llm_quality_score", 0) if before else 0,
                    "after": manifest.get("llm_quality_score", 0)},
    }
    con.execute(
        """INSERT INTO audit_log
              (timestamp, actor, action, target_type, target_id, target_ref, reason, diff)
           VALUES (?, 'hermes', 'enrich', 'project', ?, ?, ?, ?)""",
        (now, project_id, full_name,
         manifest.get("rationale", "auto-enriched by hermes cron"),
         json.dumps(diff, ensure_ascii=False))
    )
    con.commit()
    return full_name


def get_stats(con) -> dict:
    """Database statistics."""
    stats = {}
    stats["total_active"] = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1").fetchone()[0]
    stats["enriched"] = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1 AND last_enriched_at IS NOT NULL AND last_enriched_at != ''").fetchone()[0]
    stats["pending"] = stats["total_active"] - stats["enriched"]

    # Product type distribution
    rows = con.execute("""
        SELECT COALESCE(product_type, '(unclassified)'), COUNT(*)
        FROM projects WHERE is_active=1 GROUP BY product_type ORDER BY COUNT(*) DESC
    """).fetchall()
    stats["by_type"] = {r[0]: r[1] for r in rows}

    # Quality distribution
    rows2 = con.execute("""
        SELECT
            CASE
                WHEN llm_quality_score >= 80 THEN 'excellent(80+)'
                WHEN llm_quality_score >= 50 THEN 'good(50-79)'
                WHEN llm_quality_score >= 20 THEN 'mediocre(20-49)'
                WHEN llm_quality_score IS NOT NULL THEN 'poor(0-19)'
                ELSE 'unclassified'
            END as bucket, COUNT(*)
        FROM projects WHERE is_active=1 GROUP BY bucket
    """).fetchall()
    stats["by_quality"] = {r[0]: r[1] for r in rows2}

    return stats


def main():
    parser = argparse.ArgumentParser(description="AI Scout Hermes maintenance")
    sub = parser.add_subparsers(dest="cmd")

    # list-pending
    p_list = sub.add_parser("list-pending", help="List unenriched projects")
    p_list.add_argument("--limit", type=int, default=20)

    # fetch-readme
    p_readme = sub.add_parser("fetch-readme", help="Fetch README for a repo")
    p_readme.add_argument("full_name")

    # write-manifest
    p_write = sub.add_parser("write-manifest", help="Write manifest to project")
    p_write.add_argument("project_id", type=int)
    group = p_write.add_mutually_exclusive_group(required=True)
    group.add_argument("--json", help="Manifest JSON string")
    group.add_argument("--file", help="Read manifest from file")

    # batch-pending: output project info + READMEs as JSON for Hermes to process
    p_batch = sub.add_parser("batch-pending", help="Output pending projects with READMEs")
    p_batch.add_argument("--limit", type=int, default=5, help="How many to output")
    p_batch.add_argument("--fetch-readme", action="store_true", help="Also fetch READMEs (slower)")

    # detail
    p_detail = sub.add_parser("detail", help="Get project detail + README")
    p_detail.add_argument("project_id", type=int)

    # stats
    sub.add_parser("stats", help="Show DB statistics")

    # reset
    p_reset = sub.add_parser("reset", help="Reset enrichment for a project")
    p_reset.add_argument("project_id", type=int)

    args = parser.parse_args()
    con = get_db()

    if args.cmd == "list-pending":
        projects = get_pending_projects(con, args.limit)
        for p in projects:
            print(f"  [{p['id']:3d}] {p['full_name']:45s} ★{p['stars'] or 0:6.0f}  {(p['description'] or '')[:60]}")

    elif args.cmd == "fetch-readme":
        readme = fetch_readme(args.full_name)
        if readme:
            print(readme[:3000])
        else:
            print("(no README found)")

    elif args.cmd == "write-manifest":
        if args.file:
            with open(args.file) as f:
                manifest = json.load(f)
        else:
            manifest = json.loads(args.json)
        name = write_manifest(con, args.project_id, manifest)
        print(f"OK: enriched {name} (id={args.project_id})")

    elif args.cmd == "batch-pending":
        projects = get_pending_projects(con, args.limit)
        results = []
        for p in projects:
            entry = {
                "id": p["id"],
                "full_name": p["full_name"],
                "description": p["description"],
                "topics": json.loads(p["topics"] or "[]"),
                "language": p["language"],
                "stars": p.get("stars", 0),
                "category": p["category"],
            }
            if args.fetch_readme:
                entry["readme"] = fetch_readme(p["full_name"])
            results.append(entry)
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif args.cmd == "detail":
        d = get_project_detail(con, args.project_id)
        if d:
            # Don't print full README in detail, just metadata
            readme_preview = (d.get("readme") or "")[:500]
            d["readme_preview"] = readme_preview
            del d["readme"]
            print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"Project {args.project_id} not found")

    elif args.cmd == "stats":
        s = get_stats(con)
        print(json.dumps(s, indent=2, ensure_ascii=False))

    elif args.cmd == "reset":
        con.execute(
            "UPDATE projects SET product_type=NULL, summary=NULL, solves=NULL, "
            "compatible_with=NULL, install=NULL, integration_shape=NULL, "
            "requires=NULL, llm_quality_score=NULL, last_enriched_at=NULL "
            "WHERE id = ?", (args.project_id,))
        con.commit()
        print(f"Reset project {args.project_id}")

    else:
        parser.print_help()

    con.close()


if __name__ == "__main__":
    main()
