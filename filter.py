#!/usr/bin/env python3
"""
AI Scout 筛选器 - 从采集数据中筛选出值得集成的高价值发现
输出格式：适合直接推送给用户的简报
"""

import json
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"

# 我们已有的工具/框架（不需要重复推荐）
EXISTING_TOOLS = {
    "hermes-agent", "claude-code", "codex", "opencode", "cursor",
    "playwright", "gbrain", "akshare", "duckdb", "comfyui",
    "gpt-sovits", "whisper", "ollama", "lm-studio",
    "modelcontextprotocol/servers", "punkpeye/awesome-mcp-servers",
    "n8n-io/n8n", "google-gemini/gemini-cli",
}

# 集成价值评估关键词
HIGH_VALUE_KEYWORDS = {
    "mcp-server": "MCP服务器，可集成到Hermes",
    "mcp-client": "MCP客户端",
    "mcp": "MCP生态",
    "agent": "AI Agent框架",
    "multi-agent": "多Agent编排",
    "orchestrat": "编排框架",
    "rag": "RAG检索增强",
    "embedding": "向量嵌入",
    "tts": "文本转语音",
    "stt": "语音识别",
    "browser": "浏览器自动化",
    "scraping": "网络爬虫",
    "automation": "自动化",
    "workflow": "工作流引擎",
    "code-gen": "代码生成",
    "copilot": "AI编程助手",
    "skill": "技能框架",
    "tool-use": "工具使用",
    "fine-tun": "模型微调",
    "quantiz": "模型量化",
    "inference": "推理优化",
    "vision": "视觉模型",
    "ocr": "OCR文字识别",
    "video": "视频处理",
    "audio": "音频处理",
    "voice": "语音处理",
    "search": "搜索工具",
    "database": "数据库工具",
}


def score_relevance(item):
    """评估与Hermes生态的集成价值 (0-100)"""
    score = 0
    name = item.get("name", "").lower()
    desc = item.get("description", "") or ""
    topics = item.get("topics", [])
    text = f"{name} {desc} {' '.join(topics)}".lower()

    # 基础分：star数
    stars = item.get("stars", 0)
    if isinstance(stars, str):
        stars = int(stars.replace(",", "")) if stars.replace(",", "").isdigit() else 0
    if stars > 5000:
        score += 30
    elif stars > 1000:
        score += 20
    elif stars > 200:
        score += 10
    elif stars > 50:
        score += 5

    # 相关性加分
    for kw, label in HIGH_VALUE_KEYWORDS.items():
        if kw in text:
            score += 8

    # 已有工具降权
    for existing in EXISTING_TOOLS:
        if existing.lower() in name:
            score -= 50

    # 赌博/垃圾项目降权
    spam_keywords = ["stake", "casino", "bonus", "gambl", "crypto-airdrop", "free-token"]
    if any(kw in text for kw in spam_keywords):
        score = 0

    return min(max(score, 0), 100)


def main():
    if len(sys.argv) < 2:
        # 自动找最新文件
        files = sorted(OUTPUT_DIR.glob("scout_*.json"))
        if not files:
            print("No scout data found")
            sys.exit(1)
        input_file = files[-1]
    else:
        input_file = Path(sys.argv[1])

    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    all_items = []

    # 汇总所有源
    for source_name, items in data.get("data", {}).items():
        for item in items:
            item["_source"] = source_name
            item["_score"] = score_relevance(item)
            all_items.append(item)

    # 去重（按name/url）
    seen = set()
    unique = []
    for item in all_items:
        key = item.get("name", "") or item.get("url", "") or item.get("title", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    # 按分数排序
    unique.sort(key=lambda x: x["_score"], reverse=True)

    # 取top 20
    top = [item for item in unique if item["_score"] >= 15][:20]

    # 输出简报
    print(f"📊 AI Scout Daily - {data.get('date', 'Unknown')}")
    print(f"   采集: {data.get('total', 0)}条 → 筛选: {len(top)}条高价值")
    print("=" * 60)

    for i, item in enumerate(top, 1):
        name = item.get("name") or item.get("title", "Unknown")
        desc = item.get("description", "")[:80]
        stars = item.get("stars", "")
        url = item.get("url", "")
        source = item.get("_source", "")
        score = item.get("_score", 0)

        print(f"\n{i}. [{score}分] {name}")
        if stars:
            print(f"   ⭐ {stars} | 来源: {source}")
        if desc:
            print(f"   {desc}")
        if url:
            print(f"   {url}")

    # 保存筛选结果
    output_file = OUTPUT_DIR / f"filtered_{data.get('date', 'unknown')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"date": data.get("date"), "filtered": top[:20], "total_filtered": len(top)}, f, ensure_ascii=False, indent=2)

    print(f"\n📁 筛选结果已保存: {output_file}")


if __name__ == "__main__":
    main()
