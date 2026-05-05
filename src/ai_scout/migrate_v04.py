"""
AI Scout v0.4 schema migration

Adds manifest fields to `projects`, plus three new tables:
  - raw_candidates: staging area for unprocessed source hits (Hermes triage queue)
  - audit_log: every Hermes decision (add / update / prune) with reason
  - daily_briefs: cached daily picks per date (read by external MCP server)

Idempotent: safe to run repeatedly. Uses `ALTER TABLE ADD COLUMN` for new columns
(SQLite ignores the statement if the column exists; we catch OperationalError).

Usage:
    python -m ai_scout.migrate_v04          # apply migration
    python -m ai_scout.migrate_v04 --check  # show current schema state, no changes
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from .db import get_db, DB_PATH

CST = timezone(timedelta(hours=8))

# 8 product types covering the full agent-enhancement landscape, plus 'other' fallback.
# NOTE: this replaces the old `category` field's role. We keep `category` populated for
# backward compat but new code should read `product_type`.
PRODUCT_TYPES = [
    "capability_tool",        # MCP servers, Claude skills, plugins, callable tools (做具体事的)
    "memory_infra",           # GBrain, Mem0, Letta, Memori
    "runtime_exec",           # E2B, Browserbase, Daytona, sandboxes
    "framework_orchestration",# LangGraph, CrewAI, AutoGen, DSPy
    "observability_eval",     # Langfuse, LangSmith, Braintrust, Arize
    "routing_gateway",        # LiteLLM, OpenRouter, Portkey
    "knowledge_retrieval",    # Pinecone, Weaviate, RAG infra
    "auth_perm",              # Arcade, Composio, Pipedream OAuth layer
    "other",                  # fallback
]

# How a capability gets integrated into an agent. Drives the install/config UX.
INTEGRATION_SHAPES = [
    "mcp",                # MCP server, install via runtime config
    "skill",              # Claude skill / plugin folder
    "library",            # pip / npm package, code-level integration
    "cli",                # standalone CLI used as a tool
    "sidecar",            # local daemon / sidecar process
    "saas",               # cloud API
    "framework_rewrite",  # requires changing agent's framework or main loop
]

# Runtimes we generate install commands for. Order = priority.
RUNTIMES = ["claude-code", "cursor", "claude-desktop", "generic-mcp", "python", "node"]


# =============================================================================
# Migration steps
# =============================================================================

def _add_column_if_missing(con, table: str, column: str, ddl: str) -> bool:
    """Run ALTER TABLE ADD COLUMN; return True if added, False if it already existed."""
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        return True
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            return False
        raise


def add_manifest_columns(con) -> dict:
    """Add v0.4 manifest fields to projects table. Returns dict of {column: was_added}."""
    columns = [
        ("product_type",      "TEXT DEFAULT ''"),                # one of PRODUCT_TYPES
        ("summary",           "TEXT DEFAULT ''"),                # 50-100 word agent-facing positioning
        ("solves",            "TEXT DEFAULT '[]'"),              # JSON array of task labels
        ("compatible_with",   "TEXT DEFAULT '[]'"),              # JSON array of runtimes
        ("install",           "TEXT DEFAULT '{}'"),              # JSON: runtime -> command
        ("integration_shape", "TEXT DEFAULT ''"),                # one of INTEGRATION_SHAPES
        ("requires",          "TEXT DEFAULT '[]'"),              # JSON array of deps
        ("llm_quality_score", "REAL DEFAULT 0"),                 # 0-100, LLM-assessed agent friendliness
        ("editorial_pin",     "INTEGER DEFAULT 0"),              # 1 = hand-flagged as recommended
        ("last_enriched_at",  "TEXT DEFAULT ''"),                # ISO timestamp of last LLM enrich
        ("why_now",           "TEXT DEFAULT ''"),                # cached reason if currently surfaceable
    ]
    results = {}
    for col, ddl in columns:
        results[col] = _add_column_if_missing(con, "projects", col, ddl)
    return results


NEW_TABLES_SQL = """
-- Staging area for raw source hits before Hermes triage
CREATE TABLE IF NOT EXISTS raw_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                    -- e.g. 'smithery', 'product_hunt', 'github_releases'
    external_id TEXT NOT NULL,               -- source-specific ID for dedup
    payload TEXT NOT NULL,                   -- raw JSON from source
    discovered_at TEXT NOT NULL,             -- ISO timestamp
    triage_status TEXT DEFAULT 'pending',    -- pending | accepted | rejected | merged
    triage_reason TEXT DEFAULT '',           -- why accepted/rejected
    triage_at TEXT DEFAULT '',               -- when triaged
    project_id INTEGER,                      -- if accepted/merged, link to projects.id
    UNIQUE(source, external_id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_raw_candidates_status ON raw_candidates(triage_status);
CREATE INDEX IF NOT EXISTS idx_raw_candidates_source ON raw_candidates(source);

-- Audit trail of Hermes decisions
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,                     -- 'hermes' | 'editor' | 'migration'
    action TEXT NOT NULL,                    -- 'add' | 'update' | 'enrich' | 'prune' | 'pin' | 'reject'
    target_type TEXT NOT NULL,               -- 'project' | 'candidate' | 'daily_brief'
    target_id INTEGER,                       -- foreign id (project.id / candidate.id / etc.)
    target_ref TEXT DEFAULT '',              -- human-readable reference (e.g. 'owner/repo')
    reason TEXT DEFAULT '',                  -- LLM or human justification
    diff TEXT DEFAULT '{}'                   -- JSON of {field: {before, after}} for updates
);

CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);

-- Cached daily picks for the public MCP server
CREATE TABLE IF NOT EXISTS daily_briefs (
    brief_date TEXT PRIMARY KEY,             -- YYYY-MM-DD
    generated_at TEXT NOT NULL,              -- ISO timestamp
    picks TEXT NOT NULL,                     -- JSON array of {project_id, why_now, ...}
    generator_version TEXT DEFAULT 'v0.4'
);
"""


def create_new_tables(con):
    con.executescript(NEW_TABLES_SQL)


def log_migration(con, columns_added: dict):
    """Record this migration in audit_log itself."""
    now = datetime.now(CST).isoformat()
    added = [k for k, v in columns_added.items() if v]
    skipped = [k for k, v in columns_added.items() if not v]
    reason = f"v0.4 schema migration. Added columns: {added}. Already-present (skipped): {skipped}."
    con.execute(
        """INSERT INTO audit_log (timestamp, actor, action, target_type, target_ref, reason)
           VALUES (?, 'migration', 'schema_change', 'database', ?, ?)""",
        (now, str(DB_PATH), reason)
    )


# =============================================================================
# Inspection
# =============================================================================

def describe_state(con) -> dict:
    """Show what the schema currently looks like."""
    state = {}
    cols = con.execute("PRAGMA table_info(projects)").fetchall()
    state["projects_columns"] = [dict(c) for c in cols]

    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    state["tables"] = [r["name"] for r in tables]

    state["counts"] = {}
    for t in ["projects", "snapshots", "scores", "hn_refs",
              "raw_candidates", "audit_log", "daily_briefs"]:
        if t in state["tables"]:
            state["counts"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

    return state


def print_state(state: dict):
    print(f"\n=== Schema state @ {DB_PATH} ===")
    print("\nTables:", ", ".join(state["tables"]))
    print("\nprojects columns:")
    v04_cols = {"product_type", "summary", "solves", "compatible_with", "install",
                "integration_shape", "requires", "llm_quality_score",
                "editorial_pin", "last_enriched_at", "why_now"}
    for c in state["projects_columns"]:
        marker = " [v0.4]" if c["name"] in v04_cols else ""
        print(f"  {c['name']:20s} {c['type']:20s}{marker}")
    print("\nRow counts:")
    for t, n in state["counts"].items():
        print(f"  {t:20s}: {n}")


# =============================================================================
# Main
# =============================================================================

def run_migration() -> dict:
    con = get_db()
    try:
        print("[v0.4] Adding manifest columns to projects...")
        added = add_manifest_columns(con)
        for col, was_added in added.items():
            tag = "ADDED" if was_added else "skip"
            print(f"  [{tag}] {col}")

        print("\n[v0.4] Creating new tables (raw_candidates, audit_log, daily_briefs)...")
        create_new_tables(con)
        print("  done")

        log_migration(con, added)
        con.commit()
        print(f"\n[v0.4] Migration complete @ {DB_PATH}")
        return added
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description="AI Scout v0.4 schema migration")
    parser.add_argument("--check", action="store_true",
                        help="Show current schema state without making changes")
    args = parser.parse_args()

    con = get_db()
    if args.check:
        state = describe_state(con)
        print_state(state)
        con.close()
        return

    con.close()
    run_migration()
    con = get_db()
    print_state(describe_state(con))
    con.close()


if __name__ == "__main__":
    main()
