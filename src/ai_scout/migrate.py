#!/usr/bin/env python3
"""
迁移脚本：把旧的JSON采集数据导入SQLite
用法：python -m ai_scout.migrate
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .db import init_db, get_db, upsert_project, add_snapshot, add_hn_ref, CST

OUTPUT_DIR = Path(__file__).parents[2] / "output"


def extract_github_full_name(url: str) -> str | None:
    """从URL中提取GitHub repo full_name"""
    if not url:
        return None
    match = re.match(r"https?://github\.com/([^/]+/[^/]+)", url)
    if match:
        return match.group(1).rstrip("/")
    return None


def migrate():
    init_db()
    con = get_db()

    try:
        # 扫描所有scout_*.json
        files = sorted(OUTPUT_DIR.glob("scout_*.json"))
        print(f"Found {len(files)} scout files to migrate")

        total_projects = 0
        total_snapshots = 0

        for fpath in files:
            print(f"\nProcessing {fpath.name}...")
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)

            date = data.get("date", "unknown")

            for source_name, items in data.get("data", {}).items():
                for item in items:
                    # 提取full_name
                    full_name = item.get("name", "")
                    if not full_name:
                        url = item.get("url", "")
                        full_name = extract_github_full_name(url) or url

                    if not full_name or "ycombinator" in full_name:
                        # HN帖子单独处理
                        if source_name == "hackernews":
                            hn_id = item.get("objectID", "") or item.get("hn_url", "").split("=")[-1]
                            if hn_id:
                                add_hn_ref(con, None, hn_id,
                                    title=item.get("title", ""),
                                    url=item.get("url", ""),
                                    hn_url=item.get("hn_url", f"https://news.ycombinator.com/item?id={hn_id}"),
                                    points=item.get("points", 0),
                                    num_comments=item.get("num_comments", 0),
                                    author=item.get("author", ""),
                                    posted_at=item.get("created_at", ""),
                                )
                        continue

                    url = item.get("url", f"https://github.com/{full_name}")

                    pid = upsert_project(con, full_name,
                        url=url,
                        description=item.get("description", ""),
                        language=item.get("language", ""),
                        topics=item.get("topics", []),
                        source=source_name,
                        created_at=item.get("created_at", ""),
                    )
                    total_projects += 1

                    # 添加快照
                    add_snapshot(con, pid, date,
                        stars=item.get("stars", 0),
                        forks=item.get("forks", 0),
                        raw_data=item,
                    )
                    total_snapshots += 1

            con.commit()
            print(f"  → {date}: committed")

        print(f"\n✅ Migration complete:")
        print(f"   Projects: {total_projects}")
        print(f"   Snapshots: {total_snapshots}")

    except Exception as e:
        con.rollback()
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    migrate()
