"""Markdown report formatter shared by batch and web paths."""

from __future__ import annotations

import json


def _md_section(title: str, body: object) -> str:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
    text = (text or "").strip()
    if not text:
        return ""
    return f"## {title}\n\n{text}\n\n"


def compose_stock_report_md(final_state: dict, processed_signal: str, company_name: str | None = None) -> str:
    inv = final_state.get("investment_debate_state") or {}
    risk = final_state.get("risk_debate_state") or {}
    title_name = (company_name or "").strip() or str(final_state.get("company_of_interest") or "").strip()
    parts = [
        f"# {title_name} — TradingAgents 报告\n\n",
        f"- **分析日期**: {final_state.get('trade_date')}\n\n",
        "---\n\n",
        _md_section("市场分析", final_state.get("market_report")),
        _md_section("情绪 / 社交", final_state.get("sentiment_report")),
        _md_section("新闻与资讯", final_state.get("news_report")),
        _md_section("基本面", final_state.get("fundamentals_report")),
        _md_section("研究团队结论", inv.get("judge_decision") or inv.get("history")),
        _md_section("交易员计划", final_state.get("trader_investment_plan")),
        _md_section("风控辩论结论", risk.get("judge_decision") or risk.get("history")),
        _md_section("投资组合最终决策", final_state.get("final_trade_decision")),
        _md_section("信号摘要（解析后）", processed_signal or ""),
    ]
    return "".join(p for p in parts if p)
