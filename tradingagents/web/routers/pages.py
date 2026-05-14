"""Jinja page routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from tradingagents.web.routers.common import open_db
from tradingagents.web.services.report_summary import (
    generate_concise_summary,
    inject_summary_into_report,
)
from tradingagents.web.services.symbol_resolver import resolve_company_name_for_symbol

router = APIRouter(tags=["pages"])


def _patch_company_name(conn, item: dict) -> dict:
    current = str(item.get("company_name") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    if not symbol or (current and current != symbol):
        return item
    resolved = resolve_company_name_for_symbol(conn, symbol)
    if not resolved:
        return item
    item["company_name"] = resolved
    conn.execute("UPDATE analysis_reports SET company_name = ? WHERE id = ?", (resolved, item["id"]))
    return item


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
        items = [_patch_company_name(conn, dict(row)) for row in rows]
    return templates.TemplateResponse(
        request,
        "reports.html",
        {"title": "报告列表", "reports": items},
    )


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail_page(report_id: int, request: Request):
    templates = request.app.state.templates
    with open_db(request) as conn:
        row = conn.execute(
            """
            SELECT id, symbol, company_name, analysis_date, signal, report_markdown, meta_json, created_at
            FROM analysis_reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found")
        report = _patch_company_name(conn, dict(row))
        try:
            report["meta"] = json.loads(report.get("meta_json") or "{}")
        except json.JSONDecodeError:
            report["meta"] = {}
        report["concise_summary"] = generate_concise_summary(
            report.get("report_markdown") or "",
            report.get("signal") or "",
        )
        report["report_markdown"] = inject_summary_into_report(
            report.get("report_markdown") or "",
            report["concise_summary"],
        )
    return templates.TemplateResponse(
        request,
        "report_detail.html",
        {"title": f"报告 {report_id}", "report": report},
    )
