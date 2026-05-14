"""Jinja page routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from tradingagents.web.routers.common import open_db

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": "TradingAgents A股分析"},
    )


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    templates = request.app.state.templates
    with open_db(request) as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, company_name, analysis_date, signal, created_at
            FROM analysis_reports
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "reports.html",
        {"title": "报告列表", "reports": [dict(row) for row in rows]},
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail_page(report_id: int, request: Request):
    templates = request.app.state.templates
    with open_db(request) as conn:
        row = conn.execute(
            """
            SELECT id, symbol, company_name, analysis_date, signal, report_markdown, created_at
            FROM analysis_reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse(
        request,
        "report_detail.html",
        {"title": f"报告 {report_id}", "report": dict(row)},
    )
