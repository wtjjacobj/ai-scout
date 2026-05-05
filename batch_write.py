
import sqlite3, json, os, glob
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB_PATH = os.path.expanduser("~/Projects/ai-scout/data/ai_scout.db")
con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

manifest_files = sorted(glob.glob("/tmp/manifest_*.json"))
ok = 0
fail = 0
for mf in manifest_files:
    try:
        pid = int(os.path.basename(mf).replace("manifest_","").replace(".json",""))
        with open(mf) as f:
            manifest = json.load(f)
        
        solves_json = json.dumps(manifest.get("solves", []), ensure_ascii=False)
        compat_json = json.dumps(manifest.get("compatible_with", ["any"]), ensure_ascii=False)
        install_json = json.dumps(manifest.get("install", {}), ensure_ascii=False)
        requires_json = json.dumps(manifest.get("requires", []), ensure_ascii=False)
        
        now = datetime.now(CST).isoformat()
        
        con.execute("""UPDATE projects SET
              product_type = ?, summary = ?, solves = ?,
              compatible_with = ?, install = ?, integration_shape = ?,
              requires = ?, llm_quality_score = ?, last_enriched_at = ?
           WHERE id = ?""",
            (manifest.get("product_type", "other"), manifest.get("summary", ""),
             solves_json, compat_json, install_json,
             manifest.get("integration_shape", "library"), requires_json,
             manifest.get("llm_quality_score", 50), now, pid))
        
        full_name = con.execute("SELECT full_name FROM projects WHERE id=?", (pid,)).fetchone()
        fn = full_name[0] if full_name else str(pid)
        con.execute("""INSERT INTO audit_log
              (timestamp, actor, action, target_type, target_id, target_ref, reason, diff)
           VALUES (?, 'hermes-batch', 'enrich', 'project', ?, ?, ?, ?)""",
            (now, pid, fn, manifest.get("rationale", "batch enrich"), "{}"))
        ok += 1
    except Exception as e:
        fail += 1
        print(f"FAIL {mf}: {e}")

con.commit()
print(f"Done: {ok} OK, {fail} FAIL")

total = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1").fetchone()[0]
enriched = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1 AND last_enriched_at IS NOT NULL AND last_enriched_at != ''").fetchone()[0]
rows = con.execute("SELECT product_type, COUNT(*) FROM projects WHERE is_active=1 GROUP BY product_type ORDER BY COUNT(*) DESC").fetchall()
print(f"\nTotal: {total}, Enriched: {enriched}, Pending: {total-enriched}")
print("Distribution:")
for r in rows:
    print(f"  {(r[0] or 'NULL'):25s}: {r[1]}")
con.close()
