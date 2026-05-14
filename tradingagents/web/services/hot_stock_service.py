"""Weekly hot stock ingestion and query logic."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from tradingagents.a_share.universe import cn_six_digit_to_yahoo
from tradingagents.web.services.http_utils import build_http_session

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


def _fetch_hot_rank_eastmoney(limit: int = 20) -> list[dict]:
    # Mobile endpoint for hot ranking; response shape can change.
    url = "https://emappdata.eastmoney.com/stockrank/getAllCurrHqRankingList"
    payload = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "marketType": ""}
    session = build_http_session()
    session.headers.update(_HEADERS)
    resp = session.post(url, json=payload, timeout=12)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data") or []

    out: list[dict] = []
    for row in data[:limit]:
        code = str(row.get("sc", "")).strip()
        if code.startswith(("SZ", "SH")):
            code = code[2:]
        if len(code) != 6 or not code.isdigit():
            continue
        out.append(
            {
                "symbol": cn_six_digit_to_yahoo(code),
                "company_name": str(row.get("nm") or row.get("name") or code),
                "hot_score": float(row.get("hrc", 0) or 0),
                "source": "eastmoney_rank",
                "extra": row,
            }
        )
    return out


def _fallback_hot_from_quote(limit: int = 20) -> list[dict]:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": max(limit, 20),
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f8",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f3,f8",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    session = build_http_session()
    session.headers.update(_HEADERS)
    resp = session.get(url, params=params, timeout=12)
    resp.raise_for_status()
    rows = (resp.json().get("data") or {}).get("diff") or []

    out: list[dict] = []
    for row in rows[:limit]:
        code = str(row.get("f12", "")).strip()
        if not code:
            continue
        turnover = float(row.get("f8", 0) or 0)
        pct = abs(float(row.get("f3", 0) or 0))
        score = round(turnover * 0.7 + pct * 0.3, 4)
        out.append(
            {
                "symbol": cn_six_digit_to_yahoo(code),
                "company_name": str(row.get("f14", "")) or code,
                "hot_score": score,
                "source": "eastmoney_quote_fallback",
                "extra": row,
            }
        )
    return out


def refresh_hot_stocks(conn, days: int = 7, limit: int = 20) -> dict:
    today = date.today()
    inserted = 0
    used_source = "eastmoney_rank"
    for delta in range(days):
        rank_date = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            items = _fetch_hot_rank_eastmoney(limit=limit)
            used_source = "eastmoney_rank"
        except Exception:
            items = _fallback_hot_from_quote(limit=limit)
            used_source = "eastmoney_quote_fallback"

        conn.execute("DELETE FROM hot_stocks_weekly WHERE rank_date = ?", (rank_date,))
        for item in items:
            conn.execute(
                """
                INSERT INTO hot_stocks_weekly
                (symbol, company_name, hot_score, rank_date, source, extra_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["symbol"],
                    item["company_name"],
                    item["hot_score"],
                    rank_date,
                    used_source,
                    json.dumps(item.get("extra", {}), ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            inserted += 1
    return {"days": days, "inserted": inserted, "source": used_source}


def get_week_hot_stocks(conn, days: int = 7, limit: int = 100) -> list[dict]:
    start = (date.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT symbol, company_name, hot_score, rank_date, source, extra_json
        FROM hot_stocks_weekly
        WHERE rank_date >= ?
        ORDER BY rank_date DESC, hot_score DESC
        LIMIT ?
        """,
        (start, limit),
    ).fetchall()
    return [dict(row) for row in rows]
