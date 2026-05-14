"""A-share universe and symbol conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass

import requests

EASTMONEY_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
}


@dataclass(frozen=True)
class ListedName:
    symbol: str
    name: str


def cn_six_digit_to_yahoo(code: str) -> str:
    c = str(code).strip().zfill(6)
    if not c.isdigit() or len(c) != 6:
        raise ValueError(f"expected 6-digit A-share code, got {code!r}")
    if c.startswith(("5", "6", "9")):
        return f"{c}.SS"
    return f"{c}.SZ"


def fetch_universe_eastmoney(
    top_n: int,
    *,
    page_size: int = 100,
    use_system_proxy: bool = False,
    timeout: float = 30.0,
) -> list[ListedName]:
    session = requests.Session()
    session.trust_env = use_system_proxy
    session.headers.update(EASTMONEY_HEADERS)

    out: list[ListedName] = []
    pn = 1
    while len(out) < top_n:
        params = {
            "pn": pn,
            "pz": min(page_size, top_n - len(out)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f20",
            "fs": EASTMONEY_FS,
            "fields": "f12,f14,f3,f8,f20",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        response = session.get(EASTMONEY_URL, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        diff = (payload.get("data") or {}).get("diff") or []
        if not diff:
            break
        for row in diff:
            code = str(row.get("f12", "")).strip()
            name = str(row.get("f14", "")).strip() or code
            if not code:
                continue
            out.append(ListedName(symbol=cn_six_digit_to_yahoo(code), name=name))
            if len(out) >= top_n:
                break
        pn += 1
        if len(diff) < params["pz"]:
            break
    return out[:top_n]


def resolve_top_universe(top_n: int) -> list[ListedName]:
    return fetch_universe_eastmoney(top_n, use_system_proxy=False)
