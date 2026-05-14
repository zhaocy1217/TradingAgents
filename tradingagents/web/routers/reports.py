"""Report query and detail APIs."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request

from tradingagents.web.routers.common import open_db
from tradingagents.web.services.report_summary import (
    generate_concise_summary,
    inject_summary_into_report,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def list_reports(request: Request, query: str = "", limit: int = Query(default=50, ge=1, le=500)):
    with open_db(request) as conn:
        params: list[object] = []
        where = ""
        if query.strip():
            like = f"%{query.strip()}%"
            where = "WHERE symbol LIKE ? OR company_name LIKE ?"
            params.extend([like, like])
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT id, symbol, company_name, analysis_date, signal, created_at
            FROM analysis_reports
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


@router.get("/{report_id}")
def get_report(report_id: int, request: Request):
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
    item = dict(row)
    try:
        item["meta"] = json.loads(item.get("meta_json") or "{}")
    except json.JSONDecodeError:
        item["meta"] = {}
    concise_summary = (item.get("meta") or {}).get("concise_summary")
    if not concise_summary:
        concise_summary = generate_concise_summary(
            item.get("report_markdown") or "",
            item.get("signal") or "",
        )
    item["concise_summary"] = concise_summary
    item["report_markdown"] = inject_summary_into_report(
        item.get("report_markdown") or "",
        concise_summary,
    )
    return item
