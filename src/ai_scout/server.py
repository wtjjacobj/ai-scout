"""
AI Scout MCP Server v0.4 — AI Agent Capability Discovery

Tools:
  daily_brief:   Agent pulls today's curated picks (3-5 items, covering different product types)
  recommend:     Agent queries for capabilities matching a natural language task description
  project_detail: Agent gets full manifest for a specific project

FastMCP 3.x, stdio + streamable-http dual mode.
"""

import json
import math
from datetime import datetime, timezone, timedelta

from fastmcp import FastMCP

from .db import get_db
from .hermes.embed import search as semantic_search

CST = timezone(timedelta(hours=8))

mcp = FastMCP(
    "ai-scout",
    version="0.4.0",
    instructions=(
        "AI Scout — AI Agent Capability Discovery Engine. "
        "Helps agents find the right tools, skills, and infrastructure for their tasks. "
        "Use daily_brief() to get today's curated picks, recommend() to find capabilities "
        "matching a specific task, and project_detail() for detailed info on a specific project."
    ),
)


# =============================================================================
# Helpers
# =============================================================================

def _get_manifest_projects(con, limit=20, offset=0, product_type=None,
                           min_quality=0, order_by="quality") -> list[dict]:
    """Query enriched projects with manifest data."""
    where = ["p.is_active = 1", "p.product_type IS NOT NULL", "p.summary IS NOT NULL"]
    params = []

    if product_type:
        where.append("p.product_type = ?")
        params.append(product_type)
    if min_quality > 0:
        where.append("COALESCE(p.llm_quality_score, 0) >= ?")
        params.append(min_quality)

    where_clause = " AND ".join(where)

    # Order options
    order_map = {
        "quality": "COALESCE(p.llm_quality_score, 0) DESC",
        "stars": "COALESCE(s.stars, 0) DESC",
        "freshness": "p.last_enriched_at DESC",
        "name": "p.full_name ASC",
    }
    order_sql = order_map.get(order_by, order_map["quality"])

    sql = f"""
        SELECT p.id, p.full_name, p.url, p.description, p.language,
               p.product_type, p.summary, p.solves, p.compatible_with,
               p.install, p.integration_shape, p.requires,
               p.llm_quality_score, p.last_enriched_at,
               COALESCE(s.stars, 0) as stars
        FROM projects p
        LEFT JOIN snapshots s ON s.project_id = p.id
            AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots WHERE project_id = p.id)
        WHERE {where_clause}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = con.execute(sql, params).fetchall()
    return [_format_manifest(dict(r)) for r in rows]


def _format_manifest(row: dict) -> dict:
    """Parse JSON fields in a manifest row."""
    for field in ("solves", "compatible_with", "install", "requires", "topics"):
        val = row.get(field)
        if isinstance(val, str):
            try:
                row[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                row[field] = []
    return row


def _compute_why_now(con, project_id: int) -> str:
    """Determine why this project is notable today."""
    row = con.execute(
        """SELECT p.last_enriched_at, p.created_at,
                  (SELECT s.stars_7d FROM snapshots s WHERE s.project_id = p.id
                   ORDER BY s.snapshot_date DESC LIMIT 1) as stars_7d,
                  (SELECT s.stars_24h FROM snapshots s WHERE s.project_id = p.id
                   ORDER BY s.snapshot_date DESC LIMIT 1) as stars_24h
           FROM projects p WHERE p.id = ?""",
        (project_id,)
    ).fetchone()
    if not row:
        return "Recently indexed"

    reasons = []
    if row["stars_24h"] and row["stars_24h"] > 100:
        reasons.append(f"hot: +{row['stars_24h']} stars in 24h")
    if row["stars_7d"] and row["stars_7d"] > 500:
        reasons.append(f"trending: +{row['stars_7d']} stars this week")
    if row["last_enriched_at"]:
        enriched = datetime.fromisoformat(row["last_enriched_at"])
        if (datetime.now(CST) - enriched).days < 3:
            reasons.append("newly cataloged")
    if not reasons:
        reasons.append("established capability")
    return reasons[0]


# =============================================================================
# Tools
# =============================================================================

@mcp.tool(
    name="daily_brief",
    description=(
        "Get today's curated picks — 3-5 noteworthy AI agent capabilities, "
        "covering different product types (tools, memory, runtimes, frameworks, etc). "
        "Call once per day. Each item has name, type, summary, why it's notable today, "
        "and install commands."
    ),
)
def daily_brief() -> str:
    con = get_db()
    try:
        # Get enriched projects, one per product_type, ordered by quality + freshness
        # First, get all product types that have at least one enriched project
        types_rows = con.execute(
            """SELECT DISTINCT product_type FROM projects
               WHERE is_active = 1 AND product_type IS NOT NULL AND summary IS NOT NULL
               ORDER BY product_type"""
        ).fetchall()
        available_types = [r["product_type"] for r in types_rows]

        if not available_types:
            return json.dumps({
                "date": datetime.now(CST).strftime("%Y-%m-%d"),
                "items": [],
                "note": "No enriched projects yet. Run enrichment first."
            }, indent=2, ensure_ascii=False)

        picks = []
        seen_ids = set()

        # Pick the top project from each product type (up to 5 types)
        for pt in available_types[:5]:
            row = con.execute(
                """SELECT p.id, p.full_name, p.url, p.description, p.product_type,
                          p.summary, p.solves, p.compatible_with, p.install,
                          p.integration_shape, p.requires, p.llm_quality_score,
                          COALESCE(s.stars, 0) as stars
                   FROM projects p
                   LEFT JOIN snapshots s ON s.project_id = p.id
                       AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots WHERE project_id = p.id)
                   WHERE p.is_active = 1 AND p.product_type = ? AND p.summary IS NOT NULL
                   ORDER BY COALESCE(p.llm_quality_score, 0) * 0.6 + COALESCE(s.stars, 0) / 1000.0 DESC
                   LIMIT 1""",
                (pt,)
            ).fetchone()
            if row and row["id"] not in seen_ids:
                manifest = _format_manifest(dict(row))
                manifest["why_now"] = _compute_why_now(con, row["id"])
                picks.append(manifest)
                seen_ids.add(row["id"])

        # Sort picks by quality score descending
        picks.sort(key=lambda x: x.get("llm_quality_score", 0), reverse=True)

        result = {
            "date": datetime.now(CST).strftime("%Y-%m-%d"),
            "total_enriched": con.execute(
                "SELECT COUNT(*) FROM projects WHERE is_active=1 AND product_type IS NOT NULL"
            ).fetchone()[0],
            "items": picks,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool(
    name="recommend",
    description=(
        "Find AI agent capabilities matching a task description. "
        "Returns 3-5 ranked candidates with install commands, trade-offs, and alternatives. "
        "Example queries: 'extract tables from PDFs', 'add long-term memory to my agent', "
        "'browse the web and fill forms'."
    ),
)
def recommend(
    query: str,
    product_type: str = "",
    runtime: str = "",
    open_source_only: bool = False,
    limit: int = 5,
) -> str:
    """Find capabilities matching a natural language task description."""
    con = get_db()
    try:
        # PRIMARY: Semantic search via TF-IDF (if index exists)
        semantic_results = semantic_search(query, limit=limit)

        if semantic_results:
            # Add why_now and extra context
            for r in semantic_results:
                r["why_now"] = _compute_why_now(con, r["id"])

            result = {
                "query": query,
                "total_candidates": len(semantic_results),
                "search_mode": "semantic",
                "candidates": semantic_results,
                "filters_applied": {
                    "product_type": product_type or "any",
                    "runtime": runtime or "any",
                    "open_source_only": open_source_only,
                }
            }
            return json.dumps(result, indent=2, ensure_ascii=False)

        # FALLBACK: Keyword search across summary + solves + description
        # Split query into keywords for FTS-like matching
        keywords = query.lower().split()
        conditions = ["p.is_active = 1", "p.product_type IS NOT NULL", "p.summary IS NOT NULL"]
        params = []

        if product_type:
            conditions.append("p.product_type = ?")
            params.append(product_type)

        if runtime:
            conditions.append("(p.compatible_with LIKE ? OR p.compatible_with LIKE '%any%')")
            params.append(f'%"{runtime}"%')

        where_clause = " AND ".join(conditions)

        # Score each project by keyword match relevance
        # Build a relevance expression
        match_parts = []
        for kw in keywords[:5]:  # limit to 5 keywords
            match_parts.append(
                f"""(CASE WHEN LOWER(p.summary) LIKE '%{kw}%' THEN 3 ELSE 0 END
                     + CASE WHEN LOWER(p.solves) LIKE '%{kw}%' THEN 4 ELSE 0 END
                     + CASE WHEN LOWER(p.description) LIKE '%{kw}%' THEN 2 ELSE 0 END
                     + CASE WHEN LOWER(p.product_type) LIKE '%{kw}%' THEN 1 ELSE 0 END)"""
            )
        relevance_expr = " + ".join(match_parts) if match_parts else "0"

        sql = f"""
            SELECT p.id, p.full_name, p.url, p.description, p.product_type,
                   p.summary, p.solves, p.compatible_with, p.install,
                   p.integration_shape, p.requires, p.llm_quality_score,
                   COALESCE(s.stars, 0) as stars,
                   ({relevance_expr}) as relevance
            FROM projects p
            LEFT JOIN snapshots s ON s.project_id = p.id
                AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots WHERE project_id = p.id)
            WHERE {where_clause}
            ORDER BY relevance DESC, COALESCE(p.llm_quality_score, 0) DESC
            LIMIT ?
        """
        params.append(limit)

        rows = con.execute(sql, params).fetchall()
        candidates = [_format_manifest(dict(r)) for r in rows]

        # If no keyword matches, fall back to quality-based selection
        if not candidates or (candidates and candidates[0].get("relevance", 0) == 0):
            fallback = _get_manifest_projects(
                con, limit=limit, product_type=product_type or None,
                order_by="quality"
            )
            if fallback:
                candidates = fallback

        result = {
            "query": query,
            "total_candidates": len(candidates),
            "candidates": candidates,
            "filters_applied": {
                "product_type": product_type or "any",
                "runtime": runtime or "any",
                "open_source_only": open_source_only,
            }
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        con.close()


@mcp.tool(
    name="project_detail",
    description=(
        "Get the full manifest for a specific project by full_name (owner/repo). "
        "Returns product_type, summary, solves, install commands, quality score, "
        "star count, and compatibility info."
    ),
)
def project_detail(full_name: str) -> str:
    """Get detailed manifest for a specific project."""
    con = get_db()
    try:
        row = con.execute(
            """SELECT p.*,
                      COALESCE(s.stars, 0) as stars,
                      COALESCE(s.forks, 0) as forks,
                      COALESCE(s.stars_7d, 0) as stars_7d,
                      COALESCE(s.stars_24h, 0) as stars_24h,
                      sc.composite_score, sc.momentum_score, sc.quality_score
               FROM projects p
               LEFT JOIN snapshots s ON s.project_id = p.id
                   AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots WHERE project_id = p.id)
               LEFT JOIN scores sc ON sc.project_id = p.id
                   AND sc.score_date = (SELECT MAX(score_date) FROM scores WHERE project_id = p.id)
               WHERE p.full_name = ?""",
            (full_name,)
        ).fetchone()

        if not row:
            return json.dumps({"error": f"Project not found: {full_name}"}, ensure_ascii=False)

        result = _format_manifest(dict(row))
        result["why_now"] = _compute_why_now(con, row["id"])

        # Add audit trail
        audits = con.execute(
            "SELECT timestamp, action, reason FROM audit_log WHERE target_ref = ? ORDER BY timestamp DESC LIMIT 3",
            (full_name,)
        ).fetchall()
        result["audit_trail"] = [dict(a) for a in audits]

        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        con.close()


# =============================================================================
# Entry point
# =============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI Scout MCP Server v0.4")
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
