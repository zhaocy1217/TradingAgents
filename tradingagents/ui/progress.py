from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tradingagents.graph.analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
    sync_analyst_tracker_from_chunk,
)

ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

RESEARCH_TEAM = ["Bull Researcher", "Bear Researcher", "Research Manager"]
RISK_TEAM = ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst", "Portfolio Manager"]

STATUS_ICONS = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⏳",
}


@dataclass
class AnalysisProgress:
    elapsed_seconds: float
    current_step: str
    last_activity: str
    agent_status: dict[str, str]
    completed_steps: int
    total_steps: int
    tool_call_count: int
    message_count: int


@dataclass
class ProgressTracker:
    selected_analysts: list[str]
    agent_status: dict[str, str] = field(default_factory=dict)
    report_sections: dict[str, str | None] = field(default_factory=dict)
    last_activity: str = "正在初始化分析流程…"
    tool_call_count: int = 0
    message_count: int = 0
    _processed_message_ids: set[str] = field(default_factory=set)
    _wall_time_tracker: AnalystWallTimeTracker | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        selected = [a.lower() for a in self.selected_analysts]
        for analyst_key in ANALYST_ORDER:
            if analyst_key in selected:
                self.agent_status[ANALYST_AGENT_NAMES[analyst_key]] = "pending"
        for agent in RESEARCH_TEAM + ["Trader"] + RISK_TEAM:
            self.agent_status[agent] = "pending"

        for analyst_key in selected:
            report_key = ANALYST_REPORT_MAP.get(analyst_key)
            if report_key:
                self.report_sections[report_key] = None
        for section in ("investment_plan", "trader_investment_plan", "final_trade_decision"):
            self.report_sections[section] = None

        plan = build_analyst_execution_plan(
            selected,
            concurrency_limit=1,
        )
        self._wall_time_tracker = AnalystWallTimeTracker(plan)
        if selected:
            first = ANALYST_AGENT_NAMES[selected[0]]
            self.agent_status[first] = "in_progress"
            self.last_activity = f"{first} 已开始分析"

    def snapshot(self, elapsed_seconds: float) -> AnalysisProgress:
        current = self._current_step_label()
        completed, total = self._step_counts()
        return AnalysisProgress(
            elapsed_seconds=elapsed_seconds,
            current_step=current,
            last_activity=self.last_activity,
            agent_status=dict(self.agent_status),
            completed_steps=completed,
            total_steps=total,
            tool_call_count=self.tool_call_count,
            message_count=self.message_count,
        )

    def apply_chunk(self, chunk: dict[str, Any]) -> None:
        self._process_messages(chunk)
        self._update_analyst_statuses(chunk)
        self._process_investment_debate(chunk)
        self._process_trader(chunk)
        self._process_risk_debate(chunk)

    def mark_all_completed(self) -> None:
        for agent in self.agent_status:
            self.agent_status[agent] = "completed"
        self.last_activity = "分析已完成"

    def _current_step_label(self) -> str:
        for agent, status in self.agent_status.items():
            if status == "in_progress":
                return agent
        pending = [a for a, s in self.agent_status.items() if s == "pending"]
        if pending:
            return pending[0]
        return "收尾中"

    def _step_counts(self) -> tuple[int, int]:
        total = len(self.agent_status)
        completed = sum(1 for status in self.agent_status.values() if status == "completed")
        return completed, total

    def _set_status(self, agent: str, status: str) -> None:
        if agent in self.agent_status:
            self.agent_status[agent] = status

    def _process_messages(self, chunk: dict[str, Any]) -> None:
        for message in chunk.get("messages", []):
            msg_id = getattr(message, "id", None)
            if msg_id is not None:
                if msg_id in self._processed_message_ids:
                    continue
                self._processed_message_ids.add(msg_id)

            content = getattr(message, "content", None)
            text = _extract_text(content)
            if text:
                self.message_count += 1
                preview = text.replace("\n", " ").strip()
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                self.last_activity = preview

            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                self.tool_call_count += 1
                if isinstance(tool_call, dict):
                    name = tool_call.get("name", "tool")
                else:
                    name = getattr(tool_call, "name", "tool")
                self.last_activity = f"调用工具: {name}"

    def _update_analyst_statuses(self, chunk: dict[str, Any]) -> None:
        if self._wall_time_tracker is not None:
            sync_analyst_tracker_from_chunk(self._wall_time_tracker, chunk)

        selected = [a.lower() for a in self.selected_analysts]
        found_active = False

        for analyst_key in ANALYST_ORDER:
            if analyst_key not in selected:
                continue

            agent_name = ANALYST_AGENT_NAMES[analyst_key]
            report_key = ANALYST_REPORT_MAP[analyst_key]

            if chunk.get(report_key):
                self.report_sections[report_key] = chunk[report_key]

            has_report = bool(self.report_sections.get(report_key))
            if has_report:
                self._set_status(agent_name, "completed")
            elif not found_active:
                self._set_status(agent_name, "in_progress")
                found_active = True
            else:
                self._set_status(agent_name, "pending")

        if not found_active and selected:
            if self.agent_status.get("Bull Researcher") == "pending":
                self._set_status("Bull Researcher", "in_progress")

    def _process_investment_debate(self, chunk: dict[str, Any]) -> None:
        debate_state = chunk.get("investment_debate_state")
        if not debate_state:
            return

        bull_hist = (debate_state.get("bull_history") or "").strip()
        bear_hist = (debate_state.get("bear_history") or "").strip()
        judge = (debate_state.get("judge_decision") or "").strip()

        if bull_hist or bear_hist:
            for agent in RESEARCH_TEAM:
                if self.agent_status.get(agent) != "completed":
                    self._set_status(agent, "in_progress")
            self.last_activity = "研究团队辩论中"

        if judge:
            for agent in RESEARCH_TEAM:
                self._set_status(agent, "completed")
            self._set_status("Trader", "in_progress")
            self.last_activity = "研究经理已完成决策"

    def _process_trader(self, chunk: dict[str, Any]) -> None:
        if not chunk.get("trader_investment_plan"):
            return
        self.report_sections["trader_investment_plan"] = chunk["trader_investment_plan"]
        if self.agent_status.get("Trader") != "completed":
            self._set_status("Trader", "completed")
            self._set_status("Aggressive Analyst", "in_progress")
            self.last_activity = "交易团队制定计划中"

    def _process_risk_debate(self, chunk: dict[str, Any]) -> None:
        risk_state = chunk.get("risk_debate_state")
        if not risk_state:
            return

        agg_hist = (risk_state.get("aggressive_history") or "").strip()
        con_hist = (risk_state.get("conservative_history") or "").strip()
        neu_hist = (risk_state.get("neutral_history") or "").strip()
        judge = (risk_state.get("judge_decision") or "").strip()

        if agg_hist:
            self._set_status("Aggressive Analyst", "in_progress")
            self.last_activity = "激进分析师评估风险"
        if con_hist:
            self._set_status("Conservative Analyst", "in_progress")
            self.last_activity = "保守分析师评估风险"
        if neu_hist:
            self._set_status("Neutral Analyst", "in_progress")
            self.last_activity = "中性分析师评估风险"
        if judge:
            for agent in RISK_TEAM:
                self._set_status(agent, "completed")
            self.last_activity = "投资组合经理做出最终决策"


def format_progress_markdown(progress: AnalysisProgress) -> str:
    minutes, seconds = divmod(int(progress.elapsed_seconds), 60)
    elapsed = f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"

    lines = [
        f"**已运行** {elapsed} · **进度** {progress.completed_steps}/{progress.total_steps}",
        f"**当前步骤** {progress.current_step}",
        f"**最近动态** {progress.last_activity}",
        "",
        "| 智能体 | 状态 |",
        "| --- | --- |",
    ]
    for agent, status in progress.agent_status.items():
        icon = STATUS_ICONS.get(status, "⏳")
        label = {"in_progress": "进行中", "completed": "已完成", "pending": "等待中"}.get(
            status, status
        )
        lines.append(f"| {agent} | {icon} {label} |")

    if progress.tool_call_count:
        lines.append("")
        lines.append(f"_已执行 {progress.tool_call_count} 次工具调用_")
    return "\n".join(lines)


def _extract_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if isinstance(content, dict):
        text = (content.get("text") or "").strip()
        return text or None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = (item.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        return " ".join(parts) if parts else None
    text = str(content).strip()
    return text or None
