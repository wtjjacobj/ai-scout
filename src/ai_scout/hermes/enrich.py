"""
Hermes task: enrich a project's manifest from its README via LLM.

Reads a project from `projects`, fetches its README, asks an LLM to produce a
v0.4 manifest (product_type / summary / solves / install / etc.), writes the
fields back, logs the decision to audit_log.

Designed to be runnable two ways:

  CLI (single project):
    python -m ai_scout.hermes.enrich --full-name owner/repo
    python -m ai_scout.hermes.enrich --id 42 --dry-run

  CLI (batch):
    python -m ai_scout.hermes.enrich --limit 20            # 20 unenriched
    python -m ai_scout.hermes.enrich --all                  # all unenriched
    python -m ai_scout.hermes.enrich --refresh-older 30d   # re-enrich stale ones

  Library:
    from ai_scout.hermes.enrich import enrich_project
    manifest = enrich_project(con, project_id, dry_run=False)

Requires AI_SCOUT_LLM_API_KEY in env. Default model is
glm-5.1 (override with AI_SCOUT_LLM_MODEL or --model).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from ..db import get_db, CST
from ..migrate_v04 import PRODUCT_TYPES, INTEGRATION_SHAPES, RUNTIMES

DEFAULT_MODEL = os.environ.get("AI_SCOUT_LLM_MODEL", "glm-5.1")
DEFAULT_BASE_URL = os.environ.get("AI_SCOUT_LLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
DEFAULT_API_KEY = os.environ.get("AI_SCOUT_LLM_API_KEY", "")
README_MAX_CHARS = 12_000  # Trim long READMEs to keep token cost predictable


# =============================================================================
# README fetching
# =============================================================================

def fetch_readme(full_name: str, github_token: str | None = None) -> str:
    """Fetch a repo's README via GitHub API. Returns plain text or empty string."""
    headers = {"Accept": "application/vnd.github.raw+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    # Try main branch first
    url = f"https://api.github.com/repos/{full_name}/readme"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.text[:README_MAX_CHARS]
        if resp.status_code == 404:
            return ""
        print(f"  [WARN] README fetch returned {resp.status_code} for {full_name}",
              file=sys.stderr)
        return ""
    except requests.RequestException as e:
        print(f"  [WARN] README fetch failed for {full_name}: {e}", file=sys.stderr)
        return ""


# =============================================================================
# LLM prompt
# =============================================================================

_PRODUCT_TYPE_DESCRIPTIONS = {
    "capability_tool": "MCP servers, Claude skills, plugins, callable wrappers — things an agent invokes to perform a specific task (e.g. extract PDF tables, scrape a site, query a database).",
    "memory_infra": "Persistent memory or knowledge-graph layers for agents (e.g. Mem0, Letta, GBrain, Memori). Stores long-term context across sessions.",
    "runtime_exec": "Sandboxes / execution environments where agents run code or browse (e.g. E2B, Browserbase, Daytona). Provides the substrate, not the action.",
    "framework_orchestration": "Agent frameworks for planning, multi-agent coordination, or workflow definition (e.g. LangGraph, CrewAI, AutoGen, DSPy).",
    "observability_eval": "Logging, tracing, eval harnesses for agents (e.g. Langfuse, LangSmith, Braintrust, Arize). Helps developers see what agents are doing.",
    "routing_gateway": "Model/tool gateways and routers (e.g. LiteLLM, OpenRouter, Portkey). Sit between agent and underlying model APIs.",
    "knowledge_retrieval": "Vector DBs, RAG infrastructure, retrieval pipelines (e.g. Pinecone, Weaviate, Chroma). Information storage for retrieval.",
    "auth_perm": "Auth/permissions layers that let agents safely call third-party services on behalf of a user (e.g. Arcade, Composio, Pipedream OAuth).",
    "other": "Doesn't fit any of the above categories cleanly — informational repo, demo, awesome-list, training data, model weights, etc.",
}

_SYSTEM_PROMPT = """You are an expert curator for an AI agent capability registry. Given a GitHub project's metadata and README, produce a structured manifest describing what it is and how an AI agent would use it.

Output ONLY valid JSON matching the schema given via the `emit_manifest` tool. Do not include any prose before or after.

Be concise and precise. The audience is another AI agent that will read your manifest to decide whether to install this. Avoid marketing language. State capabilities in concrete terms."""


def _user_prompt(full_name: str, description: str, topics: list, language: str,
                 readme: str) -> str:
    type_desc_block = "\n".join(
        f"  - {pt}: {desc}" for pt, desc in _PRODUCT_TYPE_DESCRIPTIONS.items()
    )
    integration_desc = """
  - mcp: an MCP server installed via runtime config (claude-code, cursor, claude-desktop)
  - skill: a Claude skill / plugin folder
  - library: a code-level dependency (pip / npm package)
  - cli: standalone command-line tool used as an agent tool
  - sidecar: a local daemon / sidecar process
  - saas: a hosted cloud API (no local install, just credentials)
  - framework_rewrite: requires changing the agent's framework or main loop"""

    return f"""Project: {full_name}
Language: {language or 'unknown'}
Topics: {', '.join(topics) if topics else '(none)'}
Description (one-line): {description or '(none)'}

README (first {README_MAX_CHARS} chars):
---
{readme[:README_MAX_CHARS] if readme else '(README not available)'}
---

Classify this project into ONE product_type from:
{type_desc_block}

Pick integration_shape from:
{integration_desc}

Provide install commands ONLY for runtimes the project actually supports. If
unsure, omit. Common targets: claude-code, cursor, claude-desktop, generic-mcp,
python, node.

For `solves`, list 2-5 short task labels in present-tense English ("extract
tables from PDFs", "manage agent long-term memory"). These will be embedded
for semantic search.

For `summary`, write 50-100 words explaining what this is, who/what it's for,
and why an agent would install it. No marketing tone.

For `llm_quality_score` (0-100), assess agent-friendliness:
  - 80-100: excellent docs, clear tool descriptions, easy install, mature
  - 50-79: usable but rough edges (sparse docs / unclear schemas / heavy setup)
  - 20-49: experimental / poorly documented / niche
  - 0-19: probably not worth surfacing
"""


# Tool schema for structured output
ENRICH_TOOL = {
    "name": "emit_manifest",
    "description": "Emit the v0.4 manifest for this project.",
    "input_schema": {
        "type": "object",
        "properties": {
            "product_type": {"type": "string", "enum": PRODUCT_TYPES},
            "summary": {
                "type": "string",
                "description": "50-100 words, agent-facing positioning"
            },
            "solves": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-5 task labels in present-tense English"
            },
            "compatible_with": {
                "type": "array",
                "items": {"type": "string", "enum": RUNTIMES + ["any"]},
                "description": "Runtimes this works with. Use 'any' for runtime-agnostic."
            },
            "install": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Map of runtime -> install command. Only include runtimes that work."
            },
            "integration_shape": {
                "type": "string",
                "enum": INTEGRATION_SHAPES,
            },
            "requires": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hard dependencies (e.g. 'Node 18+', 'OpenAI API key', 'Docker')"
            },
            "llm_quality_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
            },
            "rationale": {
                "type": "string",
                "description": "1-2 sentence justification for the classification + score, used for audit_log"
            }
        },
        "required": [
            "product_type", "summary", "solves", "compatible_with",
            "install", "integration_shape", "requires", "llm_quality_score",
            "rationale"
        ],
    },
}


# =============================================================================
# LLM call
# =============================================================================

def call_llm(full_name: str, description: str, topics: list, language: str,
             readme: str, model: str, api_key: str, base_url: str = None) -> dict:
    """Call OpenAI-compatible API with JSON-mode to get structured manifest.
    Works with zhipu (GLM), deepseek, or any OpenAI-compatible provider.
    Uses response_format=json for maximum compatibility instead of tool_use."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai SDK not installed. Run: pip install openai")

    base_url = base_url or DEFAULT_BASE_URL
    client = OpenAI(api_key=api_key, base_url=base_url)

    user_prompt = _user_prompt(full_name, description, topics, language, readme)

    # Build the JSON schema instruction for the LLM
    schema_desc = """Output ONLY a valid JSON object with these fields:
{
  "product_type": one of ["capability_tool", "memory_infra", "runtime_exec", "framework_orchestration", "observability_eval", "routing_gateway", "knowledge_retrieval", "auth_perm", "other"],
  "summary": "50-100 words, agent-facing positioning",
  "solves": ["task label 1", "task label 2", ...],
  "compatible_with": ["claude-code", "cursor", "python", "node", "any", ...],
  "install": {"claude-code": "...", "python": "pip install ...", ...},
  "integration_shape": one of ["mcp", "skill", "library", "cli", "sidecar", "saas", "framework_rewrite"],
  "requires": ["Node 18+", "Docker", ...],
  "llm_quality_score": 0-100,
  "rationale": "1-2 sentence justification"
}"""

    json_system = _SYSTEM_PROMPT + "\n\n" + schema_desc

    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": json_system},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content
        if content:
            result = json.loads(content)
            # Validate required fields
            for field in ["product_type", "summary", "solves", "integration_shape", "llm_quality_score"]:
                if field not in result:
                    raise ValueError(f"Missing field: {field}")
            # Fill defaults for optional fields
            result.setdefault("compatible_with", ["any"])
            result.setdefault("install", {})
            result.setdefault("requires", [])
            result.setdefault("rationale", "")
            return result
    except Exception as e:
        print(f"  [WARN] JSON mode failed ({e}), trying plain text parse...", file=sys.stderr)

    # Fallback: plain text completion and try to extract JSON
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt + "\n\nOutput ONLY valid JSON. No markdown fences."},
        ],
    )
    content = resp.choices[0].message.content
    if content:
        # Strip markdown fences if present
        content = re.sub(r'^```(?:json)?\s*', '', content.strip())
        content = re.sub(r'\s*```\s*$', '', content.strip())
        return json.loads(content)

    raise RuntimeError(f"LLM returned empty response for {full_name}")


# =============================================================================
# DB write + audit log
# =============================================================================

def write_manifest(con, project_id: int, full_name: str,
                   manifest: dict, dry_run: bool = False) -> None:
    """Update project row with manifest fields and log to audit_log."""
    if dry_run:
        return

    now = datetime.now(CST).isoformat()
    rationale = manifest.pop("rationale", "")

    # Capture before-state for diff
    before = con.execute(
        """SELECT product_type, summary, llm_quality_score, last_enriched_at
           FROM projects WHERE id = ?""",
        (project_id,)
    ).fetchone()
    before_dict = dict(before) if before else {}

    # Serialize JSON fields
    solves_json = json.dumps(manifest["solves"], ensure_ascii=False)
    compat_json = json.dumps(manifest["compatible_with"], ensure_ascii=False)
    install_json = json.dumps(manifest["install"], ensure_ascii=False)
    requires_json = json.dumps(manifest["requires"], ensure_ascii=False)

    con.execute(
        """UPDATE projects SET
              product_type = ?,
              summary = ?,
              solves = ?,
              compatible_with = ?,
              install = ?,
              integration_shape = ?,
              requires = ?,
              llm_quality_score = ?,
              last_enriched_at = ?
           WHERE id = ?""",
        (
            manifest["product_type"],
            manifest["summary"],
            solves_json,
            compat_json,
            install_json,
            manifest["integration_shape"],
            requires_json,
            manifest["llm_quality_score"],
            now,
            project_id,
        )
    )

    # Audit log entry
    diff = {
        "product_type": {"before": before_dict.get("product_type", ""),
                         "after": manifest["product_type"]},
        "llm_quality_score": {"before": before_dict.get("llm_quality_score", 0),
                              "after": manifest["llm_quality_score"]},
        "summary_len": {"before": len(before_dict.get("summary") or ""),
                        "after": len(manifest["summary"])},
    }
    con.execute(
        """INSERT INTO audit_log
              (timestamp, actor, action, target_type, target_id, target_ref, reason, diff)
           VALUES (?, 'hermes', 'enrich', 'project', ?, ?, ?, ?)""",
        (now, project_id, full_name, rationale, json.dumps(diff, ensure_ascii=False))
    )


# =============================================================================
# Public API
# =============================================================================

def enrich_project(con, project_id: int, *, model: str = DEFAULT_MODEL,
                   api_key: str | None = None,
                   base_url: str | None = None,
                   github_token: str | None = None,
                   dry_run: bool = False, verbose: bool = True) -> dict:
    """Enrich a single project. Returns the manifest dict."""
    api_key = api_key or DEFAULT_API_KEY
    if not api_key:
        raise RuntimeError("AI_SCOUT_LLM_API_KEY not set")
    base_url = base_url or DEFAULT_BASE_URL
    github_token = github_token or os.environ.get("GITHUB_TOKEN")

    row = con.execute(
        "SELECT id, full_name, description, topics, language FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"No project with id={project_id}")

    full_name = row["full_name"]
    if verbose:
        print(f"[enrich] {full_name} (id={project_id})")

    topics = json.loads(row["topics"] or "[]")

    if verbose:
        print(f"  fetching README...")
    readme = fetch_readme(full_name, github_token=github_token)
    if verbose:
        print(f"  README chars: {len(readme)}")

    if verbose:
        print(f"  calling LLM ({model})...")
    manifest = call_llm(
        full_name=full_name,
        description=row["description"] or "",
        topics=topics,
        language=row["language"] or "",
        readme=readme,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    if verbose:
        print(f"  → product_type={manifest['product_type']}, "
              f"quality={manifest['llm_quality_score']:.0f}, "
              f"solves={len(manifest['solves'])}")

    write_manifest(con, project_id, full_name, manifest, dry_run=dry_run)
    if not dry_run:
        con.commit()

    return manifest


# =============================================================================
# Batch helpers
# =============================================================================

def find_unenriched(con, limit: int | None = None) -> list[dict]:
    """Active projects with no enrichment yet, prioritized by stars desc."""
    sql = """
        SELECT p.id, p.full_name
        FROM projects p
        LEFT JOIN snapshots s ON s.project_id = p.id
            AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots WHERE project_id = p.id)
        WHERE p.is_active = 1
          AND (p.last_enriched_at = '' OR p.last_enriched_at IS NULL)
        ORDER BY COALESCE(s.stars, 0) DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in con.execute(sql).fetchall()]


def find_stale(con, older_than_days: int, limit: int | None = None) -> list[dict]:
    """Active projects whose last_enriched_at is older than N days."""
    cutoff = (datetime.now(CST) - timedelta(days=older_than_days)).isoformat()
    sql = """
        SELECT id, full_name
        FROM projects
        WHERE is_active = 1
          AND last_enriched_at != ''
          AND last_enriched_at < ?
        ORDER BY last_enriched_at ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in con.execute(sql, (cutoff,)).fetchall()]


def run_batch(project_ids: list[int], *, model: str = DEFAULT_MODEL,
              dry_run: bool = False, sleep_seconds: float = 1.0) -> dict:
    """Enrich a list of projects sequentially. Returns counts."""
    con = get_db()
    counts = {"ok": 0, "fail": 0, "by_type": {}}
    try:
        for i, pid in enumerate(project_ids, 1):
            print(f"\n[{i}/{len(project_ids)}]", end=" ")
            try:
                manifest = enrich_project(con, pid, model=model, dry_run=dry_run, base_url=DEFAULT_BASE_URL)
                counts["ok"] += 1
                pt = manifest["product_type"]
                counts["by_type"][pt] = counts["by_type"].get(pt, 0) + 1
            except Exception as e:
                counts["fail"] += 1
                print(f"  [ERROR] {e}", file=sys.stderr)
            time.sleep(sleep_seconds)
    finally:
        con.close()
    return counts


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Enrich project manifests via LLM")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", type=int, help="Project id to enrich")
    target.add_argument("--full-name", help="Project full_name (owner/repo)")
    target.add_argument("--limit", type=int,
                        help="Enrich N unenriched projects (highest-star first)")
    target.add_argument("--all", action="store_true",
                        help="Enrich every unenriched project")
    target.add_argument("--refresh-older",
                        help="Re-enrich projects whose enrichment is older than X days (e.g. 30)")

    parser.add_argument("--dry-run", action="store_true",
                        help="Print manifest, don't write to DB")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Seconds to sleep between LLM calls (default: 1.0)")
    args = parser.parse_args()

    con = get_db()

    if args.id:
        manifest = enrich_project(con, args.id, model=args.model, dry_run=args.dry_run, base_url=DEFAULT_BASE_URL)
        print("\n=== Manifest ===")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        con.close()
        return

    if args.full_name:
        row = con.execute("SELECT id FROM projects WHERE full_name = ?",
                          (args.full_name,)).fetchone()
        if not row:
            print(f"No project named {args.full_name}", file=sys.stderr)
            sys.exit(1)
        manifest = enrich_project(con, row["id"], model=args.model, dry_run=args.dry_run, base_url=DEFAULT_BASE_URL)
        print("\n=== Manifest ===")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        con.close()
        return

    # Batch modes
    if args.refresh_older:
        days = int(re.match(r"\d+", args.refresh_older).group())
        targets = find_stale(con, older_than_days=days)
        print(f"Found {len(targets)} stale projects (>{days} days)")
    elif args.all:
        targets = find_unenriched(con)
        print(f"Found {len(targets)} unenriched projects")
    else:
        targets = find_unenriched(con, limit=args.limit)
        print(f"Picking top {len(targets)} unenriched projects")

    con.close()

    if not targets:
        print("Nothing to do.")
        return

    ids = [t["id"] for t in targets]
    counts = run_batch(ids, model=args.model, dry_run=args.dry_run,
                       sleep_seconds=args.sleep)
    print(f"\n=== Batch complete ===")
    print(f"  ok:   {counts['ok']}")
    print(f"  fail: {counts['fail']}")
    print(f"  by product_type:")
    for pt, n in sorted(counts["by_type"].items(), key=lambda x: -x[1]):
        print(f"    {pt:25s}: {n}")


if __name__ == "__main__":
    main()
