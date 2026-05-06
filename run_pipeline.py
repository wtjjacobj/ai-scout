#!/usr/bin/env python3
"""
AI Scout Cloud Pipeline — GitHub Actions entry point
Runs: collect → score → enrich → embed → commit

Usage:
  python run_pipeline.py [--skip-collect] [--skip-enrich] [--enrich-limit N]
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ai_scout.db"
INDEX_PATH = DATA_DIR / "tfidf_index.pkl"

CST = timezone(timedelta(hours=8))


def run(cmd, **kw):
    """Run a subprocess, streaming output."""
    print(f"\n{'='*60}")
    print(f"▶ {cmd}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, shell=True, cwd=ROOT, **kw)
    if result.returncode != 0:
        print(f"❌ Failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result


def step_collect():
    """Step 1: Collect from GitHub + HN → SQLite"""
    run(f"{sys.executable} -m ai_scout.collector")


def step_score():
    """Step 2: Multi-dimension scoring"""
    run(f"{sys.executable} -m ai_scout.scorer")


def step_enrich(limit=None):
    """Step 3: LLM enrichment — classify + manifest generation"""
    cmd = f"{sys.executable} -m ai_scout.hermes.enrich"
    if limit:
        cmd += f" --limit {limit}"
    else:
        cmd += " --all"
    run(cmd)


def step_embed():
    """Step 4: Build TF-IDF index for semantic search"""
    run(f"{sys.executable} -m ai_scout.hermes.embed index")


def step_stats():
    """Print pipeline summary"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1").fetchone()[0]
        enriched = con.execute(
            "SELECT COUNT(*) FROM projects WHERE is_active=1 AND summary IS NOT NULL AND summary != ''"
        ).fetchone()[0]
        latest_snap = con.execute("SELECT MAX(snapshot_date) FROM snapshots").fetchone()[0] or "N/A"
        latest_score = con.execute("SELECT MAX(score_date) FROM scores").fetchone()[0] or "N/A"

        print(f"\n{'='*60}")
        print(f"Pipeline Summary — {datetime.now(CST).strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*60}")
        print(f"  Total projects:  {total}")
        print(f"  Enriched:        {enriched} ({enriched*100//max(total,1)}%)")
        print(f"  Latest snapshot: {latest_snap}")
        print(f"  Latest scores:   {latest_score}")
        print(f"  DB size:         {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"  Index size:      {INDEX_PATH.stat().st_size / 1024:.0f} KB" if INDEX_PATH.exists() else "  Index: not built")
        print(f"{'='*60}")
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description="AI Scout Cloud Pipeline")
    parser.add_argument("--skip-collect", action="store_true", help="Skip data collection")
    parser.add_argument("--skip-score", action="store_true", help="Skip scoring")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip LLM enrichment")
    parser.add_argument("--enrich-limit", type=int, default=None, help="Limit enrichment batch size")
    args = parser.parse_args()

    print(f"🚀 AI Scout Pipeline — {datetime.now(CST).strftime('%Y-%m-%d %H:%M CST')}")

    # Validate env
    if not args.skip_enrich:
        api_key = os.environ.get("AI_SCOUT_LLM_API_KEY", "")
        if not api_key:
            print("❌ AI_SCOUT_LLM_API_KEY not set")
            sys.exit(1)

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Run pipeline
    if not args.skip_collect:
        step_collect()

    if not args.skip_score:
        step_score()

    if not args.skip_enrich:
        step_enrich(limit=args.enrich_limit)

    step_embed()
    step_stats()

    print("\n✅ Pipeline complete!")


if __name__ == "__main__":
    main()
