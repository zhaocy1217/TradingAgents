from __future__ import annotations

import threading
import time
from datetime import date

import streamlit as st

from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.ui.chat_runner import ChatInputError, ChatRunRequest, run_chat_analysis
from tradingagents.ui.progress import AnalysisProgress, format_progress_markdown

PROVIDERS = [
    "openai",
    "anthropic",
    "google",
    "xai",
    "deepseek",
    "qwen",
    "qwen-cn",
    "glm",
    "glm-cn",
    "minimax",
    "minimax-cn",
    "ollama",
]

ANALYST_OPTIONS = {
    "Market Analyst": "market",
    "Sentiment Analyst": "social",
    "News Analyst": "news",
    "Fundamentals Analyst": "fundamentals",
}

HEARTBEAT_INTERVAL_SECONDS = 2.0


def _model_picker(provider: str, mode: str, key_prefix: str) -> str:
    if provider == "openrouter":
        return st.text_input(
            f"{mode.title()} 模型 ID",
            value="",
            key=f"{key_prefix}_{mode}_model_openrouter",
        ).strip()

    options = get_model_options(provider, mode)
    labels = [label for label, _ in options]
    value_by_label = {label: value for label, value in options}

    selected_label = st.selectbox(
        f"{mode.title()} 模型",
        labels,
        index=0,
        key=f"{key_prefix}_{mode}_model_select",
    )
    selected_value = value_by_label[selected_label]
    if selected_value == "custom":
        return st.text_input(
            f"自定义 {mode} 模型 ID",
            value="",
            key=f"{key_prefix}_{mode}_model_custom",
        ).strip()
    return selected_value


def _render_sidebar() -> dict:
    st.sidebar.header("TradingAgents 参数设置")

    ticker = st.sidebar.text_input("股票/资产代码", value="SPY").strip().upper()
    analysis_date = st.sidebar.date_input("分析日期", value=date.today())
    provider = st.sidebar.selectbox("LLM 提供商", PROVIDERS, index=4)

    quick_model = _model_picker(provider, "quick", "sidebar")
    deep_model = _model_picker(provider, "deep", "sidebar")

    research_depth_label = st.sidebar.selectbox(
        "研究深度",
        ("浅度 (1)", "中等 (3)", "深度 (5)"),
        index=0,
    )
    research_depth = {"浅度 (1)": 1, "中等 (3)": 3, "深度 (5)": 5}[research_depth_label]

    output_language = st.sidebar.selectbox(
        "输出语言",
        ("English", "Chinese", "Japanese", "Korean"),
        index=1,
    )
    backend_url = st.sidebar.text_input(
        "后端 URL（可选）",
        value="",
        help="留空则使用该提供商的默认地址。",
    ).strip()

    analyst_labels = list(ANALYST_OPTIONS.keys())
    default_labels = analyst_labels[:]
    if ticker.endswith(("-USD", "-USDT", "-USDC", "-BTC", "-ETH")):
        default_labels = [label for label in analyst_labels if label != "Fundamentals Analyst"]

    selected_labels = st.sidebar.multiselect(
        "分析师团队",
        analyst_labels,
        default=default_labels,
    )

    selected_analysts = [ANALYST_OPTIONS[label] for label in selected_labels]
    return {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "llm_provider": provider,
        "quick_think_llm": quick_model,
        "deep_think_llm": deep_model,
        "research_depth": research_depth,
        "output_language": output_language,
        "backend_url": backend_url,
        "analysts": selected_analysts,
    }


def _ensure_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "请告诉我你想分析什么。我会使用左侧参数运行 TradingAgents。",
            }
        ]


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _render_live_progress(progress_placeholder, progress: AnalysisProgress) -> None:
    ratio = 0.0
    if progress.total_steps:
        ratio = progress.completed_steps / progress.total_steps
    progress_placeholder.progress(
        ratio,
        text=(
            f"{progress.current_step} · 已运行 {_format_elapsed(progress.elapsed_seconds)}"
        ),
    )
    progress_placeholder.markdown(format_progress_markdown(progress))


def _run_analysis_with_live_progress(
    progress_placeholder,
    request: ChatRunRequest,
):
    latest: dict[str, AnalysisProgress | float] = {
        "progress": AnalysisProgress(
            elapsed_seconds=0.0,
            current_step="准备中",
            last_activity="正在连接分析引擎…",
            agent_status={},
            completed_steps=0,
            total_steps=1,
            tool_call_count=0,
            message_count=0,
        ),
        "started_at": time.monotonic(),
    }
    stop_event = threading.Event()

    def on_progress(progress: AnalysisProgress) -> None:
        latest["progress"] = progress
        _render_live_progress(progress_placeholder, progress)

    def heartbeat() -> None:
        while not stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
            elapsed = time.monotonic() - latest["started_at"]
            progress = latest["progress"]
            heartbeat_progress = AnalysisProgress(
                elapsed_seconds=elapsed,
                current_step=progress.current_step,
                last_activity=(
                    f"{progress.last_activity}（后台仍在运行，请稍候）"
                ),
                agent_status=progress.agent_status,
                completed_steps=progress.completed_steps,
                total_steps=progress.total_steps,
                tool_call_count=progress.tool_call_count,
                message_count=progress.message_count,
            )
            _render_live_progress(progress_placeholder, heartbeat_progress)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        return run_chat_analysis(request, on_progress=on_progress)
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=0.5)


def main() -> None:
    st.set_page_config(page_title="TradingAgents 聊天界面", page_icon=":bar_chart:")
    st.title("TradingAgents Streamlit 聊天界面")
    st.caption("使用聊天方式发起并查看 TradingAgents 分析结果。")

    settings = _render_sidebar()
    _ensure_state()

    clear = st.sidebar.button("清空聊天记录")
    if clear:
        st.session_state.messages = []
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("请输入你的分析需求")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            progress_placeholder = st.empty()
            with st.status("正在运行 TradingAgents 分析…", expanded=True) as status:
                st.caption(
                    "完整分析通常需要数分钟。下方会实时显示当前步骤与耗时；"
                    "若步骤长时间不变，通常表示正在等待 LLM 或数据接口响应，并非后台挂掉。"
                )
                request = ChatRunRequest(prompt=prompt, **settings)
                result = _run_analysis_with_live_progress(progress_placeholder, request)
                status.update(label="分析完成", state="complete", expanded=False)

            response = (
                f"已完成 `{result.ticker}` 在 `{result.analysis_date}` 的分析。\n\n"
                f"**最终决策**\n\n{result.decision}"
            )
            st.markdown(response)
            with st.expander("查看详细交易决策"):
                st.markdown(result.final_trade_decision or "_未返回详细交易决策内容。_")
        except ChatInputError as exc:
            response = f"输入错误：{exc}"
            st.error(response)
        except Exception as exc:  # pragma: no cover
            response = f"运行失败：{exc}"
            st.error(response)

    st.session_state.messages.append({"role": "assistant", "content": response})


if __name__ == "__main__":
    main()
