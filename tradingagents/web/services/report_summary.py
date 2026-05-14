"""Generate concise actionable summary from markdown report content."""

from __future__ import annotations

import re


def _extract_section(markdown: str, title: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(title)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown or "")
    if not match:
        return ""
    return (match.group("body") or "").strip()


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _extract_rating_text(signal: str, decision_text: str) -> str:
    combined = f"{signal}\n{decision_text}".lower()
    for key in ("buy", "overweight", "hold", "underweight", "sell"):
        if key in combined:
            return key
    if "买入" in combined:
        return "buy"
    if "增持" in combined:
        return "overweight"
    if "持有" in combined:
        return "hold"
    if "减持" in combined:
        return "underweight"
    if "卖出" in combined:
        return "sell"
    return "hold"


def _direction_from_rating(rating: str) -> str:
    if rating in {"buy", "overweight"}:
        return "看涨"
    if rating in {"underweight", "sell"}:
        return "看跌"
    return "中性震荡"


def _advice_from_rating(rating: str) -> str:
    if rating == "buy":
        return "可考虑分批建仓，设置止损并控制单票仓位。"
    if rating == "overweight":
        return "偏多思路，可在回调时分批加仓，避免追高。"
    if rating == "underweight":
        return "以防守为主，考虑减仓或降低风险暴露。"
    if rating == "sell":
        return "建议回避或退出，优先保护本金。"
    return "暂以观望或持有为主，等待更清晰信号。"


def generate_concise_summary(report_markdown: str, signal: str) -> dict[str, str]:
    """Return concise direction/suggestion based on report markdown."""
    final_decision = _extract_section(report_markdown, "投资组合最终决策")
    signal_summary = _extract_section(report_markdown, "信号摘要（解析后）")
    rating = _extract_rating_text(signal or "", f"{final_decision}\n{signal_summary}")
    direction = _direction_from_rating(rating)
    suggestion = _advice_from_rating(rating)

    rationale_source = final_decision or signal_summary or signal or "暂无足够信息"
    rationale = _clean_line(rationale_source)
    return {
        "direction": direction,
        "suggestion": suggestion,
        "rationale": rationale,
    }


def inject_summary_into_report(report_markdown: str, concise_summary: dict[str, str] | None) -> str:
    """Insert concise summary near the top of markdown report."""
    raw = report_markdown or ""
    summary = concise_summary or {}
    if not raw.strip():
        return raw
    if "## 精简结论（方向与建议）" in raw:
        return raw

    direction = (summary.get("direction") or "中性震荡").strip()
    suggestion = (summary.get("suggestion") or "暂以观望或持有为主，等待更清晰信号。").strip()
    rationale = (summary.get("rationale") or "暂无足够信息").strip()
    block = (
        "## 精简结论（方向与建议）\n\n"
        f"- **预期方向**: {direction}\n"
        f"- **操作建议**: {suggestion}\n"
        f"- **核心依据**: {rationale}\n\n"
    )

    marker = "\n---\n\n"
    if marker in raw:
        head, tail = raw.split(marker, 1)
        return f"{head}\n{block}---\n\n{tail}"
    return f"{raw}\n\n{block}"

