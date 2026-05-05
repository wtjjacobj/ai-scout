"""
AI Scout REST API — FastAPI
给外部AI Agent调用的HTTP接口
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone, timedelta

from .db import get_db, query_projects, query_trending, get_project_detail, get_stats

CST = timezone(timedelta(hours=8))

app = FastAPI(
    title="AI Scout",
    description="AI项目发现引擎 — 发现→分类→评分→API。AI Agent直接查今天该关注什么AI项目。",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    """服务状态"""
    con = get_db()
    try:
        stats = get_stats(con)
        return {"status": "ok", "version": "0.2.0", "stats": stats}
    finally:
        con.close()


@app.get("/api/projects")
def list_projects(
    category: str = Query("", description="分类过滤: framework/model/tool/data/infra/other"),
    min_score: float = Query(0, description="最低综合分"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    order_by: str = Query("composite_score", description="排序: composite_score/momentum_score/quality_score/stars"),
):
    """查询项目列表（带评分）"""
    con = get_db()
    try:
        projects = query_projects(con, category=category, min_score=min_score,
                                  limit=limit, offset=offset, order_by=order_by)
        return {"count": len(projects), "projects": projects}
    finally:
        con.close()


@app.get("/api/projects/{full_name:path}")
def get_project(full_name: str):
    """获取项目详情（含历史快照、评分、HN引用）"""
    con = get_db()
    try:
        detail = get_project_detail(con, full_name)
        if not detail:
            return {"error": "Project not found", "full_name": full_name}
        return detail
    finally:
        con.close()


@app.get("/api/trending")
def trending(
    days: int = Query(7, ge=1, le=30, description="趋势周期(天)"),
    limit: int = Query(20, ge=1, le=100),
):
    """查询趋势项目（基于star增速）"""
    con = get_db()
    try:
        results = query_trending(con, days=days, limit=limit)
        return {"count": len(results), "projects": results}
    finally:
        con.close()


@app.get("/api/categories")
def list_categories():
    """获取分类统计"""
    con = get_db()
    try:
        stats = get_stats(con)
        return {"categories": stats.get("by_category", {})}
    finally:
        con.close()


@app.get("/api/daily")
def daily_report(limit: int = Query(20, ge=1, le=50)):
    """每日精选 — 综合分最高的项目"""
    con = get_db()
    try:
        projects = query_projects(con, limit=limit, order_by="composite_score")
        return {
            "date": datetime.now(CST).strftime("%Y-%m-%d"),
            "count": len(projects),
            "projects": projects,
        }
    finally:
        con.close()
