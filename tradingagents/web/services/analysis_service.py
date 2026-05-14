"""Analysis service wrapping TradingAgentsGraph for web APIs."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal

from tradingagents.a_share.report_formatter import compose_stock_report_md
from tradingagents.a_share.universe import ListedName, resolve_top_universe
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.web.services.report_summary import (
    generate_concise_summary,
    inject_summary_into_report,
)
from tradingagents.web.services.symbol_resolver import (
    resolve_company_name_for_symbol,
    resolve_symbol_candidates,
)

logger = logging.getLogger(__name__)


class JobCancelledError(RuntimeError):
    """Raised when a background job receives a cancel request."""


def latest_weekday(d: date) -> date:
    cur = d
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def normalize_analysis_date(analysis_date: str | None) -> str:
    if analysis_date:
        return analysis_date
    return latest_weekday(date.today()).strftime("%Y-%m-%d")


def _graph_phase_by_elapsed(elapsed_sec: int) -> str:
    # Trading graph can loop in debates, so this is an estimated user-facing phase.
    phase_markers = [
        (0, "Analyst团队: Market/Social/News/Fundamentals"),
        (20, "Research辩论: Bull vs Bear"),
        (45, "Research Manager汇总结论"),
        (60, "Trader生成交易计划"),
        (80, "Risk辩论: Aggressive/Conservative/Neutral"),
        (105, "Portfolio Manager最终决策"),
    ]
    current = phase_markers[0][1]
    for threshold, label in phase_markers:
        if elapsed_sec >= threshold:
            current = label
        else:
            break
    return current


def _build_graph_config(mode: Literal["light", "deep"] = "light") -> dict:
    config = DEFAULT_CONFIG.copy()
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    deepseek_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if (config.get("llm_provider") or "").lower() == "openai" and not openai_key and deepseek_key:
        config["llm_provider"] = "deepseek"
        config["deep_think_llm"] = "deepseek-v4-pro"
        config["quick_think_llm"] = "deepseek-v4-flash"
    if mode == "deep":
        # 深度模式：保持旧版 Web 策略（更充分讨论，耗时更长）。
        config["max_debate_rounds"] = 5
        config["max_risk_discuss_rounds"] = 5
    else:
        # 轻度模式：默认优先响应速度，沿用全局配置并限制极端高轮数。
        config["max_debate_rounds"] = min(int(config.get("max_debate_rounds", 1)), 2)
        config["max_risk_discuss_rounds"] = min(int(config.get("max_risk_discuss_rounds", 1)), 2)
    config["checkpoint_enabled"] = False
    return config


def _insert_report(
    conn,
    *,
    symbol: str,
    company_name: str,
    analysis_date: str,
    signal: str,
    report_markdown: str,
    meta: dict,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO analysis_reports
        (symbol, company_name, analysis_date, signal, report_markdown, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            company_name,
            analysis_date,
            signal,
            report_markdown,
            json.dumps(meta, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return int(cur.lastrowid)


def analyze_symbol_and_store(
    conn,
    *,
    symbol: str | None = None,
    query: str | None = None,
    analysis_date: str | None = None,
    analysis_mode: Literal["light", "deep"] = "light",
    progress_cb: Callable[[str, str], None] | None = None,
    should_cancel_cb: Callable[[], bool] | None = None,
) -> dict:
    trade_date = normalize_analysis_date(analysis_date)
    resolved_symbol = (symbol or "").strip().upper()
    company_name = resolved_symbol
    if resolved_symbol:
        resolved_name = resolve_company_name_for_symbol(conn, resolved_symbol)
        if resolved_name:
            company_name = resolved_name
    if progress_cb:
        progress_cb("prepare", "准备参数并校验输入")
    if should_cancel_cb and should_cancel_cb():
        raise JobCancelledError("任务在开始前已取消")

    if not resolved_symbol:
        if not query:
            raise ValueError("Either `symbol` or `query` is required")
        if progress_cb:
            progress_cb("resolve_symbol", "根据公司名解析股票代码")
        candidates = resolve_symbol_candidates(conn, query, limit=1)
        if not candidates:
            raise ValueError(f"No symbol candidates found for query: {query}")
        resolved_symbol = candidates[0]["symbol"]
        company_name = candidates[0]["company_name"]

    if should_cancel_cb and should_cancel_cb():
        raise JobCancelledError("任务在图初始化前已取消")
    if progress_cb:
        progress_cb("init_graph", f"初始化分析图: {resolved_symbol}")
    config = _build_graph_config(analysis_mode)
    graph = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        config=config,
        debug=False,
    )
    if progress_cb:
        progress_cb(
            "run_graph",
            (
                f"运行多代理分析: {resolved_symbol} / {trade_date}\n"
                "执行链路: Analysts(Market/Social/News/Fundamentals) -> "
                "Bull/Bear -> ResearchManager -> Trader -> "
                "Risk(Aggressive/Conservative/Neutral) -> PortfolioManager"
            ),
        )

    run_result: dict[str, object] = {}
    run_error: dict[str, Exception] = {}
    progress_trace = {
        "market_done": False,
        "social_done": False,
        "news_done": False,
        "fundamentals_done": False,
        "research_round": 0,
        "research_manager_done": False,
        "trader_done": False,
        "risk_round": 0,
        "risk_judge_done": False,
        "portfolio_done": False,
        "last_detail": "",
    }

    def _emit_detail(message: str) -> None:
        if not progress_cb:
            return
        progress_trace["last_detail"] = message
        progress_cb("run_graph", message)

    def _on_state_update(state: dict[str, Any]) -> None:
        """Emit milestone-based progress from graph state snapshots."""
        if not progress_cb:
            return

        if state.get("market_report") and not progress_trace["market_done"]:
            progress_trace["market_done"] = True
            _emit_detail("Market Analyst 已完成")
        if state.get("sentiment_report") and not progress_trace["social_done"]:
            progress_trace["social_done"] = True
            _emit_detail("Social Analyst 已完成")
        if state.get("news_report") and not progress_trace["news_done"]:
            progress_trace["news_done"] = True
            _emit_detail("News Analyst 已完成")
        if state.get("fundamentals_report") and not progress_trace["fundamentals_done"]:
            progress_trace["fundamentals_done"] = True
            _emit_detail("Fundamentals Analyst 已完成")

        invest_state = state.get("investment_debate_state") or {}
        invest_round = int(invest_state.get("count") or 0)
        if invest_round > int(progress_trace["research_round"]):
            progress_trace["research_round"] = invest_round
            _emit_detail(f"Research 辩论进行到第 {invest_round} 轮")
        if state.get("investment_plan") and not progress_trace["research_manager_done"]:
            progress_trace["research_manager_done"] = True
            _emit_detail("Research Manager 已产出投资结论")

        if state.get("trader_investment_plan") and not progress_trace["trader_done"]:
            progress_trace["trader_done"] = True
            _emit_detail("Trader 已生成交易计划")

        risk_state = state.get("risk_debate_state") or {}
        risk_round = int(risk_state.get("count") or 0)
        if risk_round > int(progress_trace["risk_round"]):
            progress_trace["risk_round"] = risk_round
            _emit_detail(f"Risk 辩论进行到第 {risk_round} 轮")
        if risk_state.get("judge_decision") and not progress_trace["risk_judge_done"]:
            progress_trace["risk_judge_done"] = True
            _emit_detail("Risk 辩论已收敛，提交 Portfolio Manager")

        if state.get("final_trade_decision") and not progress_trace["portfolio_done"]:
            progress_trace["portfolio_done"] = True
            _emit_detail("Portfolio Manager 已产出最终决策，正在收尾")

    def _target() -> None:
        try:
            final_state, processed_signal = graph.propagate(
                resolved_symbol,
                trade_date,
                state_update_cb=_on_state_update,
            )
            run_result["final_state"] = final_state
            run_result["processed_signal"] = processed_signal
        except Exception as exc:  # pragma: no cover - passthrough from graph internals
            run_error["error"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    started_at = time.time()
    while worker.is_alive():
        elapsed = int(time.time() - started_at)
        if progress_cb and elapsed > 0 and elapsed % 5 == 0:
            detail = str(progress_trace.get("last_detail") or "").strip()
            phase = detail or _graph_phase_by_elapsed(elapsed)
            msg = f"{phase}，已耗时 {elapsed}s"
            if should_cancel_cb and should_cancel_cb():
                msg += "（已收到取消请求，将在当前阶段收尾后停止）"
            progress_cb("run_graph", msg)
            time.sleep(1.0)
        else:
            time.sleep(0.5)
    worker.join()
    if run_error.get("error"):
        raise run_error["error"]

    final_state = run_result["final_state"]
    processed_signal = run_result["processed_signal"]
    if should_cancel_cb and should_cancel_cb():
        raise JobCancelledError("任务在生成结果后被取消")
    if progress_cb:
        progress_cb("format_report", "整理并格式化报告")
    report_markdown = compose_stock_report_md(final_state, str(processed_signal))
    concise_summary = generate_concise_summary(report_markdown, str(processed_signal))
    report_markdown = inject_summary_into_report(report_markdown, concise_summary)

    if progress_cb:
        progress_cb("store_report", "写入数据库")
    report_id = _insert_report(
        conn,
        symbol=resolved_symbol,
        company_name=final_state.get("company_of_interest") or company_name,
        analysis_date=trade_date,
        signal=str(processed_signal),
        report_markdown=report_markdown,
        meta={
            "query": query,
            "provider": config.get("llm_provider"),
            "analysis_mode": analysis_mode,
            "concise_summary": concise_summary,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    return {
        "report_id": report_id,
        "symbol": resolved_symbol,
        "company_name": final_state.get("company_of_interest") or company_name,
        "analysis_date": trade_date,
        "signal": str(processed_signal),
        "concise_summary": concise_summary,
        "report_markdown": report_markdown,
    }


def _analyze_listed_name(conn, listed_name: ListedName, analysis_date: str) -> dict:
    return analyze_symbol_and_store(
        conn,
        symbol=listed_name.symbol,
        query=listed_name.name,
        analysis_date=analysis_date,
    )


def analyze_top_and_store(
    conn,
    *,
    top_n: int,
    analysis_date: str | None = None,
    max_stocks: int | None = None,
    analysis_mode: Literal["light", "deep"] = "light",
    progress_cb: Callable[[str, str], None] | None = None,
    should_cancel_cb: Callable[[], bool] | None = None,
) -> dict:
    if should_cancel_cb and should_cancel_cb():
        raise JobCancelledError("任务在开始前已取消")
    if progress_cb:
        progress_cb("resolve_universe", f"拉取Top{top_n}股票池")
    trade_date = normalize_analysis_date(analysis_date)
    universe = resolve_top_universe(top_n)
    to_run = universe[: max_stocks or len(universe)]

    results: list[dict] = []
    errors: list[dict] = []
    for index, listed_name in enumerate(to_run, start=1):
        if should_cancel_cb and should_cancel_cb():
            raise JobCancelledError(f"任务在第 {index}/{len(to_run)} 只股票前取消")
        if progress_cb:
            progress_cb("batch_progress", f"分析进度 {index}/{len(to_run)}: {listed_name.symbol}")
        try:
            results.append(
                analyze_symbol_and_store(
                    conn,
                    symbol=listed_name.symbol,
                    query=listed_name.name,
                    analysis_date=trade_date,
                    analysis_mode=analysis_mode,
                    progress_cb=progress_cb,
                    should_cancel_cb=should_cancel_cb,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "symbol": listed_name.symbol,
                    "company_name": listed_name.name,
                    "error": str(exc),
                }
            )
    return {
        "analysis_date": trade_date,
        "requested_top_n": top_n,
        "attempted": len(to_run),
        "succeeded": len(results),
        "failed": len(errors),
        "items": results,
        "errors": errors,
    }
