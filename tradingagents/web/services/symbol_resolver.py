"""Resolve user query/company name to tradable symbols."""

from __future__ import annotations

import re
from datetime import datetime

from tradingagents.a_share.universe import cn_six_digit_to_yahoo
from tradingagents.web.services.http_utils import build_http_session

_AK_NAME_BY_CODE_CACHE: dict[str, str] | None = None


def _looks_like_symbol(raw: str) -> bool:
    up = raw.upper()
    return bool(re.fullmatch(r"\d{6}\.(SS|SZ)", up) or re.fullmatch(r"\d{6}", up))


def _normalize_symbol(raw: str) -> str:
    up = raw.upper().strip()
    if re.fullmatch(r"\d{6}", up):
        return cn_six_digit_to_yahoo(up)
    return up


def _cache_symbol(conn, symbol: str, company_name: str, source: str) -> None:
    conn.execute(
        """
        INSERT INTO symbols_cache(symbol, company_name, market, source, updated_at)
        VALUES (?, ?, 'CN', ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            company_name=excluded.company_name,
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        (symbol, company_name, source, datetime.now().isoformat(timespec="seconds")),
    )


def _search_cache(conn, query: str, limit: int) -> list[dict]:
    like = f"%{query.strip()}%"
    rows = conn.execute(
        """
        SELECT symbol, company_name, market, source
        FROM symbols_cache
        WHERE company_name LIKE ? OR symbol LIKE ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (like, like, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _search_eastmoney(query: str, limit: int = 10) -> list[dict]:
    # Eastmoney search endpoint may evolve; keep parser defensive.
    url = "https://searchapi.eastmoney.com/api/suggest/get"
    params = {
        "input": query,
        "type": 14,
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": max(limit, 10),
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }
    session = build_http_session()
    session.headers.update(headers)
    response = session.get(url, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("QuotationCodeTable", {}).get("Data", []) or []
    out: list[dict] = []
    for row in rows[:limit]:
        code = str(row.get("Code", "")).strip()
        name = str(row.get("Name", "")).strip() or code
        if not re.fullmatch(r"\d{6}", code):
            continue
        out.append(
            {
                "symbol": cn_six_digit_to_yahoo(code),
                "company_name": name,
                "market": "CN",
                "source": "eastmoney",
            }
        )
    return out


def _search_akshare(query: str, limit: int = 10) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        return []

    df = ak.stock_info_a_code_name()
    mask = (
        df["name"].astype(str).str.contains(query, regex=False, na=False)
        | df["code"].astype(str).str.contains(query, regex=False, na=False)
    )
    out: list[dict] = []
    for _, row in df.loc[mask].head(limit).iterrows():
        code = str(row["code"]).strip().zfill(6)
        out.append(
            {
                "symbol": cn_six_digit_to_yahoo(code),
                "company_name": str(row["name"]),
                "market": "CN",
                "source": "akshare",
            }
        )
    return out


def _normalize_code_from_symbol(symbol: str) -> str | None:
    raw = (symbol or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SS|SZ)", raw):
        return raw[:6]
    if re.fullmatch(r"\d{6}", raw):
        return raw
    return None


def _load_ak_name_by_code() -> dict[str, str]:
    global _AK_NAME_BY_CODE_CACHE
    if _AK_NAME_BY_CODE_CACHE is not None:
        return _AK_NAME_BY_CODE_CACHE
    try:
        import akshare as ak
    except ImportError:
        _AK_NAME_BY_CODE_CACHE = {}
        return _AK_NAME_BY_CODE_CACHE
    df = ak.stock_info_a_code_name()
    _AK_NAME_BY_CODE_CACHE = {
        str(row["code"]).strip().zfill(6): str(row["name"]).strip()
        for _, row in df.iterrows()
        if str(row.get("code", "")).strip() and str(row.get("name", "")).strip()
    }
    return _AK_NAME_BY_CODE_CACHE


def resolve_company_name_for_symbol(conn, symbol: str) -> str | None:
    normalized = _normalize_symbol(symbol)
    row = conn.execute(
        """
        SELECT company_name
        FROM symbols_cache
        WHERE symbol = ?
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if row and str(row["company_name"]).strip():
        return str(row["company_name"]).strip()

    # Release the DB snapshot before loading the full akshare universe (very slow).
    conn.commit()

    code = _normalize_code_from_symbol(normalized)
    if not code:
        return None
    name_by_code = _load_ak_name_by_code()
    name = (name_by_code.get(code) or "").strip()
    if not name:
        return None
    _cache_symbol(conn, normalized, name, "akshare")
    return name


def resolve_symbol_candidates(conn, query: str, limit: int = 10) -> list[dict]:
    cleaned = query.strip()
    if not cleaned:
        return []

    if _looks_like_symbol(cleaned):
        symbol = _normalize_symbol(cleaned)
        return [{"symbol": symbol, "company_name": symbol, "market": "CN", "source": "input"}]

    cached = _search_cache(conn, cleaned, limit)
    if cached:
        return cached

    # End any read transaction before slow outbound HTTP so other requests are not blocked.
    conn.commit()

    fetched: list[dict] = []
    try:
        fetched = _search_eastmoney(cleaned, limit=limit)
    except Exception:
        fetched = []

    if not fetched:
        fetched = _search_akshare(cleaned, limit=limit)

    for item in fetched:
        _cache_symbol(conn, item["symbol"], item["company_name"], item["source"])
    return fetched
