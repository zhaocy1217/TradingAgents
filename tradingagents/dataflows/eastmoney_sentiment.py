"""Eastmoney-derived sentiment metrics via AkShare.

Provides a compact textual block for prompt injection in the sentiment analyst.
The data comes from Eastmoney's "千股千评" endpoints wrapped by AkShare.
"""

from __future__ import annotations

from datetime import date


def _symbol_to_cn_code(ticker: str) -> str:
    raw = (ticker or "").strip().upper()
    if len(raw) == 6 and raw.isdigit():
        return raw
    if len(raw) >= 9 and raw[:6].isdigit() and raw[6] == "." and raw[7:] in {"SS", "SZ"}:
        return raw[:6]
    return raw


def _fmt_day(v: object) -> str:
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return str(v)


def fetch_eastmoney_sentiment_metrics(ticker: str) -> str:
    """Fetch Eastmoney sentiment metrics for a CN ticker.

    Returns a human-readable multi-line block. Never raises; returns a
    placeholder string when unavailable.
    """
    code = _symbol_to_cn_code(ticker)
    if not (len(code) == 6 and code.isdigit()):
        return f"<eastmoney sentiment unavailable: unsupported ticker format {ticker}>"

    try:
        import akshare as ak
    except ImportError:
        return "<eastmoney sentiment unavailable: akshare not installed>"

    lines: list[str] = [f"Eastmoney sentiment snapshot for {code}:"]

    try:
        desire_df = ak.stock_comment_detail_scrd_desire_em(symbol=code)
        if not desire_df.empty:
            row = desire_df.iloc[0]
            lines.append(
                "- 参与意愿: {val:.2f} (5日均值 {avg5:.2f}, 当日变化 {chg:.2f}, 日期 {d})".format(
                    val=float(row.get("参与意愿", 0) or 0),
                    avg5=float(row.get("5日平均参与意愿", 0) or 0),
                    chg=float(row.get("参与意愿变化", 0) or 0),
                    d=_fmt_day(row.get("交易日期", "")),
                )
            )
    except Exception as exc:  # pragma: no cover
        lines.append(f"- 参与意愿: <unavailable: {type(exc).__name__}>")

    try:
        focus_df = ak.stock_comment_detail_scrd_focus_em(symbol=code)
        if not focus_df.empty:
            row = focus_df.iloc[0]
            lines.append(
                "- 用户关注指数: {val:.2f} (日期 {d})".format(
                    val=float(row.get("用户关注指数", 0) or 0),
                    d=_fmt_day(row.get("交易日", "")),
                )
            )
    except Exception as exc:  # pragma: no cover
        lines.append(f"- 用户关注指数: <unavailable: {type(exc).__name__}>")

    try:
        score_df = ak.stock_comment_detail_zhpj_lspf_em(symbol=code)
        if not score_df.empty:
            row = score_df.iloc[0]
            lines.append(
                "- 综合评分: {val:.2f} (日期 {d})".format(
                    val=float(row.get("评分", 0) or 0),
                    d=_fmt_day(row.get("交易日", "")),
                )
            )
    except Exception as exc:  # pragma: no cover
        lines.append(f"- 综合评分: <unavailable: {type(exc).__name__}>")

    try:
        inst_df = ak.stock_comment_detail_zlkp_jgcyd_em(symbol=code)
        if not inst_df.empty:
            row = inst_df.iloc[0]
            lines.append(
                "- 机构参与度: {val:.2f} (日期 {d})".format(
                    val=float(row.get("机构参与度", 0) or 0),
                    d=_fmt_day(row.get("交易日", "")),
                )
            )
    except Exception as exc:  # pragma: no cover
        lines.append(f"- 机构参与度: <unavailable: {type(exc).__name__}>")

    if len(lines) == 1:
        return f"<eastmoney sentiment unavailable for {code}>"
    return "\n".join(lines)

