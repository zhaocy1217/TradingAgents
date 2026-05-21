import os
import unittest

import pytest

from tradingagents.ui import chat_runner
from tradingagents.ui.chat_runner import (
    ChatInputError,
    ChatRunRequest,
    build_graph_config,
    run_chat_analysis,
)


class DummyTradingAgentsGraph:
    last_selected_analysts = None
    last_config = None
    last_propagate_args = None

    def __init__(self, selected_analysts, config, debug):
        self.__class__.last_selected_analysts = selected_analysts
        self.__class__.last_config = config

    def propagate(self, ticker, analysis_date, asset_type, progress_callback=None):
        self.__class__.last_propagate_args = (ticker, analysis_date, asset_type)
        self.__class__.last_progress_callback = progress_callback
        final_state = {
            "market_report": "market",
            "sentiment_report": "sentiment",
            "news_report": "news",
            "fundamentals_report": "fundamentals",
            "investment_plan": "investment",
            "trader_investment_plan": "trader",
            "final_trade_decision": "LONG",
        }
        return final_state, "BUY"


@pytest.mark.unit
class ChatRunnerTests(unittest.TestCase):
    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "test-key"

    def tearDown(self):
        os.environ.pop("OPENAI_API_KEY", None)

    def test_build_graph_config_sets_expected_values(self):
        request = ChatRunRequest(
            prompt="Analyze NVDA",
            ticker=" nvda ",
            analysis_date="2026-05-01",
            llm_provider="openai",
            quick_think_llm="gpt-5.4-mini",
            deep_think_llm="gpt-5.4",
            research_depth=3,
            output_language="English",
            backend_url="https://api.openai.com/v1",
            analysts=["market", "news"],
        )

        config, ticker, analysis_date, analysts = build_graph_config(request)

        self.assertEqual(ticker, "NVDA")
        self.assertEqual(analysis_date, "2026-05-01")
        self.assertEqual(analysts, ["market", "news"])
        self.assertEqual(config["llm_provider"], "openai")
        self.assertEqual(config["max_debate_rounds"], 3)
        self.assertEqual(config["max_risk_discuss_rounds"], 3)
        self.assertEqual(config["backend_url"], "https://api.openai.com/v1")

    def test_build_graph_config_requires_api_key_for_provider(self):
        os.environ.pop("OPENAI_API_KEY", None)
        request = ChatRunRequest(
            prompt="Analyze NVDA",
            ticker="NVDA",
            analysis_date="2026-05-01",
            llm_provider="openai",
        )

        with self.assertRaises(ChatInputError):
            build_graph_config(request)

    def test_run_chat_analysis_uses_graph_and_returns_decision(self):
        original_graph_cls = chat_runner.TradingAgentsGraph
        chat_runner.TradingAgentsGraph = DummyTradingAgentsGraph
        try:
            request = ChatRunRequest(
                prompt="Analyze BTC quickly",
                ticker="BTC-USD",
                analysis_date="2026-05-01",
                llm_provider="openai",
            )
            result = run_chat_analysis(request)
        finally:
            chat_runner.TradingAgentsGraph = original_graph_cls

        self.assertEqual(result.ticker, "BTC-USD")
        self.assertEqual(result.asset_type, "crypto")
        self.assertEqual(result.decision, "BUY")
        self.assertEqual(result.final_trade_decision, "LONG")
        self.assertEqual(
            DummyTradingAgentsGraph.last_propagate_args,
            ("BTC-USD", "2026-05-01", "crypto"),
        )
        self.assertEqual(
            DummyTradingAgentsGraph.last_selected_analysts,
            ["market", "social", "news"],
        )

