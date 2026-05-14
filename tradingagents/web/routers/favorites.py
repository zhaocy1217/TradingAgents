"""Favorites CRUD endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from tradingagents.web.routers.common import open_db
from tradingagents.web.schemas import FavoriteCreate

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


@router.get("")
def list_favorites(request: Request):
    with open_db(request) as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, company_name, note, created_at
            FROM favorites
            ORDER BY created_at DESC
            """
        ).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


@router.post("")
def create_favorite(payload: FavoriteCreate, request: Request):
    with open_db(request) as conn:
        conn.execute(
            """
            INSERT INTO favorites (symbol, company_name, note, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name=excluded.company_name,
                note=excluded.note
            """,
            (
                payload.symbol.strip().upper(),
                payload.company_name.strip(),
                payload.note.strip(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        row = conn.execute(
            """
            SELECT id, symbol, company_name, note, created_at
            FROM favorites
            WHERE symbol = ?
            """,
            (payload.symbol.strip().upper(),),
        ).fetchone()
    return dict(row)


@router.delete("/{symbol}")
def delete_favorite(symbol: str, request: Request):
    with open_db(request) as conn:
        cur = conn.execute(
            "DELETE FROM favorites WHERE symbol = ?",
            (symbol.strip().upper(),),
        )
    return {"deleted": cur.rowcount > 0, "symbol": symbol.strip().upper()}
