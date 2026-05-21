import unittest

import pytest

from tradingagents.ui.progress import ProgressTracker, format_progress_markdown


@pytest.mark.unit
class UIProgressTests(unittest.TestCase):
    def test_tracker_marks_analyst_completed_from_chunk(self):
        tracker = ProgressTracker(["market", "news"])
        tracker.apply_chunk({"market_report": "done"})

        self.assertEqual(tracker.agent_status["Market Analyst"], "completed")
        self.assertEqual(tracker.agent_status["News Analyst"], "in_progress")

    def test_format_progress_markdown_includes_current_step(self):
        tracker = ProgressTracker(["market"])
        progress = tracker.snapshot(elapsed_seconds=42.0)
        text = format_progress_markdown(progress)

        self.assertIn("已运行", text)
        self.assertIn("Market Analyst", text)
