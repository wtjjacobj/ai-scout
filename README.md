# AI Scout — AI Agent Capability Discovery Engine

> The discovery layer AI agents didn't know they needed.

AI Scout is an MCP server that helps AI agents discover, evaluate, and install the right tools, skills, and infrastructure for their tasks. Think of it as a knowledgeable curator that any agent can query.

## Why?

AI agents today are blind to the ecosystem of tools available to them. When Claude Code needs to scrape a website, when Cursor needs to add memory, when any agent needs to find the right MCP server — there's no single place to look. AI Scout fills that gap.

## MCP Tools

### `daily_brief()`
Get today's curated picks — 3-5 noteworthy capabilities covering different product types. Call once per day.

```json
{
  "date": "2026-05-06",
  "total_enriched": 1987,
  "items": [
    {
      "full_name": "upstash/context7",
      "product_type": "knowledge_retrieval",
      "summary": "MCP server that injects up-to-date library documentation...",
      "why_now": "trending: +54312 stars this week",
      "install": {"node": "npx ctx7 setup"},
      "llm_quality_score": 92
    }
  ]
}
```

### `recommend(query)`
Find capabilities matching a natural language task description. Returns ranked candidates with install commands and trade-offs.

```
recommend(query="add long-term memory to my agent")
recommend(query="web scraping with JavaScript rendering")
recommend(query="route between multiple LLM providers")
```

### `project_detail(full_name)`
Get the full manifest for a specific project by `owner/repo`.

```
project_detail(full_name="firecrawl/firecrawl-mcp-server")
```

## Product Types

| Type | Description |
|------|-------------|
| `capability_tool` | MCP servers, plugins, callable tools |
| `memory_infra` | Persistent memory (Mem0, Letta, GBrain) |
| `runtime_exec` | Sandboxes, execution environments (E2B, Browserbase) |
| `framework_orchestration` | Agent frameworks (LangGraph, CrewAI, DSPy) |
| `observability_eval` | Monitoring, tracing, eval (Langfuse, LangSmith) |
| `routing_gateway` | Model routers (LiteLLM, OpenRouter) |
| `knowledge_retrieval` | Vector DBs, RAG (Chroma, Qdrant) |
| `auth_perm` | Auth/permissions (Composio, Arcade) |

## Stats

- **1987** indexed projects
- **9** product type categories
- **5** discovery sources (GitHub, Smithery, npm, awesome-lists, watchlist)
- **TF-IDF** semantic search with 4532 features

## Installation

```bash
# Clone
git clone https://github.com/your-org/ai-scout.git
cd ai-scout

# Install
pip install -e .

# Run as MCP server (stdio)
ai-scout

# Run as HTTP server
ai-scout --streamable-http --port 8900
```

### Configure in Claude Code

Add to your MCP settings:
```json
{
  "ai-scout": {
    "command": "ai-scout",
    "args": []
  }
}
```

### Configure in Cursor

Add to `.cursor/mcp.json`:
```json
{
  "ai-scout": {
    "command": "ai-scout"
  }
}
```

## Architecture

```
┌─────────────────────────────────┐
│   External MCP Server (server.py) │
│   daily_brief / recommend /       │
│   project_detail                  │
└──────────┬──────────────────────┘
           │ reads
┌──────────▼──────────────────────┐
│   SQLite DB (ai_scout.db)        │
│   1987 projects + TF-IDF index   │
└──────────▲──────────────────────┘
           │ writes
┌──────────┴──────────────────────┐
│   Hermes Maintenance Cron        │
│   discover → triage → enrich →   │
│   index → report                 │
└─────────────────────────────────┘
```

**Two layers:**
- **Outer**: Public MCP server, read-only, serves agents
- **Inner**: Hermes agent continuously discovers, classifies, and indexes new projects

## Discovery Sources

The Hermes agent crawls these sources every 6 hours:
1. **GitHub Search** — new AI/MCP/agent repos (7-14 day window)
2. **Smithery** — MCP server registry
3. **npm** — packages tagged mcp-server, agent-tool, claude-skill
4. **awesome-mcp-servers** — 2000+ MCP server list
5. **Watchlist** — 50+ core infra project releases

## Development

```bash
# Run discovery
python -m ai_scout.hermes.discover

# Build TF-IDF index
python -m ai_scout.hermes.embed index

# Check stats
python -m ai_scout.hermes.maintain stats
```

## License

MIT
