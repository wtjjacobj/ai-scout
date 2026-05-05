"""
AI Scout 数据库层 — SQLite + 历史追踪 + 多维评分

表结构：
  projects: 核心项目表（去重后的唯一项目）
  snapshots: 每日快照（stars/forks等时序数据）
  scores: 综合评分结果
  hn_refs: HN帖子与项目的关联
"""

import sqlite3
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "AI_SCOUT_DB",
    str(Path(__file__).parents[2] / "data" / "ai_scout.db")
))

CST = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT UNIQUE NOT NULL,          -- owner/repo
    url TEXT NOT NULL,
    description TEXT DEFAULT '',
    language TEXT DEFAULT '',
    topics TEXT DEFAULT '[]',                -- JSON array
    category TEXT DEFAULT '',                -- AI分类: framework/model/tool/data/infra/other
    subcategory TEXT DEFAULT '',             -- 子分类
    source TEXT DEFAULT '',                  -- 首次发现来源
    first_seen TEXT NOT NULL,                -- ISO时间戳
    last_seen TEXT NOT NULL,                 -- 最近一次采集到
    is_active INTEGER DEFAULT 1,             -- 1=活跃 0=归档
    created_at TEXT DEFAULT ''               -- repo创建时间
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,             -- YYYY-MM-DD
    stars INTEGER DEFAULT 0,
    forks INTEGER DEFAULT 0,
    open_issues INTEGER DEFAULT 0,
    watchers INTEGER DEFAULT 0,
    contributors INTEGER DEFAULT 0,
    -- 增速指标（采集时计算）
    stars_24h INTEGER DEFAULT 0,            -- 24h star增量
    stars_7d INTEGER DEFAULT 0,             -- 7d star增量
    -- 社交热度
    hn_points INTEGER DEFAULT 0,
    hn_comments INTEGER DEFAULT 0,
    reddit_score INTEGER DEFAULT 0,
    -- 包下载量
    npm_downloads_weekly INTEGER DEFAULT 0,
    pypi_downloads_weekly INTEGER DEFAULT 0,
    -- 元数据
    raw_data TEXT DEFAULT '{}',             -- 原始采集数据JSON
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    score_date TEXT NOT NULL,
    -- 各维度得分 (0-100)
    momentum_score REAL DEFAULT 0,          -- 动量分(star增速+社交)
    quality_score REAL DEFAULT 0,           -- 质量分(star绝对值+contributors)
    category_score REAL DEFAULT 0,          -- 分类权重(AI项目加分)
    composite_score REAL DEFAULT 0,         -- 综合评分
    -- 排名
    rank_total INTEGER DEFAULT 0,
    rank_category INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, score_date)
);

CREATE TABLE IF NOT EXISTS hn_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    hn_id TEXT,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    hn_url TEXT DEFAULT '',
    points INTEGER DEFAULT 0,
    num_comments INTEGER DEFAULT 0,
    author TEXT DEFAULT '',
    posted_at TEXT DEFAULT '',
    discovered_at TEXT DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(hn_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
CREATE INDEX IF NOT EXISTS idx_projects_active ON projects(is_active);
CREATE INDEX IF NOT EXISTS idx_snapshots_project_date ON snapshots(project_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_scores_composite ON scores(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_date ON scores(score_date);
"""


def get_db() -> sqlite3.Connection:
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    """初始化数据库schema"""
    con = get_db()
    try:
        con.executescript(SCHEMA)
        con.commit()
        print(f"[DB] Schema initialized: {DB_PATH}")
    finally:
        con.close()


def upsert_project(con, full_name: str, url: str, **kwargs) -> int:
    """插入或更新项目，返回project_id"""
    now = datetime.now(CST).isoformat()
    existing = con.execute(
        "SELECT id FROM projects WHERE full_name = ?", (full_name,)
    ).fetchone()

    if existing:
        project_id = existing["id"]
        updates = {"last_seen": now}
        for key in ("description", "language"):
            if key in kwargs and kwargs[key]:
                updates[key] = kwargs[key]
        if "topics" in kwargs and kwargs["topics"]:
            t = kwargs["topics"]
            updates["topics"] = json.dumps(t) if isinstance(t, list) else t
        if "category" in kwargs and kwargs["category"]:
            updates["category"] = kwargs["category"]
        if "subcategory" in kwargs and kwargs["subcategory"]:
            updates["subcategory"] = kwargs["subcategory"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        con.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?",
            list(updates.values()) + [project_id]
        )
        return project_id
    else:
        topics_val = kwargs.get("topics", [])
        if isinstance(topics_val, list):
            topics_val = json.dumps(topics_val)
        con.execute(
            """INSERT INTO projects (full_name, url, description, language, topics,
               category, subcategory, source, first_seen, last_seen, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                full_name, url,
                kwargs.get("description", ""),
                kwargs.get("language", ""),
                topics_val,
                kwargs.get("category", ""),
                kwargs.get("subcategory", ""),
                kwargs.get("source", ""),
                now, now,
                kwargs.get("created_at", ""),
            )
        )
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def add_snapshot(con, project_id: int, date: str, **kwargs) -> None:
    """添加每日快照（忽略重复）"""
    raw_data = kwargs.pop("raw_data", {})
    if isinstance(raw_data, dict):
        raw_data = json.dumps(raw_data, ensure_ascii=False)

    con.execute(
        """INSERT OR IGNORE INTO snapshots
           (project_id, snapshot_date, stars, forks, open_issues, watchers,
            contributors, stars_24h, stars_7d,
            hn_points, hn_comments, reddit_score,
            npm_downloads_weekly, pypi_downloads_weekly, raw_data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id, date,
            kwargs.get("stars", 0),
            kwargs.get("forks", 0),
            kwargs.get("open_issues", 0),
            kwargs.get("watchers", 0),
            kwargs.get("contributors", 0),
            kwargs.get("stars_24h", 0),
            kwargs.get("stars_7d", 0),
            kwargs.get("hn_points", 0),
            kwargs.get("hn_comments", 0),
            kwargs.get("reddit_score", 0),
            kwargs.get("npm_downloads_weekly", 0),
            kwargs.get("pypi_downloads_weekly", 0),
            raw_data,
        )
    )


def add_hn_ref(con, project_id: int | None, hn_id: str, **kwargs) -> None:
    """添加HN引用"""
    now = datetime.now(CST).isoformat()
    con.execute(
        """INSERT OR IGNORE INTO hn_refs
           (project_id, hn_id, title, url, hn_url, points, num_comments,
            author, posted_at, discovered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id, hn_id,
            kwargs.get("title", ""),
            kwargs.get("url", ""),
            kwargs.get("hn_url", ""),
            kwargs.get("points", 0),
            kwargs.get("num_comments", 0),
            kwargs.get("author", ""),
            kwargs.get("posted_at", ""),
            now,
        )
    )


def get_star_velocity(con, project_id: int) -> tuple[int, int]:
    """获取star增速：返回 (24h增量, 7d增量)"""
    today = datetime.now(CST).strftime("%Y-%m-%d")
    yesterday = (datetime.now(CST) - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (datetime.now(CST) - timedelta(days=7)).strftime("%Y-%m-%d")

    current = con.execute(
        "SELECT stars FROM snapshots WHERE project_id = ? ORDER BY snapshot_date DESC LIMIT 1",
        (project_id,)
    ).fetchone()
    if not current:
        return 0, 0

    current_stars = current["stars"]

    # 24h
    prev_day = con.execute(
        "SELECT stars FROM snapshots WHERE project_id = ? AND snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1",
        (project_id, yesterday)
    ).fetchone()
    stars_24h = current_stars - (prev_day["stars"] if prev_day else 0)

    # 7d
    prev_week = con.execute(
        "SELECT stars FROM snapshots WHERE project_id = ? AND snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1",
        (project_id, week_ago)
    ).fetchone()
    stars_7d = current_stars - (prev_week["stars"] if prev_week else 0)

    return max(stars_24h, 0), max(stars_7d, 0)


# 查询辅助函数（供API/MCP使用）

def query_projects(con, category: str = "", min_score: float = 0,
                   limit: int = 20, offset: int = 0,
                   order_by: str = "composite_score") -> list[dict]:
    """查询项目列表（带最新评分）"""
    # 取最新评分日期
    latest_date_row = con.execute(
        "SELECT MAX(score_date) FROM scores"
    ).fetchone()
    latest_date = latest_date_row[0] if latest_date_row else None
    if not latest_date:
        return []

    where = ["p.is_active = 1"]
    params = [latest_date]  # score_date = ? 的第一个参数

    if category:
        where.append("p.category = ?")
        params.append(category)

    if min_score > 0:
        where.append("s.composite_score >= ?")
        params.append(min_score)

    where_clause = " AND ".join(where)

    valid_orders = {"composite_score", "momentum_score", "quality_score", "rank_total", "stars"}
    if order_by not in valid_orders:
        order_by = "composite_score"

    if order_by == "stars":
        join_snap = " LEFT JOIN snapshots snap ON snap.project_id = p.id AND snap.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots)"
        sql = f"""SELECT p.*, s.composite_score, s.momentum_score, s.quality_score,
                         snap.stars, s.rank_total, s.rank_category
                  FROM projects p
                  {join_snap}
                  LEFT JOIN scores s ON s.project_id = p.id AND s.score_date = ?
                  WHERE {where_clause}
                  ORDER BY snap.stars DESC NULLS LAST
                  LIMIT ? OFFSET ?"""
    else:
        sql = f"""SELECT p.*, s.composite_score, s.momentum_score, s.quality_score,
                         s.rank_total, s.rank_category
                  FROM projects p
                  LEFT JOIN scores s ON s.project_id = p.id AND s.score_date = ?
                  WHERE {where_clause}
                  ORDER BY s.{order_by} DESC NULLS LAST
                  LIMIT ? OFFSET ?"""

    params.extend([limit, offset])
    rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_trending(con, days: int = 7, limit: int = 20) -> list[dict]:
    """查询趋势项目（基于star增速）"""
    date_from = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")

    sql = """
        SELECT p.full_name, p.url, p.description, p.category, p.language,
               s_latest.stars as current_stars,
               s_latest.stars_7d as stars_7d,
               s_latest.stars_24h as stars_24h,
               sc.composite_score
        FROM projects p
        JOIN snapshots s_latest ON s_latest.project_id = p.id
            AND s_latest.snapshot_date = (SELECT MAX(snapshot_date) FROM snapshots)
        LEFT JOIN scores sc ON sc.project_id = p.id
            AND sc.score_date = (SELECT MAX(score_date) FROM scores)
        WHERE p.is_active = 1 AND s_latest.stars_7d > 0
        ORDER BY s_latest.stars_7d DESC
        LIMIT ?
    """
    rows = con.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_project_detail(con, full_name: str) -> dict | None:
    """获取项目详情+最新快照+评分"""
    project = con.execute(
        "SELECT * FROM projects WHERE full_name = ?", (full_name,)
    ).fetchone()
    if not project:
        return None

    result = dict(project)

    # 最新快照
    snap = con.execute(
        "SELECT * FROM snapshots WHERE project_id = ? ORDER BY snapshot_date DESC LIMIT 1",
        (result["id"],)
    ).fetchone()
    if snap:
        result["latest_snapshot"] = dict(snap)

    # 最新评分
    score = con.execute(
        "SELECT * FROM scores WHERE project_id = ? ORDER BY score_date DESC LIMIT 1",
        (result["id"],)
    ).fetchone()
    if score:
        result["latest_score"] = dict(score)

    # 历史快照（最近30天）
    history = con.execute(
        """SELECT snapshot_date, stars, stars_24h, stars_7d, hn_points
           FROM snapshots WHERE project_id = ?
           ORDER BY snapshot_date DESC LIMIT 30""",
        (result["id"],)
    ).fetchall()
    result["history"] = [dict(h) for h in history]

    # HN引用
    hn = con.execute(
        "SELECT * FROM hn_refs WHERE project_id = ? ORDER BY points DESC",
        (result["id"],)
    ).fetchall()
    result["hn_refs"] = [dict(h) for h in hn]

    return result


def get_stats(con) -> dict:
    """数据库统计"""
    stats = {}
    stats["total_projects"] = con.execute("SELECT COUNT(*) FROM projects WHERE is_active = 1").fetchone()[0]
    stats["total_snapshots"] = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    stats["total_hn_refs"] = con.execute("SELECT COUNT(*) FROM hn_refs").fetchone()[0]
    stats["latest_snapshot_date"] = con.execute("SELECT MAX(snapshot_date) FROM snapshots").fetchone()[0]
    stats["latest_score_date"] = con.execute("SELECT MAX(score_date) FROM scores").fetchone()[0]

    # 按分类统计
    cats = con.execute(
        "SELECT category, COUNT(*) as cnt FROM projects WHERE is_active = 1 GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    stats["by_category"] = {r["category"] or "uncategorized": r["cnt"] for r in cats}

    return stats
