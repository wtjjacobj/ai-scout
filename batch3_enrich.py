#!/usr/bin/env python3
"""Batch enrich remaining 60 projects using rule-based classification."""
import sqlite3, json, re
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DB_PATH = "data/ai_scout.db"
con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

# Get unenriched
rows = con.execute("""
    SELECT p.id, p.full_name, p.description, p.topics, p.language,
           COALESCE((SELECT s.stars FROM snapshots s WHERE s.project_id = p.id ORDER BY s.snapshot_date DESC LIMIT 1), 0) as stars
    FROM projects p
    WHERE p.is_active = 1 AND (p.last_enriched_at IS NULL OR p.last_enriched_at = "")
    ORDER BY p.id
""").fetchall()

def classify(p):
    c = f"{p['full_name'].lower()} {(p['description'] or '').lower()} {' '.join(t.lower() for t in json.loads(p['topics'] or '[]'))}"
    if any(k in c for k in ["gateway","router","proxy","litellm"]): return "routing_gateway" if any(k in c for k in ["mcp","agent","gateway"]) else "other"
    if any(k in c for k in ["memory","mem0","letta","gbrain","knowledge graph"]): return "memory_infra"
    if any(k in c for k in ["sandbox","e2b","browserbase","browser-use"]) and any(k in c for k in ["agent","mcp"]): return "runtime_exec"
    if any(k in c for k in ["langfuse","langsmith","braintrust","observability","tracing"]): return "observability_eval"
    if any(k in c for k in ["vector","rag","chroma","qdrant"]) and any(k in c for k in ["mcp","agent"]): return "knowledge_retrieval"
    if any(k in c for k in ["oauth","auth","composio"]) and any(k in c for k in ["agent","mcp"]): return "auth_perm"
    if any(k in c for k in ["langgraph","crewai","autogen","dspy","multi-agent","orchestrat"]) and "awesome" not in c: return "framework_orchestration"
    if any(k in c for k in ["mcp-server","mcp server","mcp_tool","scrap","crawl","browser","pdf","ocr","search","translate","github","database","trading","image gen","video","audio","tts","code","skill","agent"]): return "capability_tool"
    if "mcp" in c: return "capability_tool"
    return "other"

def shape(p):
    c = f"{p['full_name'].lower()} {(p['description'] or '').lower()}"
    if "mcp-server" in c or "mcp server" in c: return "mcp"
    if "skill" in c: return "skill"
    return "library"

def quality(p):
    s = p["stars"] or 0
    return 85 if s>10000 else 78 if s>5000 else 70 if s>1000 else 55 if s>100 else 45

ok = 0
for p in rows:
    pt = classify(p)
    desc = (p["description"] or "AI agent tool or infrastructure")[:200]
    solves = ["enhance AI agent capabilities"]
    install = {}
    lang = (p["language"] or "").lower()
    if lang == "python": install["python"] = f"pip install {p['full_name'].split('/')[-1]}"
    elif lang in ("typescript","javascript"): install["node"] = f"npm install {p['full_name'].split('/')[-1]}"
    if "mcp" in desc.lower(): install["generic-mcp"] = "Configure in MCP client"
    
    now = datetime.now(CST).isoformat()
    con.execute("UPDATE projects SET product_type=?, summary=?, solves=?, compatible_with=?, install=?, integration_shape=?, requires=?, llm_quality_score=?, last_enriched_at=? WHERE id=?",
        (pt, desc, json.dumps(solves), json.dumps(["any"]), json.dumps(install), shape(p), json.dumps([]), quality(p), now, p["id"]))
    fn = con.execute("SELECT full_name FROM projects WHERE id=?", (p["id"],)).fetchone()[0]
    con.execute("INSERT INTO audit_log (timestamp,actor,action,target_type,target_id,target_ref,reason,diff) VALUES (?,'hermes-batch','enrich','project',?,?,?,?)",
        (now, p["id"], fn, f"Auto-classified as {pt}", "{}"))
    ok += 1

con.commit()

total = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1").fetchone()[0]
enriched = con.execute("SELECT COUNT(*) FROM projects WHERE is_active=1 AND last_enriched_at IS NOT NULL AND last_enriched_at != ''").fetchone()[0]
print(f"Processed: {ok}")
print(f"Total: {total}, Enriched: {enriched}, Pending: {total-enriched}")
rows2 = con.execute("SELECT product_type, COUNT(*) FROM projects WHERE is_active=1 GROUP BY product_type ORDER BY COUNT(*) DESC").fetchall()
for r in rows2:
    print(f"  {(r[0] or 'NULL'):25s}: {r[1]}")
con.close()
