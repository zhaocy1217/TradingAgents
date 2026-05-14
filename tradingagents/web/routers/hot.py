"""Hot stock APIs."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from tradingagents.web.routers.common import open_db
from tradingagents.web.services.hot_stock_service import get_week_hot_stocks, refresh_hot_stocks

router = APIRouter(prefix="/api/hot", tags=["hot-stocks"])


@router.get("/week")
def get_hot_week(request: Request, days: int = Query(default=7, ge=1, le=14), limit: int = Query(default=100, ge=1, le=500)):
    with open_db(request) as conn:
        items = get_week_hot_stocks(conn, days=days, limit=limit)
    return {"items": items, "total": len(items), "days": days}


@router.post("/refresh")
def refresh_hot_week(request: Request, days: int = Query(default=7, ge=1, le=14), limit: int = Query(default=20, ge=1, le=100)):
    with open_db(request) as conn:
        result = refresh_hot_stocks(conn, days=days, limit=limit)
        items = get_week_hot_stocks(conn, days=days, limit=days * limit)
    return {"refresh": result, "items": items}
