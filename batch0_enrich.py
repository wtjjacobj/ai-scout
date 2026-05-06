#!/usr/bin/env python3
"""
AI Scout Batch Manifest Generator
Reads /tmp/ai_scout_enrich_batch_0.json, generates manifests, writes to /tmp/ai_scout_manifests_batch_0.json
"""
import json, sys, os

def main():
    input_path = "/tmp/ai_scout_enrich_batch_0.json"
    output_path = "/tmp/ai_scout_manifests_batch_0.json"
    
    with open(input_path, 'r') as f:
        projects = json.load(f)
    
    print(f"Loaded {len(projects)} projects")
    
    manifests = []
    for p in projects:
        m = classify(p)
        manifests.append(m)
    
    # Stats
    type_counts = {}
    score_sum = 0
    for m in manifests:
        pt = m["product_type"]
        type_counts[pt] = type_counts.get(pt, 0) + 1
        score_sum += m["llm_quality_score"]
    
    avg = score_sum / len(manifests) if manifests else 0
    print(f"Generated {len(manifests)} manifests, avg score: {avg:.1f}")
    for pt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {pt}: {cnt}")
    
    with open(output_path, 'w') as f:
        json.dump(manifests, f, indent=2, ensure_ascii=False)
    print(f"Written to {output_path}")

def classify(p):
    pid = p.get("id", 0)
    fn = p.get("full_name", "")
    desc = p.get("description", "") or ""
    topics = p.get("topics", []) or []
    lang = p.get("language", "") or ""
    stars = p.get("stars", 0) or 0
    
    fn_l = fn.lower()
    desc_l = desc.lower()
    topics_l = [t.lower() for t in topics]
    ctx = f"{fn_l} {desc_l} {' '.join(topics_l)}"
    
    # Invalid entries
    if fn_l.startswith("http://") or fn_l.startswith("https://"):
        return manifest(pid, fn, "other", "Invalid entry - URL", ["non-repo"], [], {}, "service", [], 5)
    
    # Spam
    for kw in ["stake", "casino", "bonus", "gambling", "slot", "betting", "porn", "onlyfans"]:
        if kw in fn_l or (kw in desc_l and len(desc) < 300 and stars < 50):
            return manifest(pid, fn, "other", desc[:200] or f"Non-AI: {fn}", ["non-ai"], [], {}, "service", [], 10)
    
    is_mcp = "mcp" in fn_l or "mcp" in topics_l
    
    if is_mcp:
        return classify_mcp(p, fn_l, desc_l, topics_l, ctx, lang, stars)
    
    pt, score, solves, shape = "other", 30, ["general-purpose"], "library"
    
    if any(k in ctx for k in ["framework","orchestrat","workflow","pipeline","langchain","crewai","autogen","multi-agent","swarm","langgraph"]):
        pt, score, solves = "framework_orchestration", 55 + min(stars//100, 25), ["agent-orchestration","workflow-automation"]
        if "multi-agent" in ctx: solves.append("multi-agent-coordination")
    elif any(k in ctx for k in ["runtime","sandbox","executor","execution","container","vm","simulator","emulator","browser-automat"]):
        pt, score, solves, shape = "runtime_exec", 50 + min(stars//100, 30), ["code-execution"], "service" if "simulator" not in ctx else "cli"
        if "sandbox" in ctx: solves.append("secure-execution")
        if "browser" in ctx: solves.append("browser-automation")
    elif any(k in ctx for k in ["memory","mem0","long-term","vector-store","embed","retrieval-augmented","rag","knowledge-graph","graphiti"]):
        pt, score, solves = "memory_infra", 55 + min(stars//100, 25), ["persistent-memory"]
        if "vector" in ctx: solves.append("vector-search")
        if "graph" in ctx: solves.append("knowledge-graph")
    elif any(k in ctx for k in ["search","retrieval","crawl","scraper","index","knowledge-base","document","pdf","reader","parser"]):
        pt, score, solves = "knowledge_retrieval", 50 + min(stars//100, 25), ["information-retrieval"]
        if "pdf" in ctx: solves.append("document-parsing")
    elif any(k in ctx for k in ["auth","oauth","permission","rbac","access-control","identity","token","credential"]):
        pt, score, solves = "auth_perm", 45 + min(stars//100, 25), ["authentication","authorization","security"]
    elif any(k in ctx for k in ["observ","monitor","eval","benchmark","tracing","logging","metrics","profiler","debug"]):
        pt, score, solves = "observability_eval", 50 + min(stars//100, 25), ["agent-monitoring"]
        if "eval" in ctx: solves.append("quality-evaluation")
    elif any(k in ctx for k in ["gateway","router","proxy","load-balanc","api-management","relay","bridge"]):
        pt, score, solves, shape = "routing_gateway", 45 + min(stars//100, 25), ["api-routing","load-balancing"], "service"
    elif any(k in ctx for k in ["tool","plugin","extension","integration","sdk","api-client","wrapper","browser","gui","dashboard","cli"]):
        pt, score, solves = "capability_tool", 40 + min(stars//100, 30), ["tool-integration"]
        if "cli" in ctx: shape = "cli"
        if "dashboard" in ctx: shape = "service"
    elif any(k in ctx for k in ["prompt","template","chain","skill","recipe"]):
        pt, score, solves = "skill", 35 + min(stars//100, 25), ["prompt-engineering","task-automation"]
    elif any(k in ctx for k in ["llm","gpt","claude","model","ai","ml","neural","transformer","inference","fine-tun","chatbot","agent"]):
        pt, score, solves = "capability_tool", 40 + min(stars//100, 30), ["ai-capability"]
        if "agent" in ctx: solves.append("agent-capability")
    else:
        pt, score, solves = "other", 15 + min(stars//200, 15), ["general-purpose"]
    
    score = max(5, min(score, 100))
    compat = detect_compat(lang, ctx)
    install = detect_install(fn_l, lang, ctx)
    summary = desc[:300] if desc else f"Project: {fn}"
    
    return manifest(pid, fn, pt, summary, solves[:5], compat, install, shape, [], score)

def classify_mcp(p, fn_l, desc_l, topics_l, ctx, lang, stars):
    fn = p.get("full_name", "")
    desc = p.get("description", "") or ""
    mcp_name = fn_l.split("/")[-1].replace("-mcp","").replace("_mcp","").replace("mcp-","").replace("mcp_","")
    
    summary = desc[:300] if desc else f"MCP server providing {mcp_name} integration for AI agents"
    solves = ["mcp-tool","ai-agent-integration"]
    score = 30 + min(stars, 30)
    shape = "service"
    requires = ["Node.js or Python runtime"]
    compat = detect_compat(lang, ctx)
    install = detect_install(fn_l, lang, ctx)
    
    # Specific
    if "wireshark" in fn_l:
        summary, solves = "MCP server for Wireshark network packet analysis", ["network-analysis","packet-inspection","mcp-tool"]
    elif "stockfish" in fn_l:
        summary, solves = "MCP server integrating Stockfish chess engine", ["chess-engine","game-ai","mcp-tool"]
    elif "hn" in fn_l or "hacker" in fn_l:
        summary, solves = "MCP server for Hacker News data access", ["hacker-news","content-aggregation","mcp-tool"]
    elif "iplocate" in fn_l:
        summary, solves = "MCP server for IP geolocation via iplocate.io", ["ip-geolocation","network-info","mcp-tool"]
    elif "solmail" in fn_l:
        summary, solves = "MCP server for Solana blockchain email", ["solana","blockchain","email","mcp-tool"]
    elif "didlogic" in fn_l:
        summary, solves = "MCP server for DidLogic VoIP/telephony", ["voip","telephony","mcp-tool"]
    elif "web-eval" in fn_l:
        summary, solves, shape = "Web page evaluation and quality assessment agent", ["web-evaluation","quality-assessment","browser-automation"], "cli"
    else:
        nl = mcp_name.lower()
        if any(w in nl for w in ["sql","db","database","postgres","mysql","mongo","redis"]):
            solves = ["mcp-tool","database-access","data-query","ai-agent-integration"]
        elif any(w in nl for w in ["web","browser","scrape","crawl"]):
            solves = ["mcp-tool","web-interaction","browser-automation","ai-agent-integration"]
        elif any(w in nl for w in ["file","fs","storage"]):
            solves = ["mcp-tool","file-management","storage-access","ai-agent-integration"]
        elif any(w in nl for w in ["git","github","code"]):
            solves = ["mcp-tool","code-management","version-control","ai-agent-integration"]
        elif any(w in nl for w in ["slack","discord","chat","email","message"]):
            solves = ["mcp-tool","communication","messaging","ai-agent-integration"]
        elif any(w in nl for w in ["search","find","query"]):
            solves = ["mcp-tool","search","information-retrieval","ai-agent-integration"]
        elif any(w in nl for w in ["image","vision","video"]):
            solves = ["mcp-tool","multimedia","visual-processing","ai-agent-integration"]
        elif any(w in nl for w in ["map","geo","location","weather"]):
            solves = ["mcp-tool","geospatial","location-data","ai-agent-integration"]
        elif any(w in nl for w in ["calendar","time","schedule","task"]):
            solves = ["mcp-tool","scheduling","task-management","ai-agent-integration"]
        elif any(w in nl for w in ["math","calc","compute","data"]):
            solves = ["mcp-tool","computation","data-processing","ai-agent-integration"]
        else:
            solves = ["mcp-tool","tool-integration","ai-agent-integration"]
    
    score = max(5, min(score, 100))
    return manifest(p.get("id",0), fn, "capability_tool", summary, solves, compat, install, shape, requires, score)

def detect_compat(lang, ctx):
    compat = set()
    l = (lang or "").lower()
    if l in ("python","python3"): compat.add("python")
    elif l in ("javascript","typescript","jsx","tsx"): compat.add("node")
    elif l in ("rust","go","c","cpp","java"): compat.add("native")
    elif l in ("swift","objective-c"): compat.add("swift")
    if "python" in ctx: compat.add("python")
    if any(w in ctx for w in ["node","npm","javascript","typescript"]): compat.add("node")
    if "docker" in ctx: compat.add("docker")
    if not compat: compat.add("any")
    return sorted(compat)

def detect_install(fn_l, lang, ctx):
    l = (lang or "").lower()
    pkg = fn_l.split("/")[-1]
    if "docker" in ctx: return {"docker": f"docker pull {pkg}"}
    if l in ("python","python3"): return {"pip": f"pip install {pkg}"}
    if l in ("javascript","typescript","jsx","tsx"): return {"npm": f"npm install {pkg}"}
    if l == "rust": return {"cargo": f"cargo install {pkg}"}
    if l == "go": return {"go": f"go install github.com/{fn_l}"}
    if l == "swift": return {"swift": "swift package init"}
    return {"github": f"git clone https://github.com/{fn_l}.git"}

def manifest(pid, fn, pt, summary, solves, compat, install, shape, requires, score):
    return {
        "project_id": pid,
        "full_name": fn,
        "product_type": pt,
        "summary": summary,
        "solves": solves[:5],
        "compatible_with": compat,
        "install": install,
        "integration_shape": shape,
        "requires": requires,
        "llm_quality_score": score
    }

if __name__ == "__main__":
    main()
