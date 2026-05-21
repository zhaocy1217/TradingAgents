from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from typing import Any, Callable

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.ui.progress import AnalysisProgress, ProgressTracker
from tradingagents.llm_clients.api_key_env import get_api_key_env

CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")
DEFAULT_ANALYSTS = ["market", "social", "news", "fundamentals"]


class ChatInputError(ValueError):
    """Raised when chat input is invalid or missing required config."""


@dataclass
class ChatRunRequest:
    prompt: str
    ticker: str
    analysis_date: str | date
    llm_provider: str = "deepseek"
    quick_think_llm: str = "deepseek-v4-flash"
    deep_think_llm: str = "deepseek-v4-pro"
    research_depth: int = 1
    output_language: str = "Chinese"
    backend_url: str | None = None
    analysts: list[str] | None = None


@dataclass
class ChatRunResult:
    ticker: str
    analysis_date: str
    asset_type: str
    decision: str
    final_trade_decision: str
    reports: dict[str, str]


def _normalize_ticker(ticker: str) -> str:
    normalized = (ticker or "").strip().upper()
    if not normalized:
        raise ChatInputError("Ticker is required.")
    return normalized


def _normalize_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d")
    except Exception as exc:  # pragma: no cover - exact parser error text not important
        raise ChatInputError("Analysis date must be in YYYY-MM-DD format.") from exc
    return parsed.strftime("%Y-%m-%d")


def _detect_asset_type(ticker: str) -> str:
    if ticker.endswith(CRYPTO_SUFFIXES):
        return "crypto"
    return "stock"


def _validate_api_key(provider: str) -> None:
    env_var = get_api_key_env(provider)
    if env_var and not os.environ.get(env_var):
        raise ChatInputError(
            f"Missing API key for provider '{provider}'. Set environment variable {env_var}."
        )


def build_graph_config(request: ChatRunRequest) -> tuple[dict[str, Any], str, str, list[str]]:
    ticker = _normalize_ticker(request.ticker)
    analysis_date = _normalize_date(request.analysis_date)
    provider = request.llm_provider.strip().lower()
    if not provider:
        raise ChatInputError("LLM provider is required.")

    _validate_api_key(provider)
    asset_type = _detect_asset_type(ticker)
    analysts = list(request.analysts or DEFAULT_ANALYSTS)
    if asset_type == "crypto":
        analysts = [analyst for analyst in analysts if analyst != "fundamentals"]
    if not analysts:
        raise ChatInputError("At least one analyst must be selected.")

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = provider
    config["quick_think_llm"] = request.quick_think_llm.strip()
    config["deep_think_llm"] = request.deep_think_llm.strip()
    config["max_debate_rounds"] = int(request.research_depth)
    config["max_risk_discuss_rounds"] = int(request.research_depth)
    config["output_language"] = request.output_language.strip() or "English"

    backend_url = (request.backend_url or "").strip()
    if backend_url:
        config["backend_url"] = backend_url

    return config, ticker, analysis_date, analysts


def run_chat_analysis(
    request: ChatRunRequest,
    on_progress: Callable[[AnalysisProgress], None] | None = None,
) -> ChatRunResult:
    config, ticker, analysis_date, analysts = build_graph_config(request)
    asset_type = _detect_asset_type(ticker)

    graph = TradingAgentsGraph(
        selected_analysts=analysts,
        config=config,
        debug=False,
    )

    tracker: ProgressTracker | None = None
    started_at = monotonic()

    def progress_callback(chunk: dict[str, Any]) -> None:
        if tracker is None or on_progress is None:
            return
        tracker.apply_chunk(chunk)
        on_progress(tracker.snapshot(monotonic() - started_at))

    if on_progress is not None:
        tracker = ProgressTracker(analysts)
        on_progress(tracker.snapshot(0.0))

    final_state, decision = graph.propagate(
        ticker,
        analysis_date,
        asset_type=asset_type,
        progress_callback=progress_callback if on_progress is not None else None,
    )

    if tracker is not None:
        tracker.mark_all_completed()
        if on_progress is not None:
            on_progress(tracker.snapshot(monotonic() - started_at))

    reports = {
        "market_report": final_state.get("market_report", ""),
        "sentiment_report": final_state.get("sentiment_report", ""),
        "news_report": final_state.get("news_report", ""),
        "fundamentals_report": final_state.get("fundamentals_report", ""),
        "investment_plan": final_state.get("investment_plan", ""),
        "trader_investment_plan": final_state.get("trader_investment_plan", ""),
        "final_trade_decision": final_state.get("final_trade_decision", ""),
    }

    return ChatRunResult(
        ticker=ticker,
        analysis_date=analysis_date,
        asset_type=asset_type,
        decision=decision,
        final_trade_decision=reports["final_trade_decision"],
        reports=reports,
    )

