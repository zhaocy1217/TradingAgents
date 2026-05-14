"""Symbol search API."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from tradingagents.web.routers.common import open_db
from tradingagents.web.services.symbol_resolver import resolve_symbol_candidates

router = APIRouter(prefix="/api/symbols", tags=["symbols"])


@router.get("/search")
def search_symbols(request: Request, q: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=30)):
    with open_db(request) as conn:
        items = resolve_symbol_candidates(conn, q, limit=limit)
    return {"items": items, "total": len(items), "query": q}
