# AI Scout 🔍

> **AI项目发现引擎 — 发现 → 分类 → 评分 → API**
>
> 面向AI Agent的开源项目雷达。Agent不用自己去GitHub翻项目，查一眼就知道今天该关注什么。

[![MCP Server](https://img.shields.io/badge/MCP-Server-blue)](https://modelcontextprotocol.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## 🎯 解决什么问题

AI Agent需要知道最新的AI工具和框架，但GitHub trending太泛、awesome-list太静态、手动搜索太慢。

**AI Scout自动采集 → 智能分类 → 多维评分 → 暴露API**，Agent直接查就行。

## ✨ 特性

| 特性 | 说明 |
|---|---|
| 🔄 **多源采集** | GitHub Search + MCP Registry + Hacker News + OSSInsight，异步并发 |
| 🏷️ **AI自动分类** | framework / model / tool / data / infra，规则引擎 |
| 📊 **多维评分** | 动量(star增速+社交) × 0.5 + 质量(绝对值) × 0.3 + 分类权重 × 0.2 |
| 🛡️ **Spam过滤** | 自动过滤赌博/空投/垃圾项目，安全POC降权 |
| 🌐 **REST API** | 6个端点，FastAPI，外部直接HTTP调用 |
| 🔌 **MCP Server** | 5个工具，AI Agent直接连上来查 |
| ⏰ **Cron调度** | 每日自动采集+评分，飞书推送 |

## 🚀 快速开始

```bash
# 安装
git clone https://github.com/your-username/ai-scout.git
cd ai-scout
python3.12 -m venv .venv
.venv/bin/pip install -e .

# 采集（异步并发）
.venv/bin/python -m ai_scout.collector_async

# 评分
.venv/bin/python -m ai_scout.scorer

# 启动REST API
.venv/bin/uvicorn ai_scout.api:app --host 0.0.0.0 --port 8900

# 启动MCP Server (stdio)
.venv/bin/ai-scout

# MCP Server (HTTP)
.venv/bin/ai-scout --streamable-http --port 8901
```

## 🔌 MCP Server 工具

| 工具 | 说明 | 示例 |
|---|---|---|
| `daily_report` | 今日精选 | "今天有什么AI项目值得关注？" |
| `search_projects` | 搜索项目 | "找MCP server相关项目" |
| `get_trending` | 趋势榜 | "最近7天最火的AI项目" |
| `project_detail` | 项目详情 | "tell me about n8n-io/n8n" |
| `scout_stats` | 数据统计 | "你追踪了多少项目？" |

### Claude Desktop / Hermes 配置

```json
{
  "mcpServers": {
    "ai-scout": {
      "command": "/path/to/ai-scout/.venv/bin/python",
      "args": ["-m", "ai_scout.server"]
    }
  }
}
```

## 🌐 REST API

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 服务状态+统计 |
| `GET /api/daily?limit=10` | 每日精选 |
| `GET /api/trending?days=7` | 趋势项目（star增速） |
| `GET /api/projects?category=tool&min_score=20` | 项目列表 |
| `GET /api/projects/{owner/repo}` | 项目详情（含历史、评分、HN讨论） |
| `GET /api/categories` | 分类统计 |

## 📊 评分模型

```
Composite = Momentum × 0.5 + Quality × 0.3 + Category × 0.2

Momentum (0-100): star 24h/7d增速 + HN/Reddit社交热度
Quality  (0-100): star绝对值 + forks绝对值
Category (0-30):  AI分类加分 + MCP/Agent等热门主题加分
```

### 分类体系

| 分类 | 说明 | 示例 |
|---|---|---|
| `framework` | Agent/RAG/工作流框架 | LangChain, CrewAI |
| `model` | LLM/模型/检查点 | LLaMA, Stable Diffusion |
| `tool` | CLI/MCP Server/工具 | FastMCP, ComfyUI |
| `data` | 数据集/向量库/基准 | MMLU, Chroma |
| `infra` | 推理/训练/量化/部署 | vLLM, llama.cpp |

## 🏗️ 架构

```
src/ai_scout/
├── db.py              # SQLite数据层（4表：projects/snapshots/scores/hn_refs）
├── collector.py       # 同步采集器（兼容旧版）
├── collector_async.py # 异步并发采集器（3-5x更快）
├── scorer.py          # 多维评分引擎 + spam过滤
├── api.py             # REST API（FastAPI）
├── server.py          # MCP Server（FastMCP 3.x）
├── migrate.py         # JSON→SQLite迁移
data/
└── ai_scout.db        # SQLite数据库
```

## 🔧 环境变量

| 变量 | 说明 | 必需 |
|---|---|---|
| `AI_SCOUT_DB` | SQLite数据库路径 | 否（默认 data/ai_scout.db） |
| `GITHUB_TOKEN` | GitHub API token（提高rate limit） | 否 |

## 📜 License

MIT
