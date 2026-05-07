#!/usr/bin/env python3
"""Parallel enrich — ThreadPoolExecutor with N workers, each with own DB connection."""
import os, sys, time, sqlite3, argparse, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_scout.hermes.enrich import enrich_project
from ai_scout.db import DB_PATH

API_KEY = os.environ.get("AI_SCOUT_LLM_API_KEY", "")
MODEL = os.environ.get("AI_SCOUT_LLM_MODEL", "glm-5.1")
BASE_URL = os.environ.get("AI_SCOUT_LLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")

# Get GitHub token from gh CLI
def _get_github_token():
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except:
        return None

GH_TOKEN = _get_github_token()


def get_unenriched_ids():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    rows = con.execute("""
        SELECT id FROM projects 
        WHERE is_active = 1 AND llm_quality_score IS NULL
        ORDER BY id
    """).fetchall()
    con.close()
    return [r[0] for r in rows]


def worker(project_id: int) -> dict:
    con = sqlite3.connect(str(DB_PATH), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.row_factory = sqlite3.Row
    try:
        manifest = enrich_project(
            con, project_id, model=MODEL, api_key=API_KEY, 
            base_url=BASE_URL, github_token=GH_TOKEN, verbose=False
        )
        return {"id": project_id, "status": "ok", 
                "pt": manifest.get("product_type", ""), 
                "q": manifest.get("llm_quality_score", 0)}
    except Exception as e:
        return {"id": project_id, "status": "error", "error": str(e)[:80]}
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not API_KEY:
        print("[ERROR] AI_SCOUT_LLM_API_KEY not set"); sys.exit(1)

    ids = get_unenriched_ids()
    if args.limit > 0:
        ids = ids[:args.limit]
    if not ids:
        print("Nothing to enrich."); return

    print(f"Enriching {len(ids)} projects | {args.workers} workers | model={MODEL} | gh_token={'yes' if GH_TOKEN else 'no'}")
    
    ok = fail = 0
    t0 = time.time()
    
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, pid): pid for pid in ids}
        for i, future in enumerate(as_completed(futures), 1):
            r = future.result()
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(ids) - i) / rate / 60 if rate > 0 else 0
            
            if r["status"] == "ok":
                ok += 1
                q_val = float(r['q']) if r['q'] is not None else 0
                print(f"  [{i}/{len(ids)}] id={r['id']} q={q_val:.0f} {r['pt'][:20]} | {rate:.1f}/s ETA:{eta:.0f}m")
            else:
                fail += 1
                print(f"  [{i}/{len(ids)}] id={r['id']} ERR {r['error'][:50]} | {rate:.1f}/s")
    
    elapsed = time.time() - t0
    print(f"\n=== Done {elapsed/60:.1f}min | ok:{ok} fail:{fail} rate:{len(ids)/elapsed:.1f}/s ===")


if __name__ == "__main__":
    main()
